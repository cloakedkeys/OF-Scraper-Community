"""Check the library against the database, offline; a preview, nothing changes.

For every media the database marks downloaded, it looks for the recorded file
and compares its size with the recorded one. It works on user_data.db files
(a file, or a folder such as a profile's .data that is searched for them), or
with --postgres on the rows of the creator --creator names in the PostgreSQL
database OFSC_DATABASE_URL names. No login, and the scraper's settings
aren't read.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import ofscraper.db.creator_rows as creator_rows
import ofscraper.db.postgres as postgres
import ofscraper.sites as sites
from ofscraper.commands.stash_sync import map_path

MISSING, EMPTY, SIZE_DIFFERS, NO_FILE_RECORDED, OK = (
    "missing", "empty", "size_differs", "no_file_recorded", "ok"
)
# a media with several files is as bad as its worst one
WORST_FIRST = (MISSING, EMPTY, SIZE_DIFFERS, OK)
# areas whose items go away: a reset can't bring them back
GONE_AREAS = {"stories", "highlights"}
# profile pictures and headers are also blocked by a cache entry this can't clear
PROFILE_AREAS = {"profile", "avatar", "header"}
MEDIA_COLUMNS = ["media_id", "post_id", "directory", "filename", "size", "downloaded", "api_type"]
OPTIONAL = ["skip_permanent", "unlocked"]


def _optional(rows, table, column, fallback="NULL"):
    return column if column in rows.columns(table) else f"{fallback}"


def _deleted_posts(rows):
    if "is_deleted" not in rows.columns("posts"):
        return set()
    return {str(r[0]) for r in rows.select("posts", ["post_id"], "is_deleted = TRUE")}


def _full_path(directory, filename, path_root, mappings):
    path = os.path.join(directory or "", filename)
    path = map_path(path, mappings) if mappings else path
    if not os.path.isabs(path):
        if path_root is None:
            raise ValueError("Relative recorded paths require --path-root (the original working directory)")
        path = os.path.join(path_root, path)
    return os.path.abspath(path)


def _check(path, recorded_size):
    try:
        actual = os.stat(path).st_size
    except FileNotFoundError:
        return MISSING, None
    except NotADirectoryError:
        return MISSING, None
    if actual == 0:
        return EMPTY, 0
    if recorded_size not in (None, 0) and int(recorded_size) != actual:
        return SIZE_DIFFERS, actual
    return OK, actual


def _not_recoverable(api_type, post_id, unlocked, deleted_posts):
    """Why a reset wouldn't bring this file back, or None."""
    area = (api_type or "").lower()
    if area in PROFILE_AREAS:
        return "profile picture or header: a cache entry also blocks it, which this can't clear"
    if area in GONE_AREAS:
        return f"{area} expire"
    if str(post_id) in deleted_posts:
        return "the post was deleted"
    if unlocked == 0:
        return "the item is locked"
    return None


def check_rows(rows, path_root=None, mappings=(), sample=20):
    """The findings for one creator's rows."""
    if "medias" not in rows.tables():
        return {"database": rows.name, "skipped": "no medias table"}
    columns = MEDIA_COLUMNS + [_optional(rows, "medias", c) for c in OPTIONAL]
    media = rows.select("medias", columns, "downloaded = TRUE AND media_id IS NOT NULL")
    deleted = _deleted_posts(rows)
    by_media = defaultdict(list)
    for row in media:
        by_media[str(row[0])].append(row)

    counts = Counter()
    findings = []
    folders = set()
    for media_id, group in by_media.items():
        if all(not (r[3] or "").strip() for r in group):
            counts[NO_FILE_RECORDED] += 1
            if any(r[7] for r in group):
                counts["skip_permanent"] += 1
            continue
        results = {}
        for _, post_id, directory, filename, size, _, api_type, _, unlocked in group:
            if not (filename or "").strip():
                continue
            path = _full_path(directory, filename, path_root, mappings)
            folders.add(os.path.dirname(path))
            if path not in results:
                results[path] = (_check(path, size), size, post_id, api_type, unlocked)
        path, ((finding, actual), size, post_id, api_type, unlocked) = min(
            results.items(), key=lambda item: WORST_FIRST.index(item[1][0][0])
        )
        counts[finding] += 1
        if finding == OK:
            continue
        why = _not_recoverable(api_type, post_id, unlocked, deleted)
        if why:
            counts["not_recoverable"] += 1
        if len(findings) < sample:
            entry = {"finding": finding, "media_id": media_id, "post_id": str(post_id),
                     "area": api_type, "path": path, "recorded_size": size, "size": actual}
            if why:
                entry["not_recoverable"] = why
            findings.append(entry)
    return {
        "database": rows.name,
        "site": rows.site,
        "creator": getattr(rows, "creator", None),
        "media_checked": sum(counts[k] for k in (MISSING, EMPTY, SIZE_DIFFERS, OK, NO_FILE_RECORDED)),
        **{k: counts[k] for k in (MISSING, EMPTY, SIZE_DIFFERS, NO_FILE_RECORDED, OK)},
        "not_recoverable": counts["not_recoverable"],
        "skip_permanent": counts["skip_permanent"],
        "folders_missing": sorted(f for f in folders if not os.path.isdir(f))[:sample],
        "findings": findings,
    }


def verify(databases, path_root=None, mappings=(), sample=20):
    """Check every creator in these files or folders."""
    report = {"applied": False, "creators": [], "totals": Counter()}
    for rows in creator_rows.files(databases):
        with closing(rows):
            result = check_rows(rows, path_root, mappings, sample)
        _add(report, result)
    return _finish(report)


def verify_postgres(database_url, creator, site=None, path_root=None, mappings=(), sample=20):
    wanted_sites = [site] if site else list(sites.SITES)
    report = {"applied": False, "creators": [], "totals": Counter()}
    try:
        with creator_rows.in_postgres(database_url, wanted_sites, [creator]) as found:
            for rows in found:
                _add(report, check_rows(rows, path_root, mappings, sample))
    except creator_rows.NotFound:
        raise creator_rows.NotFound(
            f"PostgreSQL has no creator named {creator} (looked in: {', '.join(wanted_sites)})"
        ) from None
    return _finish(report)


def _add(report, result):
    report["creators"].append(result)
    for key in (MISSING, EMPTY, SIZE_DIFFERS, NO_FILE_RECORDED, OK, "not_recoverable", "media_checked"):
        report["totals"][key] += result.get(key, 0)


def _finish(report):
    report["totals"] = dict(report["totals"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ofscraper verify", description=__doc__)
    parser.add_argument("databases", type=Path, nargs="*", help="user_data.db files, or folders (e.g. a profile's .data) to search for them")
    parser.add_argument("--path-map", action="append", default=[], metavar="RECORDED=HERE", help="read a recorded path prefix as another, e.g. a library recorded in Docker and checked from the host; repeatable")
    parser.add_argument("--path-root", type=Path, help="Original working directory for relative recorded paths")
    parser.add_argument("--sample", type=int, default=20, metavar="N", help="list up to N findings per creator (default 20)")
    parser.add_argument("--postgres", action="store_true", help=f"Check the PostgreSQL database ${postgres.URL_KEY} names instead of user_data.db files; give --creator and no database with it")
    parser.add_argument("--site", choices=list(sites.SITES), help="With --postgres, the creator's site (default: the site that has the creator)")
    parser.add_argument("--creator", metavar="NAME", help="With --postgres, the creator to check, by username or id")
    args = parser.parse_args(argv)

    mappings = []
    for item in args.path_map:
        if "=" not in item:
            parser.error(f"--path-map needs RECORDED=HERE, got {item!r}")
        mappings.append(tuple(item.split("=", 1)))

    database_url = os.environ.get(postgres.URL_KEY, "").strip()
    if args.postgres:
        if args.databases:
            parser.error("give user_data.db files or --postgres, not both")
        if not database_url:
            parser.error(f"--postgres needs {postgres.URL_KEY} set in the environment this is run in; see POSTGRESQL.md")
        if not args.creator:
            parser.error("--postgres needs --creator: the creator to check, by username or id")
    elif args.site is not None or args.creator is not None:
        parser.error("--site and --creator go with --postgres")
    elif not args.databases:
        parser.error("give user_data.db files or folders, or --postgres and --creator")

    try:
        if args.postgres:
            result = verify_postgres(database_url, args.creator, args.site, args.path_root, mappings, args.sample)
        else:
            result = verify(args.databases, args.path_root, mappings, args.sample)
    except KeyboardInterrupt:
        parser.exit(130, "Stopped\n")
    except (OSError, sqlite3.Error, ValueError, creator_rows.NotFound, postgres.PostgresUnavailable) as error:
        parser.exit(1, f"Verify failed: {error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

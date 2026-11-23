"""Check the library against the database, offline; preview unless --apply.

For every media the database marks downloaded, it looks for the recorded file
and compares its size with the recorded one. It works on user_data.db files
(a file, or a folder such as a profile's .data that is searched for them), or
with --postgres on the rows of the creator --creator names in the PostgreSQL
database OFSC_DATABASE_URL names. No login, and the scraper's settings
aren't read. --apply marks what's missing or empty as not downloaded, so the
next normal run downloads it again. See VERIFY.md.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import ofscraper.db.creator_rows as creator_rows
import ofscraper.db.postgres as postgres
import ofscraper.sites as sites
from ofscraper.commands.recover_collisions import (
    MEANWHILE,
    _remove_unused,
    _undo_folder,
    _unconfirmed,
)
from ofscraper.commands.stash_sync import map_path

MISSING, EMPTY, SIZE_DIFFERS, NO_FILE_RECORDED, OK = (
    "missing", "empty", "size_differs", "no_file_recorded", "ok"
)
FINDINGS = (MISSING, EMPTY, SIZE_DIFFERS, NO_FILE_RECORDED, OK)
# a media with several files is as bad as its worst one
WORST_FIRST = (MISSING, EMPTY, SIZE_DIFFERS, OK)
# areas whose items go away: a reset can't bring them back
GONE_AREAS = {"stories", "highlights"}
# profile pictures and headers are also blocked by a cache entry this can't clear
PROFILE_AREAS = {"profile", "avatar", "header"}
MEDIA_COLUMNS = ["media_id", "post_id", "directory", "filename", "size", "downloaded", "api_type"]
OPTIONAL = ["skip_permanent", "unlocked"]
# what an undo file holds of each PostgreSQL row; the last two say the row
# is still the one that was reset
PG_ROW = ["media_id", "post_id", "directory", "filename"]
SQLITE, POSTGRESQL = "sqlite", "postgresql"
BAD = ".bad"
MOSTLY_MISSING = 0.5


def _optional(rows, table, column):
    return column if column in rows.columns(table) else "NULL"


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
    except (FileNotFoundError, NotADirectoryError):
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


def scan(rows, path_root=None, mappings=()):
    """Every media marked downloaded, with its finding: {media_id: {...}}.
    Each has "finding", "paths" ({path: (finding, size on disk)}) and, for a
    finding other than ok, what the report shows of it."""
    columns = MEDIA_COLUMNS + [_optional(rows, "medias", c) for c in OPTIONAL]
    media = rows.select("medias", columns, "downloaded = TRUE AND media_id IS NOT NULL")
    deleted = _deleted_posts(rows)
    by_media = defaultdict(list)
    for row in media:
        by_media[str(row[0])].append(row)

    found = {}
    for media_id, group in by_media.items():
        if all(not (r[3] or "").strip() for r in group):
            found[media_id] = {"finding": NO_FILE_RECORDED, "paths": {},
                               "skip_permanent": any(r[7] for r in group)}
            continue
        paths = {}
        first = {}
        for _, post_id, directory, filename, size, _, api_type, _, unlocked in group:
            if not (filename or "").strip():
                continue
            path = _full_path(directory, filename, path_root, mappings)
            if path not in paths:
                paths[path] = _check(path, size)
                first[path] = (size, post_id, api_type, unlocked)
        path = min(paths, key=lambda p: WORST_FIRST.index(paths[p][0]))
        finding, actual = paths[path]
        size, post_id, api_type, unlocked = first[path]
        entry = {"finding": finding, "paths": paths}
        if finding != OK:
            entry["shown"] = {"finding": finding, "media_id": media_id, "post_id": str(post_id),
                              "area": api_type, "path": path, "recorded_size": size, "size": actual}
            why = _not_recoverable(api_type, post_id, unlocked, deleted)
            if why:
                entry["shown"]["not_recoverable"] = why
                entry["not_recoverable"] = why
        found[media_id] = entry
    return found


def _guard(found):
    """Why --apply should leave this creator alone, or None: when most of
    its files are missing, the drive or share is likely not connected."""
    with_file = [e for e in found.values() if e["paths"]]
    missing = sum(1 for e in with_file if e["finding"] == MISSING)
    if not with_file or missing <= MOSTLY_MISSING * len(with_file):
        return None
    folders = {os.path.dirname(p) for e in with_file for p in e["paths"]}
    try:
        top = os.path.commonpath(sorted(folders))
    except ValueError:
        top = None
    if top and not os.path.isdir(top):
        return (f"the creator's folder {top} isn't there: is the drive or share"
                " connected? Give --allow-mostly-missing to reset all the same")
    return (f"{missing} of {len(with_file)} files are missing: is the drive or share"
            " connected? Give --allow-mostly-missing to reset all the same")


def _to_reset(found, reset_size_differs):
    """The media --apply resets, and the files it moves aside as
    [path, new name]."""
    resetting, moving = [], []
    wanted = {MISSING, EMPTY} | ({SIZE_DIFFERS} if reset_size_differs else set())
    for media_id, entry in found.items():
        if entry["finding"] not in wanted or entry.get("not_recoverable"):
            continue
        resetting.append(media_id)
        for path, (finding, _) in entry["paths"].items():
            if finding == EMPTY or (finding == SIZE_DIFFERS and reset_size_differs):
                moving.append(path)
    return resetting, [[path, _bad_name(path)] for path in sorted(moving)]


def _bad_name(path):
    target = f"{path}{BAD}"
    n = 1
    while os.path.lexists(target):
        n += 1
        target = f"{path}.{n}{BAD}"
    return target


def _report(rows, found, sample):
    counts = Counter(e["finding"] for e in found.values())
    shown = [e["shown"] for e in found.values() if "shown" in e]
    folders = {os.path.dirname(p) for e in found.values() for p in e["paths"]}
    return {
        "database": rows.name,
        "site": rows.site,
        "creator": getattr(rows, "creator", None),
        "media_checked": len(found),
        **{k: counts[k] for k in FINDINGS},
        "not_recoverable": sum(1 for e in found.values() if e.get("not_recoverable")),
        "skip_permanent": sum(1 for e in found.values() if e.get("skip_permanent")),
        "folders_missing": sorted(f for f in folders if not os.path.isdir(f))[:sample],
        "findings": shown[:sample],
    }


def _preview(rows, options):
    if "medias" not in rows.tables():
        return {"database": rows.name, "skipped": "no medias table"}
    found = scan(rows, options["path_root"], options["mappings"])
    report = _report(rows, found, options["sample"])
    _plan(report, found, options)
    return report


def _plan(report, found, options):
    """Fill in what --apply would do for this creator; the media and files
    to change, or None when the guard leaves the creator alone."""
    resetting, moving = _to_reset(found, options["reset_size_differs"])
    report["media_to_reset"] = len(resetting)
    report["files_to_move_aside"] = len(moving)
    report["media_reset"] = 0
    report["undo_file"] = None
    guard = None if options["allow_mostly_missing"] else _guard(found)
    if guard:
        report["left_alone"] = guard
        return None
    return resetting, moving


def _move_aside(moving, renames):
    """Rename each file to its .bad name, recording each rename as it's done."""
    for path, target in moving:
        if os.path.lexists(target):
            raise FileExistsError(f"{target} appeared while this ran. Nothing was changed")
        os.rename(path, target)
        renames.append([path, target])


def _move_back(renames):
    """Undo renames that were done: what can't be put back is said."""
    left = []
    for path, target in reversed(renames):
        try:
            if os.path.lexists(target) and not os.path.lexists(path):
                os.rename(target, path)
        except OSError:
            left.append(target)
    return left


def _write_undo(undo_file, record):
    with undo_file.open("x", encoding="utf-8") as output:
        json.dump(record, output)
        output.flush()
        os.fsync(output.fileno())


class _WritableFile(creator_rows.FileRows):
    """A user_data.db opened for changes, locked for this run."""

    def _connection(self):
        if self._conn is None:
            uri = self.path.resolve().as_uri()
            self._conn = sqlite3.connect(f"{uri}?mode=rw", uri=True, isolation_level=None)
            self._conn.execute("BEGIN IMMEDIATE")
        return self._conn


def _apply_file(path, options):
    """verify --apply on one user_data.db: report, undo file, renames and
    reset in one transaction."""
    database = Path(path).resolve(strict=True)
    rows = _WritableFile(database)
    try:
        if "medias" not in rows.tables():
            rows._conn.rollback()
            return {"database": rows.name, "skipped": "no medias table"}
        found = scan(rows, options["path_root"], options["mappings"])
        report = _report(rows, found, options["sample"])
        planned = _plan(report, found, options)
        conn = rows._connection()
        if not planned or not planned[0]:
            conn.rollback()
            return report
        resetting, moving = planned
        changed = []
        for media_id in resetting:
            changed.extend(list(r) for r in conn.execute(
                "SELECT id, model_id, media_id, post_id FROM medias"
                " WHERE media_id = ? AND downloaded = 1", (media_id,)
            ))
        undo_file = database.with_name(f"{database.name}.verify-undo-{uuid.uuid4().hex}.json")
        record = {"version": 1, "source": SQLITE, "database": str(database),
                  "rows": sorted(changed, key=str), "renames": []}
        renames = []
        undo_created = False
        try:
            # the undo file names every rename before it's done
            record["renames"] = moving
            _write_undo(undo_file, record)
            undo_created = True
            _move_aside(moving, renames)
            for row_id, *_ in changed:
                conn.execute("UPDATE medias SET downloaded = 0 WHERE id = ? AND downloaded = 1", (row_id,))
        except BaseException as error:
            conn.rollback()
            left = _move_back(renames)
            if undo_created and not left:
                _remove_unused(undo_file, error)
            raise
        try:
            conn.commit()
        except BaseException as error:
            raise _unconfirmed(
                error,
                f"The undo file is kept: {undo_file}. Run this with --undo and that"
                " file, without --apply, to see whether the changes arrived.",
                saved_to="the database file",
            ) from error
        report["undo_file"] = str(undo_file)
        report["media_reset"] = len(resetting)
        report["files_moved_aside"] = len(renames)
        return report
    finally:
        rows.close()


def verify(databases, path_root=None, mappings=(), sample=20, apply=False,
           reset_size_differs=False, allow_mostly_missing=False):
    """Check every creator in these files or folders; with apply, reset."""
    options = {"path_root": path_root, "mappings": mappings, "sample": sample,
               "reset_size_differs": reset_size_differs,
               "allow_mostly_missing": allow_mostly_missing}
    report = {"applied": apply, "creators": [], "totals": Counter()}
    for rows in creator_rows.files(databases):
        if apply:
            result = _apply_file(rows.path, options)
        else:
            with closing(rows):
                result = _preview(rows, options)
        _add(report, result)
    return _finish(report)


def _apply_postgres(rows, options, undo_dir, database):
    found = scan(rows, options["path_root"], options["mappings"])
    report = _report(rows, found, options["sample"])
    planned = _plan(report, found, options)
    if not planned or not planned[0]:
        return report
    resetting, moving = planned
    wanted = set(resetting)
    columns = ["id", *PG_ROW]
    marking = [tuple(r) for r in rows.select("medias", columns, "downloaded = TRUE AND media_id IS NOT NULL")
               if str(r[1]) in wanted]
    undo_file = Path(undo_dir) / (
        f"postgresql-{rows.site}-{rows.model_id}.verify-undo-{uuid.uuid4().hex}.json"
    )
    record = {"version": 2, "source": POSTGRESQL, "site": rows.site, "model_id": rows.model_id,
              "rows": [dict(zip(PG_ROW, r[1:])) for r in marking], "renames": []}
    if database:
        record["database"] = database
    renames = []
    undo_created = False
    try:
        record["renames"] = moving
        _write_undo(undo_file, record)
        undo_created = True
        _move_aside(moving, renames)
        for row in marking:
            was = {"downloaded": 1, "directory": row[3], "filename": row[4]}
            if not rows.change("medias", row[0], was, {"downloaded": 0}):
                raise postgres.ChangedMeanwhile(MEANWHILE)
    except BaseException as error:
        left = _move_back(renames)
        if undo_created and not left:
            _remove_unused(undo_file, error)
        raise
    try:
        rows.commit()
    except BaseException as error:
        raise _unconfirmed(
            error,
            f"The undo file is kept: {undo_file}. Run this with --undo and that"
            " file, without --apply, to see whether the changes arrived.",
        ) from error
    report["undo_file"] = str(undo_file)
    report["media_reset"] = len(resetting)
    report["files_moved_aside"] = len(renames)
    return report


def _database_name(database_url):
    database = postgres.describe(database_url)
    return None if database == postgres.describe("") else database


def verify_postgres(database_url, creator, site=None, path_root=None, mappings=(), sample=20,
                    apply=False, undo_dir=None, reset_size_differs=False,
                    allow_mostly_missing=False):
    options = {"path_root": path_root, "mappings": mappings, "sample": sample,
               "reset_size_differs": reset_size_differs,
               "allow_mostly_missing": allow_mostly_missing}
    if undo_dir is not None:
        undo_dir = _undo_folder(undo_dir)
    elif apply:
        raise ValueError("A folder for the undo file is needed to apply")
    wanted_sites = [site] if site else list(sites.SITES)
    report = {"applied": apply, "creators": [], "totals": Counter()}
    try:
        with creator_rows.in_postgres(database_url, wanted_sites, [creator], write=apply) as found:
            if apply and len(found) != 1:
                raise ValueError(
                    f"PostgreSQL has more than one creator named {creator}."
                    " Give --site, or a username or id that only one of them has"
                )
            for rows in found:
                if apply:
                    result = _apply_postgres(rows, options, undo_dir, _database_name(database_url))
                else:
                    result = _preview(rows, options)
                _add(report, result)
    except creator_rows.NotFound:
        raise creator_rows.NotFound(
            f"PostgreSQL has no creator named {creator} (looked in: {', '.join(wanted_sites)})"
        ) from None
    return _finish(report)


# --- undo ---

def _read_undo(undo_file):
    record = json.loads(Path(undo_file).read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("source") not in (SQLITE, POSTGRESQL):
        raise ValueError("This isn't an undo file written by verify")
    renames = record.get("renames")
    if not isinstance(renames, list) or any(
        not isinstance(r, list) or len(r) != 2 or not all(isinstance(x, str) for x in r)
        for r in renames
    ):
        raise ValueError("Invalid renames in the undo file")
    return record


def _renames_to_undo(renames):
    """The renames still to be put back: the undo file lists each before
    it's done, so one that wasn't done, or was put back, is passed over."""
    return [[path, target] for path, target in renames
            if os.path.lexists(target) and not os.path.lexists(path)]


def undo(undo_file, apply=False):
    """Put back what a --apply on a user_data.db changed: rows reset since
    are marked downloaded again, and files moved aside get their names back."""
    record = _read_undo(undo_file)
    if record["source"] != SQLITE:
        raise ValueError("Undo file was written from PostgreSQL: give --postgres and --creator with it")
    if record.get("version") != 1:
        raise ValueError("Undo file was written by a version of this command that this one doesn't know")
    rows = record.get("rows")
    if not isinstance(rows, list) or any(not isinstance(r, list) or len(r) != 4 for r in rows):
        raise ValueError("Invalid undo rows")
    database = Path(record["database"]).resolve(strict=True)
    renames = _renames_to_undo(record["renames"])
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(f"{database.as_uri()}?mode={mode}", uri=True)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            condition = "id = ? AND model_id IS ? AND media_id = ? AND post_id = ? AND downloaded = 0"
            if apply:
                count = sum(conn.execute(f"UPDATE medias SET downloaded = 1 WHERE {condition}", r).rowcount
                            for r in rows)
                for path, target in renames:
                    os.rename(target, path)
            else:
                count = sum(conn.execute(f"SELECT COUNT(*) FROM medias WHERE {condition}", r).fetchone()[0]
                            for r in rows)
    return {"applied": apply, "rows_to_restore": count, "files_to_rename_back": len(renames)}


def undo_postgres(database_url, creator, undo_file, site=None, apply=False):
    record = _read_undo(undo_file)
    if record["source"] != POSTGRESQL:
        raise ValueError("Undo file was written for a user_data.db: give it without --postgres")
    if record.get("version") != 2:
        raise ValueError("Undo file was written by a version of this command that this one doesn't know")
    wanted = record.get("rows")
    if not isinstance(wanted, list) or any(not isinstance(r, dict) or set(r) != set(PG_ROW) for r in wanted):
        raise ValueError("Invalid undo rows")
    wanted_sites = [site] if site else list(sites.SITES)
    with creator_rows.in_postgres(database_url, wanted_sites, [creator], write=apply) as found:
        if len(found) != 1:
            raise ValueError(f"PostgreSQL has more than one creator named {creator}: give --site")
        (rows,) = found
        if (record.get("site"), str(record.get("model_id"))) != (rows.site, str(rows.model_id)):
            raise ValueError(
                f"Undo file is for {record.get('site')} creator {record.get('model_id')},"
                f" not {rows.site} creator {rows.model_id}"
            )
        current = {(str(r[1]), str(r[2])): tuple(r)
                   for r in rows.select("medias", ["id", *PG_ROW, "downloaded"], "media_id IS NOT NULL")}
        restoring = []
        for row in wanted:
            now = current.get((str(row["media_id"]), str(row["post_id"])))
            if now and now[5] == 0 and (now[3], now[4]) == (row["directory"], row["filename"]):
                restoring.append(now)
        renames = _renames_to_undo(record["renames"])
        if apply:
            for now in restoring:
                was = {"downloaded": 0, "directory": now[3], "filename": now[4]}
                if not rows.change("medias", now[0], was, {"downloaded": 1}):
                    raise postgres.ChangedMeanwhile(MEANWHILE)
            for path, target in renames:
                os.rename(target, path)
            try:
                rows.commit()
            except BaseException as error:
                raise _unconfirmed(
                    error,
                    "Run the same --undo again, without --apply: it shows what is left to restore.",
                ) from error
    return {"applied": apply, "rows_to_restore": len(restoring), "files_to_rename_back": len(renames)}


def _add(report, result):
    report["creators"].append(result)
    for key in (*FINDINGS, "not_recoverable", "media_checked", "media_to_reset", "media_reset"):
        if key in result:
            report["totals"][key] += result[key]
    if result.get("left_alone"):
        report["totals"]["creators_left_alone"] += 1


def _finish(report):
    report["totals"] = dict(report["totals"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ofscraper verify", description=__doc__)
    parser.add_argument("databases", type=Path, nargs="*", help="user_data.db files, or folders (e.g. a profile's .data) to search for them")
    parser.add_argument("--apply", action="store_true", help="Mark missing and empty files as not downloaded, so the next run downloads them again; writes an undo file first")
    parser.add_argument("--reset-size-differs", action="store_true", help="With --apply, also reset files whose size isn't the recorded one (moved aside with .bad added)")
    parser.add_argument("--allow-mostly-missing", action="store_true", help="With --apply, reset a creator even when most of its files are missing (otherwise left alone: the drive may not be connected)")
    parser.add_argument("--undo", metavar="FILE", help="Preview putting back what an --apply changed, from its undo file; with --apply, put it back")
    parser.add_argument("--path-map", action="append", default=[], metavar="RECORDED=HERE", help="read a recorded path prefix as another, e.g. a library recorded in Docker and checked from the host; repeatable")
    parser.add_argument("--path-root", type=Path, help="Original working directory for relative recorded paths")
    parser.add_argument("--sample", type=int, default=20, metavar="N", help="list up to N findings per creator (default 20)")
    parser.add_argument("--postgres", action="store_true", help=f"Check the PostgreSQL database ${postgres.URL_KEY} names instead of user_data.db files; give --creator and no database with it")
    parser.add_argument("--site", choices=list(sites.SITES), help="With --postgres, the creator's site (default: the site that has the creator)")
    parser.add_argument("--creator", metavar="NAME", help="With --postgres, the creator to check, by username or id")
    parser.add_argument("--undo-dir", metavar="DIR", help="With --postgres --apply, the folder for the undo file: a folder that is kept (in Docker, a mounted one)")
    args = parser.parse_args(argv)

    mappings = []
    for item in args.path_map:
        if "=" not in item:
            parser.error(f"--path-map needs RECORDED=HERE, got {item!r}")
        mappings.append(tuple(item.split("=", 1)))
    if args.undo is not None and not args.undo.strip():
        parser.error("--undo needs the path of an undo file")
    if args.undo is not None and (args.reset_size_differs or args.allow_mostly_missing or args.undo_dir):
        parser.error("--undo puts back an earlier --apply; it takes no other --apply options")

    database_url = os.environ.get(postgres.URL_KEY, "").strip()
    if args.postgres:
        if args.databases:
            parser.error("give user_data.db files or --postgres, not both")
        if not database_url:
            parser.error(f"--postgres needs {postgres.URL_KEY} set in the environment this is run in; see POSTGRESQL.md")
        if not args.creator:
            parser.error("--postgres needs --creator: the creator to check, by username or id")
        if args.apply and args.undo is None and not (args.undo_dir or "").strip():
            parser.error(
                "--postgres --apply needs --undo-dir: the folder for the undo file, which is"
                " the only way back. Give a folder that is kept (in Docker a mounted one)"
            )
    elif args.site is not None or args.creator is not None or args.undo_dir is not None:
        parser.error("--site, --creator and --undo-dir go with --postgres")
    elif args.undo is not None:
        if args.databases:
            parser.error("--undo takes the database from the undo file; give no database with it")
    elif not args.databases:
        parser.error("give user_data.db files or folders, or --postgres and --creator")

    try:
        if args.undo is not None and args.postgres:
            result = undo_postgres(database_url, args.creator, args.undo, args.site, args.apply)
        elif args.undo is not None:
            result = undo(args.undo, args.apply)
        elif args.postgres:
            result = verify_postgres(
                database_url, args.creator, args.site, args.path_root, mappings, args.sample,
                apply=args.apply, undo_dir=args.undo_dir,
                reset_size_differs=args.reset_size_differs,
                allow_mostly_missing=args.allow_mostly_missing,
            )
        else:
            result = verify(
                args.databases, args.path_root, mappings, args.sample, apply=args.apply,
                reset_size_differs=args.reset_size_differs,
                allow_mostly_missing=args.allow_mostly_missing,
            )
    except postgres.Unconfirmed as error:
        parser.exit(130 if error.interrupted else 1, f"Verify failed: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Stopped. Nothing was changed.\n")
    except (OSError, sqlite3.Error, ValueError, creator_rows.NotFound, postgres.PostgresUnavailable) as error:
        parser.exit(1, f"Verify failed: {error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

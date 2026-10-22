"""Offline recovery of ambiguous shared download paths; preview unless --apply."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid
from collections import defaultdict
from contextlib import closing


def find_collisions(conn, path_root=None):
    """Group distinct media by their recorded path or existing file identity."""
    groups = defaultdict(list)
    rows = conn.execute(
        "SELECT model_id, media_id, directory, filename, downloaded FROM medias "
        "WHERE media_id IS NOT NULL AND filename IS NOT NULL AND filename != ''"
    )
    for model, media, directory, filename, downloaded in rows:
        path = Path(directory or "", filename)
        if not path.is_absolute():
            if path_root is None:
                raise ValueError("Relative recorded paths require --path-root (the original working directory)")
            path = Path(path_root) / path
        path = Path(os.path.abspath(path))
        try:
            info = path.stat()
        except FileNotFoundError:
            # Preserve case when there is no file to establish filesystem identity.
            key = ("path", str(path))
        else:
            if not stat.S_ISREG(info.st_mode):
                continue
            key = ("file", info.st_dev, info.st_ino) if info.st_ino else ("path", str(path))
        groups[key].append((model, media, str(path), downloaded))
    return [
        rows for rows in groups.values()
        if len({(r[0], r[1]) for r in rows}) > 1
    ]


def recover(database, apply=False, path_root=None):
    """Back up and reset only download flags, in one transaction when applying."""
    database = Path(database).resolve(strict=True)
    uri = database.as_uri()
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(f"{uri}?mode={mode}", uri=True)) as conn:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        try:
            groups = find_collisions(conn, path_root)
            owners = {(r[0], r[1]) for group in groups for r in group}
            pending = [
                owner for owner in owners
                if conn.execute(
                    "SELECT 1 FROM medias WHERE model_id IS ? AND media_id = ? "
                    "AND downloaded = 1 LIMIT 1", owner
                ).fetchone()
            ]
            result = {
                "groups": [
                    {"paths": sorted({r[2] for r in group}),
                     "media": [{"model_id": model, "media_id": media}
                               for model, media in sorted(
                                   {(r[0], r[1]) for r in group}, key=str)]}
                    for group in groups
                ],
                "media_to_mark": len(pending),
                "rows_marked": 0,
                "backup": None,
                "applied": apply,
            }
            if apply and pending:
                backup = database.with_name(
                    f"{database.name}.collision-backup-{uuid.uuid4().hex}.sqlite"
                )
                # The write reservation prevents another writer from changing
                # the snapshot between detection, backup and the flag updates.
                with backup.open("xb"):
                    pass
                with closing(sqlite3.connect(f"{uri}?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(backup)) as destination:
                        source.backup(destination)
                result["backup"] = str(backup)
                for owner in pending:
                    result["rows_marked"] += conn.execute(
                        "UPDATE medias SET downloaded = 0 "
                        "WHERE model_id IS ? AND media_id = ? AND downloaded = 1",
                        owner,
                    ).rowcount
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="One model's user_data.db")
    parser.add_argument("--apply", action="store_true", help="Back up the database and mark ambiguous media for re-download")
    parser.add_argument("--path-root", type=Path, help="Original working directory for relative recorded paths")
    args = parser.parse_args(argv)
    try:
        result = recover(args.database, apply=args.apply, path_root=args.path_root)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Recovery failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

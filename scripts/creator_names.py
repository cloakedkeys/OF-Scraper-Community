r"""List and record the usernames each creator account has had.

OF-Scraper keeps one database per account ({data_dir}/{model_id}/user_data.db). Its
`profiles` table holds one row per (account, username) pair, and OF-Scraper adds a row
whenever it processes a creator under a name it hasn't seen, so a renamed creator ends up
with several rows. This script reads that history back and can record a name you learned
elsewhere (e.g. from an older download tool).

    python scripts/creator_names.py                      # accounts with more than one name
    python scripts/creator_names.py --all                # every account and its names
    python scripts/creator_names.py --find NAME          # account(s) that ever used NAME
    python scripts/creator_names.py --add MODEL_ID NAME  # record another name
    python scripts/creator_names.py --csv names.csv      # also write the listing to CSV

The default data directory is the default `metadata` location for the main profile,
{home_directory}/.config/ofscraper/main_profile/.data; use --data-dir if yours differs.

--add only appends a row and ignores duplicates. It never changes the first recorded
name, which OF-Scraper uses as the folder name for a deleted account. Close OF-Scraper
before using --add.
"""

import argparse
import contextlib
import csv
import pathlib
import sqlite3
import sys

DEFAULT_DATA_DIR = pathlib.Path.home() / ".config" / "ofscraper" / "main_profile" / ".data"


def _connect_ro(db):
    return sqlite3.connect(pathlib.Path(db).resolve().as_uri() + "?mode=ro", uri=True)


def accounts(data_dir):
    """Yield (model_id, names in the order they were recorded, media count) per account."""
    for db in sorted(pathlib.Path(data_dir).glob("*/user_data.db")):
        with contextlib.closing(_connect_ro(db)) as con:
            try:
                names = [r[0] for r in con.execute("SELECT username FROM profiles ORDER BY id")]
                media = con.execute("SELECT count(*) FROM medias").fetchone()[0]
            except sqlite3.OperationalError:
                continue
        yield db.parent.name, names, media


def add_name(data_dir, model_id, name):
    """Record `name` for `model_id`. Returns True if a row was added, False if it existed."""
    db = pathlib.Path(data_dir) / str(model_id) / "user_data.db"
    if not db.exists():
        raise FileNotFoundError(f"no OF-Scraper database for account {model_id}: {db}")
    with contextlib.closing(sqlite3.connect(db)) as con:
        added = con.execute(
            "INSERT OR IGNORE INTO profiles (user_id, username) VALUES (?, ?)",
            (int(model_id), name),
        ).rowcount
        con.commit()
    return bool(added)


def select(data_dir, find=None, show_all=False):
    rows = []
    for model_id, names, media in accounts(data_dir):
        if find is not None:
            if any(n.lower() == find.lower() for n in names):
                rows.append((model_id, names, media))
        elif show_all or len(names) > 1:
            rows.append((model_id, names, media))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data-dir", type=pathlib.Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--all", action="store_true", help="list every account")
    ap.add_argument("--find", metavar="NAME", help="accounts that ever used NAME")
    ap.add_argument("--add", nargs=2, metavar=("MODEL_ID", "NAME"), help="record a name")
    ap.add_argument("--csv", type=pathlib.Path, help="also write the listing to a CSV file")
    args = ap.parse_args(argv)

    if args.add:
        model_id, name = args.add
        try:
            added = add_name(args.data_dir, model_id, name)
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"{'Added' if added else 'Already recorded'}: {name} -> {model_id}")
        return 0

    rows = select(args.data_dir, find=args.find, show_all=args.all)
    # Row order is the order names were recorded, not the order of the renames, so the
    # first recorded name is shown apart from the others rather than as a timeline.
    for model_id, names, media in rows:
        others = f"   also known as: {', '.join(names[1:])}" if len(names) > 1 else ""
        print(f"{model_id:>10}  {media:>6} media  {names[0] if names else '?'}{others}")
    scope = "" if args.all or args.find else " with more than one name"
    print(f"\n{len(rows)} account(s){scope}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["model_id", "media", "first_recorded_name", "other_names"])
            for model_id, names, media in rows:
                w.writerow([model_id, media, names[0] if names else "", " | ".join(names[1:])])
    return 0


if __name__ == "__main__":
    sys.exit(main())

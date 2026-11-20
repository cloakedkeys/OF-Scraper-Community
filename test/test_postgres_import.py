"""Bringing user_data.db files into PostgreSQL (#608): everything the command
does short of talking to a server, which test_postgres_import_live.py does.

The stand-in target keeps what it's given the way the real one would: rows
are kept only once committed.
"""

import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.commands.postgres_import as importer
import ofscraper.db.postgres as postgres
import ofscraper.utils.settings as settings
from ofscraper.db.difference import ALL_FLAGS
from test.import_files import (
    current_file,
    file_2023,
    file_before_newest_columns,
    sql,
)


class Target:
    def __init__(self, held=None):
        self.held = held or {}
        self.kept = {}
        self.pending = {}
        self.prepared = []
        self.opened = []
        self.fail_at = None

    def check(self):
        return "18.0"

    def open(self, read_only):
        self.opened.append(read_only)
        self.pending = {key: list(rows) for key, rows in self.kept.items()}

    def prepare(self, sites, flags):
        self.prepared.append((list(sites), set(flags)))

    def creators(self, site):
        return set(self.held.get(site, ()))

    def count(self, site, table):
        return len(self.pending.get((site, table), []))

    def write(self, site, table, columns, rows):
        if self.fail_at == table:
            raise importer.Refused("the server went away")
        there = self.pending.setdefault((site, table), [])
        new = [dict(zip(columns, row)) for row in rows]
        if table in importer.SHARED_TABLES:
            new = [row for row in new if row not in there]
        there.extend(new)
        return len(new)

    def commit(self):
        self.kept = {key: list(rows) for key, rows in self.pending.items()}

    def close(self):
        self.pending = {}

    def rows(self, site, table):
        return self.kept.get((site, table), [])


def run(folder, target=None, **kwargs):
    lines = []
    code = importer.run(folder, target=target, out=lines.append, **kwargs)
    return code, "\n".join(lines)


def snapshot(folder):
    """Every file under `folder`, with its content's hash."""
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(autouse=True)
def no_url(monkeypatch):
    monkeypatch.delenv(postgres.URL_KEY, raising=False)


# --- values ---


@pytest.mark.parametrize(
    "value, kind, expected",
    [
        (None, "BIGINT", None),
        (5, "BIGINT", 5),
        (b"5", "BIGINT", 5),
        (" 5 ", "BIGINT", 5),
        (5.0, "BIGINT", 5),
        (True, "BOOLEAN", True),
        (2**63 - 1, "BIGINT", 2**63 - 1),
        ("", "BIGINT", None),
        (1, "BOOLEAN", True),
        (0, "BOOLEAN", False),
        (b"1", "BOOLEAN", True),
        ("false", "BOOLEAN", False),
        ("", "BOOLEAN", None),
        (9.99, "NUMERIC", 9.99),
        ("15", "NUMERIC", 15),
        ("9.5", "NUMERIC", 9.5),
        ("", "TIMESTAMPTZ", None),
        ("2024-03-01 09:30:00", "TIMESTAMPTZ", "2024-03-01T09:30:00+00:00"),
        ("2024-03-01T09:30:00+02:00", "TIMESTAMPTZ", "2024-03-01T09:30:00+02:00"),
        (1709285400, "TIMESTAMPTZ", "2024-03-01T09:30:00+00:00"),
        (b"plain", "TEXT", "plain"),
        ("", "TEXT", ""),
        (5, "TEXT", "5"),
    ],
)
def test_a_value_is_converted_for_its_column(value, kind, expected):
    converted = importer.convert(value, kind)
    assert converted == expected
    assert type(converted) is type(expected)


@pytest.mark.parametrize(
    "value, kind, why",
    [
        ("0123456789abcdef0123456789abcdef", "BIGINT", "isn't a whole number"),
        (5.5, "BIGINT", "isn't a whole number"),
        (True, "BIGINT", "isn't a whole number"),
        (2**63, "BIGINT", "too large"),
        (2, "BOOLEAN", "neither yes"),
        ("yes", "BOOLEAN", "neither yes"),
        ("free", "NUMERIC", "isn't a number"),
        (float("nan"), "NUMERIC", "isn't a number"),
        ("not a date", "TIMESTAMPTZ", "isn't a date"),
        (b"\xff\xfe", "TEXT", "isn't valid UTF-8"),
        ("a\x00b", "TEXT", "null character"),
        (5, "MADE_UP", "doesn't know"),
    ],
)
def test_a_value_the_column_cant_hold_is_refused(value, kind, why):
    with pytest.raises(importer.Bad, match=why):
        importer.convert(value, kind)


def test_every_column_type_postgresql_uses_is_converted():
    kinds = {kind for table in postgres.COLUMN_TYPES.values() for kind in table.values()}
    for kind in kinds:
        assert importer.convert(None, kind) is None
        # an unknown type raises: see test_a_value_the_column_cant_hold_is_refused
        importer.convert("1", kind)


# --- a file comes in ---


def test_a_current_file_is_brought_in(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "111" / "user_data.db", 111)
    before = snapshot(folder)
    target = Target()

    code, said = run(folder, target, apply=True)

    assert code == 0, said
    assert "onlyfans: 1 files, 1 creators" in said
    assert target.prepared == [(["onlyfans"], set(ALL_FLAGS))]
    # read first, then written
    assert target.opened == [True, False]
    assert {key: len(rows) for key, rows in target.kept.items() if rows} == {
        ("onlyfans", "labels"): 1,
        ("onlyfans", "medias"): 3,
        ("onlyfans", "messages"): 1,
        ("onlyfans", "models"): 1,
        ("onlyfans", "posts"): 2,
        ("onlyfans", "profiles"): 1,
        ("onlyfans", "stories"): 1,
    }
    # oldest first, without the file's own row numbers
    posts = target.rows("onlyfans", "posts")
    assert [row["post_id"] for row in posts] == [20, 10]
    assert "id" not in posts[0]
    assert posts[1] == {
        "post_id": 10,
        "text": "first\nline two\ttab \\ slash",
        "price": 9.99,
        "paid": True,
        "archived": True,
        "pinned": False,
        "stream": False,
        "opened": False,
        "created_at": "2024-03-01T09:30:00+00:00",
        "model_id": 111,
        "is_deleted": True,
        "purchase_type": None,
        "mentions": None,
    }
    first, second, third = target.rows("onlyfans", "medias")
    assert (first["size"], first["downloaded"], first["skip_permanent"]) == (
        2**40,
        True,
        None,
    )
    assert first["posted_at"] == "2024-03-01T09:30:00+00:00"
    assert (second["created_at"], second["downloaded"], second["skip_permanent"]) == (
        None,
        False,
        True,
    )
    # the ids PostgreSQL keeps as text (a profile picture's are hashes)
    assert (third["media_id"], third["post_id"]) == ("1", "20")
    assert target.rows("onlyfans", "labels")[0]["label_id"] == "900"
    assert target.rows("onlyfans", "profiles") == [
        {"user_id": 111, "username": "a_creator"}
    ]
    # the files weren't touched, and nothing was left beside them
    assert snapshot(folder) == before


def test_a_file_from_2023_is_brought_in(tmp_path):
    folder = tmp_path / "data"
    file_2023(folder / "user_data.db", 111)
    before = snapshot(folder)
    target = Target()

    code, said = run(folder, target, apply=True)

    assert code == 0, said
    posts = target.rows("onlyfans", "posts")
    assert [(row["post_id"], row["model_id"], row["paid"]) for row in posts] == [
        (10, 111, False),
        (11, 111, True),
    ]
    medias = target.rows("onlyfans", "medias")
    assert [(row["media_id"], row["model_id"], row["downloaded"]) for row in medias] == [
        ("1", 111, True),
        ("2", 111, False),
    ]
    assert medias[0]["created_at"] == "2023-01-05T10:00:00+00:00"
    assert len(target.rows("onlyfans", "messages")) == 1
    assert target.rows("onlyfans", "profiles") == [
        {"user_id": 111, "username": "a_creator"}
    ]
    # the old file still has its old layout
    assert snapshot(folder) == before
    assert "model_id" not in [
        row[1] for row in sql(folder / "user_data.db", "PRAGMA table_info(posts)")
    ]


def test_a_file_from_before_the_newest_columns_is_brought_in(tmp_path):
    folder = tmp_path / "data"
    file_before_newest_columns(folder / "user_data.db", 111)
    before = snapshot(folder)
    target = Target()

    code, said = run(folder, target, apply=True)

    assert code == 0, said
    assert len(target.rows("onlyfans", "medias")) == 3
    assert target.rows("onlyfans", "medias")[0]["skip_permanent"] is None
    assert target.rows("onlyfans", "posts")[0]["mentions"] is None
    assert snapshot(folder) == before


def test_each_file_goes_to_its_sites_tables(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "111" / "user_data.db", 111)
    current_file(folder / "Fansly" / "111" / "user_data.db", 111, site="fansly")
    target = Target()

    code, said = run(folder, target, apply=True)

    # the same id on two sites is two creators
    assert code == 0, said
    assert target.prepared[0][0] == ["fansly", "onlyfans"]
    assert len(target.rows("onlyfans", "medias")) == 3
    assert len(target.rows("fansly", "medias")) == 3


def test_what_sqlite_hasnt_written_into_the_file_yet_comes_too(tmp_path):
    folder = tmp_path / "data"
    path = current_file(folder / "user_data.db", 111)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute(
            "INSERT INTO posts (post_id, model_id, created_at) VALUES (99, 111, '')"
        )
        conn.commit()
        assert path.with_name("user_data.db-wal").stat().st_size > 0
        target = Target()
        code, said = run(folder, target, apply=True)
    finally:
        conn.close()

    assert code == 0, said
    assert [row["post_id"] for row in target.rows("onlyfans", "posts")] == [20, 10, 99]


def test_a_profile_picture_and_a_label_with_a_word_for_an_id_come_too(tmp_path):
    folder = tmp_path / "data"
    path = current_file(folder / "user_data.db", 111)
    picture = "0123456789abcdef0123456789abcdef"
    sql(
        path,
        "INSERT INTO medias (media_id, post_id, api_type, downloaded, model_id)"
        f" VALUES ('{picture}', '{picture[::-1]}', 'Profile', 1, 111)",
    )
    sql(
        path,
        "INSERT INTO labels (label_id, name, type, post_id, model_id)"
        " VALUES ('archived', 'Archived', 'archived', 10, 111)",
    )
    target = Target()

    code, said = run(folder, target, apply=True)

    assert code == 0, said
    last = target.rows("onlyfans", "medias")[-1]
    assert (last["media_id"], last["post_id"]) == (picture, picture[::-1])
    assert target.rows("onlyfans", "labels")[-1]["label_id"] == "archived"


def test_rows_without_a_creator_get_the_files_one(tmp_path):
    folder = tmp_path / "data"
    path = current_file(folder / "user_data.db", 111)
    sql(path, "UPDATE posts SET model_id = NULL WHERE post_id = 20")
    target = Target()

    code, said = run(folder, target, apply=True)

    assert code == 0, said
    assert {row["model_id"] for row in target.rows("onlyfans", "posts")} == {111}


# --- the preview ---


def test_without_apply_nothing_is_written(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "user_data.db", 111)
    before = snapshot(folder)
    target = Target()

    code, said = run(folder, target)

    assert code == 0
    assert "medias             3 rows" in said
    assert "Nothing was written. Run again with --apply" in said
    assert target.prepared == []
    assert target.opened == [True]
    assert target.kept == {}
    assert snapshot(folder) == before


def test_the_files_can_be_checked_without_a_server(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "user_data.db", 111)

    code, said = run(folder)

    assert code == 0
    assert "onlyfans: 1 files, 1 creators" in said
    assert "OFSC_DATABASE_URL isn't set: the files were checked" in said


def test_apply_needs_a_database(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "user_data.db", 111)

    code, said = run(folder, apply=True)

    assert code == 2
    assert "OFSC_DATABASE_URL isn't set" in said


def test_a_folder_without_databases_is_said_so(tmp_path):
    assert run(tmp_path / "missing")[0] == 2
    code, said = run(tmp_path)
    assert code == 2
    assert "No user_data.db files" in said


def test_an_empty_file_is_left_out(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "111" / "user_data.db", 111)
    empty = folder / "222" / "user_data.db"
    current_file(empty, 222)
    for table in ("posts", "medias", "messages", "stories", "labels"):
        sql(empty, f"DELETE FROM {table}")
    target = Target()

    code, said = run(folder, target, apply=True)

    assert code == 0, said
    assert "1 files have nothing in them" in said
    assert target.rows("onlyfans", "profiles") == [
        {"user_id": 111, "username": "a_creator"}
    ]


def test_the_working_copies_are_removed(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "user_data.db", 111)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "mine.txt").write_text("kept")

    code, said = run(folder, Target(), apply=True, staging=staging)

    assert code == 0, said
    assert [path.name for path in staging.iterdir()] == ["mine.txt"]


# --- what stops a file ---


def refused(tmp_path, spoil, why, **kwargs):
    """Two creators' files, one of them spoiled: nothing is written, and the
    report says why."""
    folder = tmp_path / "data"
    current_file(folder / "111" / "user_data.db", 111)
    bad = current_file(folder / "222" / "user_data.db", 222)
    spoil(bad)
    before = snapshot(folder)
    target = Target(**kwargs)

    code, said = run(folder, target, apply=True)

    assert code == 1
    assert why in said
    assert "1 files can't be brought in" in said
    assert str(bad) in said
    assert "Nothing was written" in said
    assert target.kept == {}
    assert target.prepared == []
    assert snapshot(folder) == before
    return folder, said


def test_a_value_that_cant_be_stored_stops_the_import(tmp_path):
    def spoil(path):
        sql(path, "UPDATE messages SET user_id = 'not-a-number' WHERE post_id = 30")
        sql(path, "UPDATE posts SET paid = 2 WHERE post_id = 10")

    folder, said = refused(tmp_path, spoil, "2 values can't be stored")
    assert "messages row 1, user_id: 'not-a-number' isn't a whole number" in said
    assert "posts row 2, paid: 2 is neither yes (1) nor no (0)" in said


def test_text_that_isnt_utf8_is_listed(tmp_path):
    def spoil(path):
        sql(path, "UPDATE posts SET text = CAST(X'FFFE41' AS TEXT) WHERE post_id = 10")

    refused(tmp_path, spoil, "posts row 2, text: isn't valid UTF-8 text")


def test_only_the_first_bad_values_are_listed(tmp_path):
    def spoil(path):
        sql(
            path,
            "INSERT INTO stories (post_id, model_id, paid) VALUES (?, 222, 7)",
            [(number,) for number in range(100, 130)],
        )

    folder, said = refused(tmp_path, spoil, "30 values can't be stored (and 10 more)")
    assert said.count("stories row") == importer.SHOWN


def test_rows_nobody_owns_stop_the_import(tmp_path):
    def spoil(path):
        sql(path, "INSERT INTO profiles (user_id, username) VALUES (333, 'another')")
        sql(path, "UPDATE posts SET model_id = NULL WHERE post_id = 20")

    refused(tmp_path, spoil, "posts row 1, model_id: has no creator")


def test_a_file_from_a_newer_version_is_refused(tmp_path):
    def spoil(path):
        sql(path, "INSERT INTO schema_flags VALUES ('a_later_step', '1')")

    refused(tmp_path, spoil, "a newer version of the scraper")


def test_a_column_this_version_doesnt_know_is_refused(tmp_path):
    def spoil(path):
        sql(path, "ALTER TABLE medias ADD COLUMN made_up TEXT")

    refused(tmp_path, spoil, "medias has a column this version doesn't know (made_up)")


def test_a_site_this_version_doesnt_know_is_refused(tmp_path):
    def spoil(path):
        sql(path, "INSERT INTO schema_flags VALUES ('site', 'elsewhere')")

    refused(tmp_path, spoil, "a site this version doesn't know ('elsewhere')")


def test_a_file_that_isnt_a_database_is_refused(tmp_path):
    def spoil(path):
        path.write_bytes(b"not a database" * 100)

    refused(tmp_path, spoil, "its copy couldn't be brought up to date")


def test_a_creator_postgresql_already_has_is_refused(tmp_path):
    refused(
        tmp_path,
        lambda path: None,
        "PostgreSQL already has rows for creator 222",
        held={"onlyfans": {222, 999}},
    )


def test_two_files_for_one_creator_stop_at_the_preview(tmp_path):
    folder = tmp_path / "data"
    first = current_file(folder / "old_name" / "user_data.db", 111)
    second = current_file(folder / "new_name" / "user_data.db", 111, name="renamed")
    current_file(folder / "333" / "user_data.db", 333)
    target = Target()

    code, said = run(folder, target)

    assert code == 1
    assert "2 files can't be brought in" in said
    assert f"creator 111 is also in {first}" in said
    assert f"creator 111 is also in {second}" in said
    assert "Merge Databases" in said
    assert target.kept == {}


def test_a_table_that_isnt_the_scrapers_is_left_out_and_said(tmp_path):
    folder = tmp_path / "data"
    path = current_file(folder / "user_data.db", 111)
    sql(path, "CREATE TABLE notes (words TEXT)")
    sql(path, "INSERT INTO notes VALUES ('mine')")
    target = Target()

    code, said = run(folder, target, apply=True)

    assert code == 0, said
    assert "its table 'notes' (1 rows) isn't one of the scraper's" in said
    assert ("onlyfans", "notes") not in target.kept


def test_the_good_files_can_be_brought_in_without_the_bad(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "111" / "user_data.db", 111)
    bad = current_file(folder / "222" / "user_data.db", 222)
    sql(bad, "UPDATE posts SET paid = 2")
    target = Target()

    code, said = run(folder, target, apply=True, skip_bad=True)

    # brought in, but not everything: the exit code says so
    assert code == 1
    assert "onlyfans: 8 rows added." in said
    assert {row["model_id"] for row in target.rows("onlyfans", "medias")} == {111}
    assert {row["user_id"] for row in target.rows("onlyfans", "profiles")} == {111}


# --- all or nothing ---


def test_a_failure_part_way_leaves_nothing_and_a_rerun_works(tmp_path):
    folder = tmp_path / "data"
    current_file(folder / "111" / "user_data.db", 111)
    current_file(folder / "222" / "user_data.db", 222)
    target = Target()
    target.fail_at = "stories"

    code, said = run(folder, target, apply=True)

    assert code == 1
    assert "Nothing was written. the server went away" in said
    assert target.kept == {}

    target.fail_at = None
    code, said = run(folder, target, apply=True)

    assert code == 0, said
    assert len(target.rows("onlyfans", "medias")) == 6


def test_rows_that_dont_all_arrive_are_not_kept(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    current_file(folder / "user_data.db", 111)
    target = Target()
    write = target.write

    def loses_a_row(site, table, columns, rows):
        count = write(site, table, columns, rows)
        if table == "medias":
            target.pending[site, table].pop()
        return count

    monkeypatch.setattr(target, "write", loses_a_row)

    code, said = run(folder, target, apply=True)

    assert code == 1
    assert "onlyfans.medias holds 2 rows where 3 were expected" in said
    assert target.kept == {}


# --- the command line ---


def test_the_command_reads_its_own_arguments(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        importer, "run", lambda folder, **kwargs: calls.append((folder, kwargs)) or 0
    )

    assert importer.main([str(tmp_path)]) == 0
    assert importer.main([str(tmp_path), "--apply", "--skip-bad-files"]) == 0

    assert calls == [
        (tmp_path, dict(apply=False, skip_bad=False, staging=None)),
        (tmp_path, dict(apply=True, skip_bad=True, staging=None)),
    ]

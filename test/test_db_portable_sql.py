"""The database's queries keep to SQL that SQLite and PostgreSQL both accept
(#608), so the same query text can run on either.

The first test reads every SQL string out of the database code and looks for
the SQLite-only spellings that were in use. The rest check that the rewritten
queries still do the same thing on SQLite.
"""

import ast
import asyncio
import contextlib
import pathlib
import re
import sqlite3

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.db.operations as operations
import ofscraper.db.operations_.empty as empty
import ofscraper.db.operations_.media as media
import ofscraper.db.operations_.others as others
import ofscraper.db.operations_.profile as profile
import ofscraper.db.operations_.wrapper as wrapper
import ofscraper.db.utils.columns as db_columns
from ofscraper.db.transition import modify_tables

OPERATIONS = pathlib.Path(operations.__file__).parent / "operations_"
STATEMENT = re.compile(r"^\s*(WITH|SELECT|UPDATE|DELETE|INSERT)\b", re.IGNORECASE)
BOOLEANS = (
    "linked|downloaded|unlocked|skip_permanent|preview|paid|archived|pinned"
    "|stream|opened|is_deleted"
)
# what the queries used, and what both databases accept instead
SQLITE_ONLY = {
    "a table name in quotes": re.compile(
        r"\b(INTO|UPDATE|FROM)\s+'", re.IGNORECASE
    ),
    "INSERT OR REPLACE (use ON CONFLICT)": re.compile(r"INSERT\s+OR\b", re.IGNORECASE),
    "a yes/no column compared with 0 or 1 (use TRUE/FALSE)": re.compile(
        rf"\b({BOOLEANS})\s*(=|IS|IS\s+NOT)\s*\(?[01]\)?(?![\w.])", re.IGNORECASE
    ),
    "IS ? (use db_columns.same_creator)": re.compile(r"\bIS\s+\?", re.IGNORECASE),
    "a % sign (the PostgreSQL driver's placeholder)": re.compile("%"),
    "INSERT ... SELECT ? (the value has no type)": re.compile(
        r"INSERT\b[^;]*\bSELECT\s+\?", re.IGNORECASE | re.DOTALL
    ),
}
# the steps that bring an old SQLite file up to date only ever run on SQLite
SQLITE_UPGRADE = re.compile(r"PRAGMA|sqlite_master", re.IGNORECASE)


def sql_strings():
    found = []
    for path in sorted(OPERATIONS.glob("*.py")):
        if path.name == "wrapper.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(
                    part.value if isinstance(part, ast.Constant) else "x"
                    for part in node.values
                )
            else:
                continue
            if STATEMENT.match(text) and not SQLITE_UPGRADE.search(text):
                found.append((f"{path.name}:{node.lineno}", text))
    return found


def test_the_queries_are_found():
    # the check below would pass on nothing
    assert len(sql_strings()) > 60


def test_no_query_uses_a_spelling_only_sqlite_accepts():
    problems = [
        f"{where}: {name}"
        for where, text in sql_strings()
        for name, pattern in SQLITE_ONLY.items()
        if pattern.search(text)
    ]
    assert problems == []


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "user_data.db"

    async def make():
        await operations.create_tables(db_path=path)
        await modify_tables(db_path=path, new_db=True)

    asyncio.run(make())
    return path


def rows(db, sql):
    with contextlib.closing(sqlite3.connect(db)) as conn:
        return sorted(conn.execute(sql).fetchall())


def test_a_flag_is_set_once_and_can_be_set_again(db):
    async def go():
        await others.add_flag_schema("made_up_flag", db_path=db)
        await others.add_flag_schema("made_up_flag", db_path=db)
        await others.write_site_flag("fansly", db_path=db)
        await others.write_site_flag("fansly", db_path=db)

    asyncio.run(go())

    assert rows(
        db,
        "SELECT flag_name, flag_value FROM schema_flags"
        " WHERE flag_name IN ('made_up_flag', 'site')",
    ) == [("made_up_flag", "1"), ("site", "fansly")]
    assert "made_up_flag" in others.get_schema_changes(db_path=db)
    # a flag with another value isn't a finished step
    assert "site" not in others.get_schema_changes(db_path=db)


def test_a_profile_and_a_model_are_added_once(db):
    async def go():
        for _ in range(2):
            await profile.write_profile_table(
                model_id=5, username="a_creator", db_path=db
            )
            await profile.write_models_table(model_id=5, db_path=db)
        await profile.write_profile_table(model_id=5, username="renamed", db_path=db)

    asyncio.run(go())

    assert rows(db, "SELECT user_id, username FROM profiles") == [
        (5, "a_creator"),
        (5, "renamed"),
    ]
    assert rows(db, "SELECT model_id FROM models") == [(5,)]


def test_empty_counts_rows_by_position(db):
    assert asyncio.run(empty.empty(db_path=db)) is True
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO messages (post_id, model_id) VALUES (1, 5)")
        conn.commit()
    assert asyncio.run(empty.empty(db_path=db)) is False


@pytest.mark.parametrize("model_id", [5, None], ids=["a creator", "no creator"])
def test_permanent_skips_are_read_and_unmarked_for_the_rows_creator(db, model_id):
    # model_id IS ? matched a row without a creator too; so does the new form
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.executemany(
            "INSERT INTO medias (media_id, post_id, model_id, downloaded,"
            " skip_permanent) VALUES (?,?,?,1,?)",
            [(1, 10, model_id, 1), (2, 10, model_id, None), (3, 10, 99, 1)],
        )
        conn.commit()

    async def go():
        ids = await media.get_permanent_skip_media_ids(model_id=model_id, db_path=db)
        changed = await media.unmark_permanent_skips(
            [1, 2, 3], model_id=model_id, db_path=db
        )
        return ids, changed

    assert asyncio.run(go()) == ({1}, 1)
    assert rows(db, "SELECT media_id, downloaded, skip_permanent FROM medias") == [
        (1, None, None),
        (2, 1, None),
        (3, 1, 1),
    ]


def test_same_creator():
    assert db_columns.same_creator(5) == ("model_id = ?", [5])
    assert db_columns.same_creator(None) == ("model_id IS NULL", [])


def test_an_sqlite_too_old_for_the_queries_is_named(db, monkeypatch):
    monkeypatch.setattr(wrapper.sqlite3, "sqlite_version_info", (3, 22, 0))
    monkeypatch.setattr(wrapper.sqlite3, "sqlite_version", "3.22.0")

    with pytest.raises(wrapper.DatabaseUnreadable, match="3.22.0.*3.24 or newer"):
        others.get_schema_changes(db_path=db)


def test_this_pythons_sqlite_is_new_enough():
    assert sqlite3.sqlite_version_info >= wrapper.MIN_SQLITE

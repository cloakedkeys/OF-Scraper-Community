"""The generic SQLite helpers behind the per-table upgrade, rebuild and area
reads (#844): db_columns.add_column, rebuild.TableSpec and media._area_media.
"""

import asyncio
import contextlib
import sqlite3

import arrow
import pytest

import ofscraper.db.operations_.labels as labels
import ofscraper.db.operations_.media as media
import ofscraper.db.operations_.messages as messages
import ofscraper.db.operations_.others as others
import ofscraper.db.operations_.posts as posts
import ofscraper.db.operations_.stories as stories
import ofscraper.db.utils.columns as db_columns
import ofscraper.db.utils.rebuild as rebuild

# every add_column_* wrapper: the table, the column and the type it adds
ADDED_COLUMNS = [
    (media.add_column_media_hash, "medias", "hash", "VARCHAR", None),
    (media.add_column_media_unlocked, "medias", "unlocked", "BOOL", None),
    (media.add_column_media_posted_at, "medias", "posted_at", "TIMESTAMP", None),
    (media.add_column_media_ID, "medias", "model_id", "INTEGER", None),
    (media.add_column_media_skip_permanent, "medias", "skip_permanent", "BOOL", None),
    (media.add_column_media_duration, "medias", "duration", "VARCHAR", None),
    (posts.add_column_post_ID, "posts", "model_id", "INTEGER", None),
    (posts.add_column_post_pinned, "posts", "pinned", "BOOLEAN", None),
    (posts.add_column_post_stream, "posts", "stream", "BOOLEAN", None),
    (posts.add_column_post_opened, "posts", "opened", "BOOLEAN", None),
    (posts.add_column_post_is_deleted, "posts", "is_deleted", "BOOLEAN", "0"),
    (posts.add_column_post_purchase_type, "posts", "purchase_type", "VARCHAR", None),
    (posts.add_column_post_mentions, "posts", "mentions", "VARCHAR", None),
    (messages.add_column_messages_ID, "messages", "model_id", "INTEGER", None),
    (messages.add_column_message_is_deleted, "messages", "is_deleted", "BOOLEAN", "0"),
    (messages.add_column_message_group_id, "messages", "group_id", "VARCHAR", None),
    (stories.add_column_stories_ID, "stories", "model_id", "INTEGER", None),
    (stories.add_column_stories_highlight_id, "stories", "highlight_id", "INTEGER", None),
    (labels.add_column_labels_ID, "labels", "model_id", "INTEGER", None),
    (others.add_column_other_ID, "others", "model_id", "INTEGER", None),
    (others.add_column_products_ID, "products", "model_id", "INTEGER", None),
]


def sql(db, statement, params=()):
    with contextlib.closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(statement, params).fetchall()
        conn.commit()
        return rows


def column_info(db, table):
    """{name: (type, default)} of the table's columns."""
    return {row[1]: (row[2], row[4]) for row in sql(db, f"PRAGMA table_info('{table}')")}


@pytest.mark.parametrize(
    "add,table,column,sqltype,default",
    ADDED_COLUMNS,
    ids=[add.__name__ for add, *_ in ADDED_COLUMNS],
)
def test_each_wrapper_adds_its_column_once(tmp_path, add, table, column, sqltype, default):
    db = tmp_path / "user_data.db"
    sql(db, f"CREATE TABLE {table} (id INTEGER NOT NULL, PRIMARY KEY (id))")
    sql(db, f"INSERT INTO {table} (id) VALUES (1)")

    asyncio.run(add(db_path=db))
    assert column_info(db, table)[column] == (sqltype, default)

    # a second run, as at every start, changes nothing
    asyncio.run(add(db_path=db))
    assert list(column_info(db, table)) == ["id", column]
    assert sql(db, f"SELECT id FROM {table}") == [(1,)]


def test_add_column_commits_and_says_whether_it_added(tmp_path):
    db = tmp_path / "user_data.db"
    sql(db, "CREATE TABLE posts (id INTEGER)")
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        assert db_columns.add_column(conn, "posts", "mentions", "VARCHAR") is True
        assert not conn.in_transaction
        assert db_columns.add_column(conn, "posts", "mentions", "VARCHAR") is False
    assert "mentions" in column_info(db, "posts")


def test_add_column_rolls_back_and_raises_on_an_error(tmp_path):
    db = tmp_path / "user_data.db"
    sql(db, "CREATE TABLE posts (id INTEGER)")
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("INSERT INTO posts (id) VALUES (1)")
        # no such table
        with pytest.raises(sqlite3.OperationalError):
            db_columns.add_column(conn, "missing", "mentions", "VARCHAR")
        assert not conn.in_transaction
    # the uncommitted insert went with the rollback
    assert sql(db, "SELECT id FROM posts") == []


def test_transition_rows_fill_missing_optional_columns():
    spec = rebuild.TableSpec("t", "", "", [], keys=("a", "b"), optional=("c",))
    assert rebuild.transition_rows(spec, [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5}]) == [
        (1, 2, 3),
        (4, 5, None),
    ]
    with pytest.raises(KeyError):
        rebuild.transition_rows(spec, [{"a": 1}])


def test_transition_read_fills_model_id(tmp_path):
    db = tmp_path / "user_data.db"
    # a table from before model_id
    sql(db, "CREATE TABLE others (id INTEGER, post_id INTEGER, text VARCHAR, price INTEGER,"
            " paid INTEGER, archived BOOLEAN, created_at TIMESTAMP)")
    sql(db, "INSERT INTO others (post_id, text) VALUES (601, 'a')")
    assert asyncio.run(others.get_all_others_transition(db_path=db, database_model=42)) == [
        {
            "post_id": 601,
            "text": "a",
            "price": None,
            "paid": None,
            "archived": None,
            "created_at": None,
            "model_id": 42,
        }
    ]


AREAS = {
    "get_timeline_media": "Timeline",
    "get_pinned_media": "Pinned",
    "get_archived_media": "Archived",
    "get_messages_media": "Messages",
    "get_streams_media": "Streams",
    "get_highlights_media": "Highlights",
    "get_stories_media": "Stories",
}
POSTED = "2024-01-02T10:00:00+00:00"
CREATED = "2024-01-01T10:00:00+00:00"


@pytest.fixture
def area_db(tmp_path):
    """A medias table with, for each area, a row with a posted date, one
    with only a created date and one with neither, and another creator's
    row."""
    db = tmp_path / "user_data.db"
    sql(db, media.mediaCreate)
    media_id = 0
    for api_type in AREAS.values():
        for posted_at, created_at, model_id in (
            (POSTED, CREATED, 5),
            (None, CREATED, 5),
            (None, None, 5),
            (POSTED, CREATED, 6),
        ):
            media_id += 1
            sql(
                db,
                "INSERT INTO medias (media_id, post_id, api_type, posted_at,"
                " created_at, model_id) VALUES (?, ?, ?, ?, ?, ?)",
                [media_id, media_id, api_type, posted_at, created_at, model_id],
            )
    return db


@pytest.mark.parametrize("reader,api_type", AREAS.items())
def test_area_reads(area_db, reader, api_type):
    # @run: called outside an event loop, the read runs to its result
    rows = getattr(media, reader)(model_id=5, db_path=area_db)
    assert {row["api_type"] for row in rows} == {api_type}
    assert {row["model_id"] for row in rows} == {5}
    dates = [row["posted_at"] for row in rows]
    expected = [arrow.get(POSTED), arrow.get(CREATED), arrow.get(0)]
    if reader == "get_timeline_media":
        # the timeline's posted_at is an Arrow; the other areas' a timestamp
        assert all(isinstance(date, arrow.Arrow) for date in dates)
        assert dates == expected
    else:
        assert all(isinstance(date, float) for date in dates)
        assert dates == [date.float_timestamp for date in expected]

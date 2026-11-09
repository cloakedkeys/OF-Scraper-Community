"""`ofscraper db` only reads a database (#373).

The shared wrapper set WAL mode and took the write lock for every operation,
so just reading a database switched it from rollback journal mode to WAL,
and a read-only database file couldn't be read at all. The db command's
readers now open the file read-only; writes keep WAL.
"""

import asyncio
import contextlib
import hashlib
import os
import sqlite3
import stat
from types import SimpleNamespace

import pytest

import ofscraper.commands.db as db_command
import ofscraper.db.operations_.media as media_ops
import ofscraper.db.operations_.wrapper as wrapper

ALL_AREAS = [
    "Timeline",
    "Pinned",
    "Archived",
    "Messages",
    "Streams",
    "Highlights",
    "Stories",
]
READERS = [
    "get_all_medias",
    "get_timeline_media",
    "get_pinned_media",
    "get_archived_media",
    "get_messages_media",
    "get_streams_media",
    "get_highlights_media",
    "get_stories_media",
    "get_permanent_skip_media_ids",
]


def make_db(path):
    """A real database in rollback (delete) journal mode, with two rows."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute(media_ops.mediaCreate)
        conn.executemany(
            "INSERT INTO medias (media_id, post_id, api_type, media_type,"
            " created_at, posted_at, model_id, skip_permanent)"
            " VALUES (?, ?, ?, 'Images', '2024-01-01', '2024-01-02', 5, ?)",
            [(1, 100, "timeline", 1), (2, 101, "messages", None)],
        )
        conn.commit()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def journal_mode(path):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def run_db(monkeypatch):
    """Run `ofscraper db`'s reads on one database; returns its rows."""

    def run(path, areas=ALL_AREAS, unmark=False):
        args = SimpleNamespace(
            download_area=areas,
            downloaded=None,
            protected=False,
            normal=False,
            unlocked=None,
            preview=None,
            post_id=None,
            media_id=None,
            unmark_permanent_skips=unmark,
        )
        options = SimpleNamespace(
            size_max=None,
            size_min=None,
            length_max=None,
            length_min=None,
            posted_before=None,
            posted_after=None,
            created_after=None,
            created_before=None,
            mediatypes=["Audios", "Videos", "Images"],
            max_post_count=None,
            db_asc=True,
            db_sort="mediaid",
            export=None,
        )
        monkeypatch.setattr(db_command.settings, "get_args", lambda: args)
        monkeypatch.setattr(db_command.settings, "get_settings", lambda: options)
        for name in READERS:
            reader = getattr(db_command, name)
            monkeypatch.setattr(
                db_command,
                name,
                lambda reader=reader, **kwargs: reader(**kwargs, db_path=path),
            )
        manager = db_command.DBManager("a creator", 5)
        # the command's run() keeps the current loop open; close it after
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            manager.get_wanted_media()
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        return [row["media_id"] for row in manager.media]

    return run


@pytest.fixture
def connections(monkeypatch):
    """Counts the wrapper's connections: one per read means no retries."""
    opened = []
    real = sqlite3.connect

    def connect(*args, **kwargs):
        opened.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(wrapper.sqlite3, "connect", connect)
    return opened


@pytest.mark.parametrize(
    "areas,unmark",
    [(ALL_AREAS, False), (["Timeline", "Messages"], False), (ALL_AREAS, True)],
    ids=["all areas", "some areas", "unmark filter"],
)
def test_db_leaves_rollback_database_unchanged(tmp_path, run_db, areas, unmark):
    # Given a database in rollback journal mode
    path = tmp_path / "user_data.db"
    make_db(path)
    before = digest(path)

    # When ofscraper db only reads it
    rows = run_db(path, areas, unmark)

    # Then the rows are shown
    assert rows == ([1] if unmark else [1, 2])
    # and its journal mode and bytes are unchanged, with no WAL files beside it
    assert path.read_bytes()[18:20] == b"\x01\x01"
    assert digest(path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["user_data.db"]
    assert journal_mode(path) == "delete"


@pytest.mark.parametrize("mode", ["delete", "wal"])
def test_db_reads_read_only_file(tmp_path, run_db, connections, mode):
    # Given a read-only database file
    path = tmp_path / "user_data.db"
    make_db(path)
    if mode == "wal":
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
    before = digest(path)
    os.chmod(path, stat.S_IREAD)
    connections.clear()
    try:
        # When ofscraper db reads it
        rows = run_db(path)
        assert digest(path) == before
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)

    # Then the rows are shown, with no error and no retries
    assert rows == [1, 2]
    assert len(connections) == 1


def test_sync_wrapper_reads_read_only_file(tmp_path, connections):
    # the synchronous wrapper takes read_only=True the same way
    path = tmp_path / "user_data.db"
    make_db(path)
    before = digest(path)
    os.chmod(path, stat.S_IREAD)
    connections.clear()
    try:
        ids = media_ops.get_media_post_ids(model_id=5, db_path=path, read_only=True)
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    assert sorted(ids) == [(1, 100), (2, 101)]
    assert len(connections) == 1
    assert digest(path) == before
    assert journal_mode(path) == "delete"


def test_write_still_uses_wal(tmp_path, run_db):
    # Given a rollback database that ofscraper db has read
    path = tmp_path / "user_data.db"
    make_db(path)
    run_db(path)

    # When something writes to it (a table check as a scrape makes, and
    # the db command's own un-marking)
    asyncio.run(media_ops.create_media_table(db_path=path))
    changed = asyncio.run(
        media_ops.unmark_permanent_skips([1], model_id=5, db_path=path)
    )

    # Then the database uses WAL, and the write is kept
    assert changed == 1
    assert journal_mode(path) == "wal"
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute(
            "SELECT skip_permanent FROM medias WHERE media_id = 1"
        ).fetchone() == (None,)

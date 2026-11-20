"""A read that decides what a creator already has counts only that creator's
rows (#608).

One database can hold several creators: a merged database, or a `metadata`
folder that doesn't name the creator. These reads had no creator in them, so
one creator's rows answered for another: a post or media the other creator
already had was never saved for this one, and removing duplicate files by
hash could delete the other creator's file.

Each test builds a real SQLite database with the app's own create and
migration code, and puts two creators' rows in it.
"""

import asyncio
import contextlib
import sqlite3
from types import SimpleNamespace

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.db.operations as operations
import ofscraper.db.operations_.media as media
import ofscraper.db.operations_.messages as messages
import ofscraper.db.operations_.posts as posts
import ofscraper.db.operations_.stories as stories
import ofscraper.utils.hash as hash_
import ofscraper.utils.settings as settings
from ofscraper.db.transition import modify_tables

ONE, TWO = 111, 222
DATE = "2024-01-03T03:04:05+00:00"


def execute(db, sql, rows):
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.executemany(sql, rows)
        conn.commit()


def media_rows(db):
    with contextlib.closing(sqlite3.connect(db)) as conn:
        return sorted(
            conn.execute("SELECT media_id, post_id, model_id FROM medias").fetchall()
        )


@pytest.fixture
def db(tmp_path):
    """Two creators in one database. Post 10 and media 1 belong to both."""
    path = tmp_path / "user_data.db"

    async def make():
        await operations.create_tables(db_path=path)
        await modify_tables(db_path=path, new_db=True)

    asyncio.run(make())
    for table in ("posts", "messages", "stories"):
        execute(
            path,
            f"INSERT INTO {table} (post_id, model_id) VALUES (?, ?)",
            [(10, ONE), (11, ONE), (10, TWO), (22, TWO)],
        )
    execute(
        path,
        "INSERT INTO medias (media_id, post_id, model_id, downloaded, directory,"
        " filename, hash, size, media_type) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, 10, ONE, 1, "/one", "/one/a.jpg", "same", 5, "Images"),
            (2, 11, ONE, 1, "/one", "/one/b.jpg", "other", 5, "Images"),
            (1, 10, TWO, 1, "/two", "/two/a.jpg", "same", 5, "Images"),
            (3, 22, TWO, 0, None, None, None, None, "Videos"),
        ],
    )
    return path


@pytest.mark.parametrize(
    "read",
    [posts.get_all_post_ids, messages.get_all_messages_ids, stories.get_all_stories_ids],
    ids=["posts", "messages", "stories"],
)
def test_the_ids_a_creator_has_are_its_own(db, read):
    assert sorted(asyncio.run(read(model_id=ONE, db_path=db))) == [10, 11]
    assert sorted(asyncio.run(read(model_id=TWO, db_path=db))) == [10, 22]


def test_the_media_a_creator_has_is_its_own(db):
    assert sorted(asyncio.run(media.get_media_ids(model_id=ONE, db_path=db))) == [1, 2]
    assert sorted(asyncio.run(media.get_media_ids(model_id=TWO, db_path=db))) == [1, 3]
    assert sorted(media.get_media_post_ids(model_id=ONE, db_path=db)) == [
        (1, 10),
        (2, 11),
    ]
    assert sorted(media.get_media_post_ids(model_id=TWO, db_path=db)) == [
        (1, 10),
        (3, 22),
    ]


def test_the_downloaded_files_of_a_creator_are_its_own(db):
    # the check table opens the folder of the file it gets here (#424)
    assert media.get_media_post_files_downloaded(model_id=TWO, db_path=db) == {
        (1, 10): ("/two", "/two/a.jpg")
    }
    assert media.get_media_post_files_downloaded(
        model_id=ONE, db_path=db, media_id=1
    ) == {(1, 10): ("/one", "/one/a.jpg")}


def test_a_read_without_a_creator_is_the_whole_database(db):
    # tools that read a database by its path (merge, the tests) give no creator
    assert sorted(asyncio.run(posts.get_all_post_ids(db_path=db))) == [10, 10, 11, 22]
    assert len(media.get_media_post_ids(db_path=db)) == 4
    assert len(media.get_media_post_files_downloaded(db_path=db)) == 2


def test_across_all_models_still_reads_every_creator(db):
    # "unique media across all models" means the whole database, as before
    assert media.get_media_ids_downloaded(model_id=TWO, db_path=db) == {1, 2}
    assert sorted(media.get_media_post_ids_downloaded(model_id=TWO, db_path=db)) == [
        (1, 10),
        (1, 10),
        (2, 11),
    ]


def test_a_table_from_before_model_id_is_not_filtered(tmp_path):
    # such a table holds one creator's rows, and has no column to filter on
    path = tmp_path / "user_data.db"
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, post_id INTEGER)")
        conn.execute("INSERT INTO posts (post_id) VALUES (10)")
        conn.commit()
    assert asyncio.run(
        posts.get_all_post_ids(model_id=ONE, db_path=path, read_only=True)
    ) == [10]


def _media(media_id, post_id):
    return SimpleNamespace(
        id=media_id,
        post_id=post_id,
        link=None,
        responsetype="timeline",
        mediatype="images",
        preview=0,
        linked=None,
        date=DATE,
        postdate=DATE,
        duration_string=None,
        canview=True,
    )


def test_media_another_creator_has_is_still_saved_for_this_one(db):
    # media 2 of post 11 is creator ONE's. It counted as already saved, so
    # the update that followed matched no row of creator TWO and nothing was
    # written for it.
    async def save():
        await media.batch_mediainsert([_media(2, 11)], model_id=TWO, db_path=db)

    asyncio.run(save())

    assert (2, 11, TWO) in media_rows(db)
    assert (2, 11, ONE) in media_rows(db)


def test_duplicate_files_are_only_looked_for_within_the_creator(
    db, tmp_path, monkeypatch
):
    # both creators have a file with the hash "same". Neither has two, so
    # there's nothing to remove: before, the two counted as duplicates and
    # one creator's file was deleted
    assert media.get_dupe_media_hashes(model_id=ONE, db_path=db) == []
    assert media.get_dupe_media_hashes(model_id=TWO, db_path=db) == []

    # a real duplicate inside one creator is still found, with its own files
    execute(
        db,
        "INSERT INTO medias (media_id, post_id, model_id, downloaded, filename,"
        " hash, size, media_type) VALUES (?,?,?,?,?,?,?,?)",
        [(4, 11, ONE, 1, "/one/copy.jpg", "same", 5, "Images")],
    )
    assert media.get_dupe_media_hashes(model_id=ONE, db_path=db) == ["same"]
    assert media.get_dupe_media_hashes(
        model_id=ONE, db_path=db, mediatype="Images"
    ) == ["same"]
    assert media.get_dupe_media_hashes(
        model_id=ONE, db_path=db, mediatype="Videos"
    ) == []
    assert sorted(
        media.get_dupe_media_files(model_id=ONE, db_path=db, hash="same")
    ) == ["/one/a.jpg", "/one/copy.jpg"]
    assert media.get_dupe_media_hashes(model_id=TWO, db_path=db) == []


def test_removing_duplicates_leaves_the_other_creators_file(tmp_path, monkeypatch):
    path = tmp_path / "user_data.db"

    async def make():
        await operations.create_tables(db_path=path)
        await modify_tables(db_path=path, new_db=True)

    asyncio.run(make())
    files = {}
    for name, model_id in (("one.jpg", ONE), ("two.jpg", TWO)):
        files[model_id] = tmp_path / name
        files[model_id].write_bytes(b"same bytes")
    execute(
        path,
        "INSERT INTO medias (media_id, post_id, model_id, downloaded, filename,"
        " hash, size) VALUES (?,?,?,?,?,?,?)",
        [
            (1, 10, ONE, 1, str(files[ONE]), "same", 10),
            (1, 10, TWO, 1, str(files[TWO]), "same", 10),
        ],
    )
    real = settings.get_settings()

    class Stub(SimpleNamespace):
        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(settings, "get_settings", lambda: Stub(hash=True))
    monkeypatch.setattr(
        hash_,
        "get_dupe_media_hashes",
        lambda **kwargs: media.get_dupe_media_hashes(db_path=path, **kwargs),
    )
    monkeypatch.setattr(
        hash_,
        "get_dupe_media_files",
        lambda **kwargs: media.get_dupe_media_files(db_path=path, **kwargs),
    )

    hash_.remove_dupes_hash("creator_two", TWO)

    assert files[ONE].is_file() and files[TWO].is_file()

import asyncio
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

import ofscraper.classes.placeholder as placeholder
import ofscraper.utils.paths.collisions as collisions
import ofscraper.utils.paths.paths as paths
from ofscraper.db.operations_.media import mediaCreate

MODEL = 10


def _media(media_id):
    return SimpleNamespace(id=media_id, post_id=1, model_id=MODEL, username="model")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the model's database at a temp file; returns a row-adder."""
    path = tmp_path / "user_data.db"
    monkeypatch.setattr(
        placeholder.databasePlaceholder,
        "databasePathHelper",
        lambda self, model_id, username: path,
    )

    def add(media_id, filepath, post_id=1):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(mediaCreate)
            conn.execute(
                "INSERT INTO medias (media_id, post_id, directory, filename, model_id, downloaded)"
                " VALUES (?, ?, ?, ?, ?, 1)",
                (media_id, post_id + media_id, str(filepath.parent), filepath.name, MODEL),
            )

    return add


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    collisions.reset()
    monkeypatch.setattr(collisions.config_data, "get_filename_collision", lambda: "media_id")
    yield
    collisions.reset()


def _resolve(path, media_id):
    return asyncio.run(collisions.resolve(path, _media(media_id)))


def test_free_path_is_kept(tmp_path, db):
    target = tmp_path / "caption.mp4"
    assert _resolve(target, 1) == target


def test_second_media_in_same_run_gets_its_id(tmp_path, db):
    target = tmp_path / "caption.mp4"
    assert _resolve(target, 1) == target
    assert _resolve(target, 2) == tmp_path / "caption_2.mp4"
    # same media asked again resolves the same way
    assert _resolve(target, 1) == target
    assert _resolve(target, 2) == tmp_path / "caption_2.mp4"


def test_path_recorded_for_another_media(tmp_path, db):
    target = tmp_path / "caption.mp4"
    db(1, target)
    assert _resolve(target, 2) == tmp_path / "caption_2.mp4"


def test_path_recorded_for_this_media(tmp_path, db):
    # a re-download (e.g. --force-all) keeps the media's own path
    target = tmp_path / "caption.mp4"
    db(1, target)
    assert _resolve(target, 1) == target


def test_stable_across_runs(tmp_path, db):
    target = tmp_path / "caption.mp4"
    first = [_resolve(target, 1), _resolve(target, 2)]
    for media_id, path in zip((1, 2), first):
        db(media_id, path)
    collisions.reset()  # next run
    assert [_resolve(target, 2), _resolve(target, 1)] == first[::-1]


def test_path_shared_by_old_overwrites_goes_to_lowest_id(tmp_path, db):
    # both rows point at one file, left by the old overwrite behaviour
    target = tmp_path / "caption.mp4"
    db(5, target)
    db(9, target)
    assert _resolve(target, 9) == tmp_path / "caption_9.mp4"
    assert _resolve(target, 5) == target


def test_concurrent_resolves_claim_once(tmp_path, db):
    target = tmp_path / "caption.mp4"

    async def go():
        return await asyncio.gather(
            *(collisions.resolve(target, _media(i)) for i in range(1, 6))
        )

    paths_ = asyncio.run(go())
    assert paths_.count(target) == 1
    assert len(set(paths_)) == 5


def test_overwrite_setting_keeps_old_behaviour(tmp_path, db, monkeypatch):
    monkeypatch.setattr(collisions.config_data, "get_filename_collision", lambda: "overwrite")
    target = tmp_path / "caption.mp4"
    db(1, target)
    assert _resolve(target, 2) == target


def test_new_model_database_without_table_is_quick(tmp_path, db):
    # the wrapper retries OperationalError with backoff; a missing table
    # must not reach it
    start = time.monotonic()
    assert _resolve(tmp_path / "caption.mp4", 1) == tmp_path / "caption.mp4"
    assert time.monotonic() - start < 2


def test_suffix_survives_truncation(tmp_path):
    long = tmp_path / ("x" * 400 + ".mp4")
    out = paths.truncate(collisions.with_media_id(long, 3784819492))
    assert out.name.endswith("_3784819492.mp4")
    assert len(str(out)) < len(str(long))


def test_placeholder_init_uses_resolved_path(tmp_path, db, monkeypatch):
    target = tmp_path / "caption.mp4"
    db(1, target)
    monkeypatch.setattr(placeholder, "naming_script", lambda *a: _none())

    async def go():
        p = placeholder.Placeholders(_media(2), "mp4")

        async def mediadir(**kw):
            return tmp_path

        async def filename():
            return "caption.mp4"

        p.getmediadir = mediadir
        p.createfilename = filename
        return (await p.init()).filepath

    assert asyncio.run(go()) == tmp_path / "caption_2.mp4"


async def _none():
    return None


def test_keeper_of_shared_path_replaces_larger_file(tmp_path, db):
    # an old collision left media 9's (larger) file at media 5's path
    import ofscraper.commands.scraper.actions.utils.paths as action_paths

    target = tmp_path / "caption.jpg"
    db(5, target)
    db(9, target)
    target.write_bytes(b"media 9, the larger image")
    assert _resolve(target, 5) == target
    temp = tmp_path / "temp.part"
    temp.write_bytes(b"media 5")
    action_paths.moveHelper(temp, target, _media(5))
    assert target.read_bytes() == b"media 5"


def test_normal_path_keeps_larger_existing_file(tmp_path, db):
    import ofscraper.commands.scraper.actions.utils.paths as action_paths

    target = tmp_path / "caption.jpg"
    db(5, target)
    target.write_bytes(b"complete earlier download")
    assert _resolve(target, 5) == target
    temp = tmp_path / "temp.part"
    temp.write_bytes(b"short")
    action_paths.moveHelper(temp, target, _media(5))
    assert target.read_bytes() == b"complete earlier download"

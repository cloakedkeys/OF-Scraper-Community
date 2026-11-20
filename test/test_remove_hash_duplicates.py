"""remove_hash_match: after a creator's downloads, later copies of a file the
creator already has are deleted (#609).

It deletes files, so most of this is about what it must leave alone. The rows
are written by the app's own code (the scrape's insert, then the download's
update), and the files are real files in a temporary folder.
"""

import asyncio
import logging
import os
import threading
from types import SimpleNamespace

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.classes.placeholder as placeholder
import ofscraper.commands.scraper.actions.download.download as download
import ofscraper.db.operations as operations
import ofscraper.utils.hash as hash_util
import ofscraper.utils.settings as settings
from ofscraper.classes.of.media import Media
from ofscraper.classes.of.posts import Post
from ofscraper.commands.scraper.actions.download import stop
from ofscraper.db.operations_.media import batch_mediainsert, mark_media_as_downloaded

MODEL_ID = 12345
OTHER_MODEL = 54321
USERNAME = "a-creator"
PICTURE = b"the same picture"


@pytest.fixture
def env(monkeypatch, tmp_path):
    real = settings.get_settings()
    stub = SimpleNamespace(**{**dict(real), "hash": True, "allow_dupe_downloads": False})
    monkeypatch.setattr(settings, "get_settings", lambda *a, **k: stub)
    db = tmp_path / "user_data.db"
    # one database for both creators, as a shared `metadata` folder gives
    monkeypatch.setattr(
        placeholder.databasePlaceholder,
        "databasePathHelper",
        lambda self, model_id, model_username: db,
    )
    monkeypatch.setattr(operations, "create_backup", lambda *a, **k: None)
    monkeypatch.setattr(stop, "requested", threading.Event())

    async def create():
        await operations.create_tables(db_path=db)

    asyncio.run(create())
    out = tmp_path / "out"
    out.mkdir()
    return SimpleNamespace(stub=stub, out=out)


def save(env, media_id, post_id, name, content=PICTURE, kind="photo", model_id=MODEL_ID):
    """A download: the scrape's row, the file, and the row marked as saved
    with the file's hash. Returns the file."""
    post = Post(
        {"id": post_id, "postedAt": "2026-01-02T03:04:05+00:00", "text": ""},
        model_id, USERNAME, responsetype="timeline",
    )
    item = Media(
        {"id": media_id, "type": kind, "canView": True,
         "files": {"full": {"url": f"https://cdn.example/{media_id}"}}},
        0, post,
    )
    path = env.out / name
    path.write_bytes(content)

    async def go():
        await batch_mediainsert([item], model_id=model_id, username=USERNAME)
        await mark_media_as_downloaded(
            item, model_id=model_id, username=USERNAME, downloaded=True,
            filepath=path, hashdata=hash_util.get_hash(path), size=path.stat().st_size,
        )

    asyncio.run(go())
    return path


def clean(model_id=MODEL_ID):
    return download.remove_downloads_with_hashes(USERNAME, model_id)


def files(env):
    return sorted(path.name for path in env.out.iterdir())


def test_a_later_copy_is_deleted_and_the_first_is_kept(env, caplog):
    save(env, 1, 10, "first.jpg")
    copy = save(env, 2, 11, "copy.jpg")
    again = save(env, 3, 12, "again.jpg")
    save(env, 4, 13, "other.jpg", content=b"another picture")

    with caplog.at_level(logging.INFO, logger="shared"):
        removed = clean()

    assert removed == [copy, again]
    assert files(env) == ["first.jpg", "other.jpg"]
    assert "Removed 2 duplicate files of a-creator" in caplog.text
    # a second run finds nothing more
    assert clean() == []
    assert files(env) == ["first.jpg", "other.jpg"]


@pytest.mark.parametrize("setting", [False, None])
def test_nothing_is_deleted_unless_the_setting_is_on(env, setting):
    # false records hashes without deleting; null (the default) doesn't hash
    save(env, 1, 10, "first.jpg")
    save(env, 2, 11, "copy.jpg")
    env.stub.hash = setting

    assert clean() == []
    assert files(env) == ["copy.jpg", "first.jpg"]


@pytest.mark.parametrize("kind, name", [("video", "clip"), ("audio", "sound")])
def test_videos_and_audio_are_cleaned_too(env, kind, name):
    save(env, 1, 10, f"{name}_1", kind=kind)
    save(env, 2, 11, f"{name}_2", kind=kind)

    clean()

    assert files(env) == [f"{name}_1"]


def test_a_file_that_two_posts_share_is_not_deleted(env):
    # one media in two posts: two rows, one file
    save(env, 1, 10, "shared.jpg")
    save(env, 1, 11, "shared.jpg")

    assert clean() == []
    assert files(env) == ["shared.jpg"]


def test_a_file_that_changed_since_it_was_saved_is_not_deleted(env):
    # the database still says the two are the same; the files no longer are
    save(env, 1, 10, "first.jpg")
    edited = save(env, 2, 11, "edited.jpg")
    edited.write_bytes(b"edited by hand!!")
    assert edited.stat().st_size == len(PICTURE)

    assert clean() == []
    assert files(env) == ["edited.jpg", "first.jpg"]


def test_another_creators_copy_is_not_deleted(env):
    save(env, 1, 10, "mine.jpg")
    save(env, 2, 11, "theirs.jpg", model_id=OTHER_MODEL)

    assert clean() == []
    assert clean(OTHER_MODEL) == []
    assert files(env) == ["mine.jpg", "theirs.jpg"]

    # nor when this creator has a spare copy of its own to remove
    spare = save(env, 3, 12, "mine_again.jpg")

    assert clean(OTHER_MODEL) == []
    assert clean() == [spare]
    assert files(env) == ["mine.jpg", "theirs.jpg"]


def test_a_copy_of_another_media_type_is_not_deleted(env):
    save(env, 1, 10, "picture.jpg")
    save(env, 2, 11, "clip.mp4", kind="video")
    copy = save(env, 3, 12, "picture_copy.jpg")

    assert clean() == [copy]
    assert files(env) == ["clip.mp4", "picture.jpg"]


def test_the_first_copy_still_there_is_the_one_kept(env):
    gone = save(env, 1, 10, "first.jpg")
    save(env, 2, 11, "second.jpg")
    third = save(env, 3, 12, "third.jpg")
    gone.unlink()

    assert clean() == [third]
    assert files(env) == ["second.jpg"]


def test_another_name_for_the_kept_file_is_left(env):
    first = save(env, 1, 10, "first.jpg")
    link = env.out / "link.jpg"
    try:
        os.link(first, link)
    except OSError:
        pytest.skip("hard links aren't available here")
    save_row_only = save(env, 2, 11, "placeholder.jpg")
    save_row_only.unlink()
    # media 2's row names the link
    os.link(first, save_row_only)

    assert clean() == []
    assert first.is_file() and save_row_only.is_file()


def test_the_copies_allow_dupes_made_of_one_media_are_kept(env):
    # with --allow-dupes a media has a file per post, on purpose
    env.stub.allow_dupe_downloads = True
    save(env, 7, 10, "seven.jpg")
    save(env, 7, 11, "seven_11.jpg")
    repost = save(env, 9, 12, "repost.jpg")

    assert clean() == [repost]
    assert files(env) == ["seven.jpg", "seven_11.jpg"]


def test_a_copy_that_cant_be_deleted_doesnt_stop_the_rest(env, monkeypatch, caplog):
    save(env, 1, 10, "first.jpg")
    locked = save(env, 2, 11, "locked.jpg")
    free = save(env, 3, 12, "free.jpg")
    unlink = os.unlink

    def in_use(path):
        if os.fspath(path) == os.fspath(locked):
            raise PermissionError("the file is open in another program")
        unlink(path)

    monkeypatch.setattr(hash_util.os, "unlink", in_use)

    with caplog.at_level(logging.WARNING, logger="shared"):
        removed = clean()

    assert removed == [free]
    assert files(env) == ["first.jpg", "locked.jpg"]
    assert "Could not remove the duplicate file" in caplog.text


def run_download(env):
    async def nothing_to_download(username, model_id, medialist, posts):
        return (0, 0, 0, 0, 0)

    async def go():
        return await download.download_process(USERNAME, MODEL_ID, [], [])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(download, "process_dicts", nothing_to_download)
        return asyncio.run(go())


def test_a_download_run_removes_the_duplicates_when_it_is_done(env):
    save(env, 1, 10, "first.jpg")
    save(env, 2, 11, "copy.jpg")

    assert run_download(env) == (0, 0, 0, 0, 0)
    assert files(env) == ["first.jpg"]


def test_a_stopped_run_removes_nothing(env):
    save(env, 1, 10, "first.jpg")
    save(env, 2, 11, "copy.jpg")
    stop.requested.set()

    run_download(env)

    assert files(env) == ["copy.jpg", "first.jpg"]

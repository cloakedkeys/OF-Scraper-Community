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

    def add(media_id, filepath, post_id=1, downloaded=1):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(mediaCreate)
            conn.execute(
                "INSERT INTO medias (media_id, post_id, directory, filename, model_id, downloaded)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (media_id, post_id + media_id, str(filepath.parent), filepath.name, MODEL, downloaded),
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


def test_owner_not_marked_downloaded_replaces_file(tmp_path, db):
    # e.g. after metadata update reset an old collision: the file there
    # isn't known to be this media's
    import ofscraper.commands.scraper.actions.utils.paths as action_paths

    target = tmp_path / "caption.jpg"
    db(5, target, downloaded=0)
    target.write_bytes(b"another media's larger image")
    assert _resolve(target, 5) == target
    temp = tmp_path / "temp.part"
    temp.write_bytes(b"media 5")
    action_paths.moveHelper(temp, target, _media(5))
    assert target.read_bytes() == b"media 5"


@pytest.mark.parametrize("mode,expected", [("update", True), ("check", False)])
def test_metadata_update_distrusts_shared_path(tmp_path, db, monkeypatch, mode, expected):
    import ofscraper.commands.metadata.manager as metadata_manager

    target = tmp_path / "caption.jpg"
    db(5, target)
    db(9, target)
    _resolve(target, 9)  # Placeholders.init runs this before the helpers
    monkeypatch.setattr(metadata_manager.settings, "get_settings", lambda: SimpleNamespace(metadata=mode))
    prev = {"directory": str(target.parent), "filename": target.name}
    shared = metadata_manager.MetaDataManager()._metadata_shared_path
    assert asyncio.run(shared(prev, MODEL, "model")) is expected
    assert asyncio.run(shared({}, MODEL, "model")) is False


def test_suffix_owned_by_another_media_in_db_is_skipped(tmp_path, db):
    # review on #116: media 3 is recorded at caption_2.jpg, which is media
    # 2's first fallback; media 2 must not land on it
    import ofscraper.commands.scraper.actions.utils.paths as action_paths

    db(1, tmp_path / "caption.jpg")
    db(3, tmp_path / "caption_2.jpg")
    (tmp_path / "caption_2.jpg").write_bytes(b"media 3")
    got = _resolve(tmp_path / "caption.jpg", 2)
    assert got not in {tmp_path / "caption.jpg", tmp_path / "caption_2.jpg"}
    temp = tmp_path / "temp.part"
    temp.write_bytes(b"media 2, a larger file than media 3")
    action_paths.moveHelper(temp, got, _media(2))
    assert (tmp_path / "caption_2.jpg").read_bytes() == b"media 3"


def test_suffix_claimed_earlier_in_run_is_skipped(tmp_path, db):
    assert _resolve(tmp_path / "same_2.jpg", 3) == tmp_path / "same_2.jpg"
    assert _resolve(tmp_path / "same.jpg", 1) == tmp_path / "same.jpg"
    got = _resolve(tmp_path / "same.jpg", 2)
    assert got not in {tmp_path / "same.jpg", tmp_path / "same_2.jpg"}
    # media 3 keeps its claim
    assert _resolve(tmp_path / "same_2.jpg", 3) == tmp_path / "same_2.jpg"


def test_case_detection_matches_the_filesystem(tmp_path):
    (tmp_path / "probe.txt").write_bytes(b"x")
    really_insensitive = (tmp_path / "PROBE.TXT").exists()
    assert collisions.case_insensitive(tmp_path / "probe.txt") is really_insensitive


def test_case_variant_of_recorded_path(tmp_path, db):
    # review on #116: on a case-insensitive filesystem CaseName.JPG and
    # casename.jpg are the same file
    recorded = tmp_path / "CaseName.JPG"
    db(1, recorded)
    recorded.write_bytes(b"media 1")
    got = _resolve(tmp_path / "casename.jpg", 2)
    if collisions.case_insensitive(recorded):
        assert got == tmp_path / "casename_2.jpg"
    else:
        assert got == tmp_path / "casename.jpg"


def test_metadata_checks_previous_path_after_template_change(tmp_path, db, monkeypatch):
    # review on #116: the old shared path is no longer what the template
    # renders, so it was never looked up
    import ofscraper.commands.metadata.manager as metadata_manager

    old = tmp_path / "old_shared.jpg"
    db(5, old)
    db(9, old)
    assert _resolve(tmp_path / "media_9.jpg", 9) == tmp_path / "media_9.jpg"
    monkeypatch.setattr(metadata_manager.settings, "get_settings", lambda: SimpleNamespace(metadata="update"))
    prev = {"directory": str(old.parent), "filename": old.name}
    shared = metadata_manager.MetaDataManager()._metadata_shared_path
    assert asyncio.run(shared(prev, MODEL, "model")) is True


@pytest.fixture
def case_sensitive_dir(tmp_path):
    """A Windows directory with per-directory case sensitivity (as WSL uses)."""
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("per-directory case sensitivity is a Windows feature")
    d = tmp_path / "cs"
    d.mkdir()
    r = subprocess.run(
        ["fsutil", "file", "setCaseSensitiveInfo", str(d), "enable"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.skip(f"can't enable case sensitivity here: {r.stdout or r.stderr}")
    return d


def test_case_sensitive_windows_dir_keeps_distinct_names(case_sensitive_dir, db):
    # review on #116: these are two different files in a case-sensitive dir
    upper, lower = case_sensitive_dir / "CaseName.JPG", case_sensitive_dir / "casename.jpg"
    upper.write_bytes(b"media 1")
    lower.write_bytes(b"media 2")
    assert not upper.samefile(lower)
    db(1, upper)
    db(2, lower)
    assert collisions.case_insensitive(lower) is False
    assert _resolve(lower, 2) == lower
    assert asyncio.run(collisions.shared_in_db(lower, MODEL, "model")) is False


def test_new_subdir_inherits_case_sensitivity(case_sensitive_dir, db):
    (case_sensitive_dir / "probe.txt").write_bytes(b"x")
    new = case_sensitive_dir / "Posts" / "Images"  # not created yet
    assert collisions.case_insensitive(new / "a.jpg") is False
    assert collisions._key(new / "A.jpg") != collisions._key(new / "a.jpg")


def test_case_insensitive_parent_still_folds_its_own_names(case_sensitive_dir, tmp_path):
    # tmp_path (ordinary storage) ignores case for the "cs" name itself; the
    # names inside cs keep their case
    (case_sensitive_dir / "probe.txt").write_bytes(b"x")
    if not collisions.case_insensitive(tmp_path / "x"):
        pytest.skip("temp storage here is case-sensitive")
    a = collisions._key(tmp_path / "CS" / "Name.jpg")
    b = collisions._key(tmp_path / "cs" / "Name.jpg")
    c = collisions._key(tmp_path / "cs" / "name.jpg")
    assert a == b and b != c

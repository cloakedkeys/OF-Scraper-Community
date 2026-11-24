"""system_free_min measures free space on the disk that holds save_location
(#716), not on `/`. OFSC_DISK_SPACE_CHECK_PATH still wins when it's set."""

import json
import pathlib
import shutil
from types import SimpleNamespace

import pytest

import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.settings as settings
import ofscraper.utils.system.free as free


@pytest.fixture
def measured(monkeypatch, tmp_path):
    """A fresh home with a config.json whose save_location is in tmp_path;
    returns the paths shutil.disk_usage was asked about."""
    home = tmp_path / "home"
    config = home / ".config" / "ofscraper" / "config.json"
    config.parent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(
        settings, "get_settings", lambda: SimpleNamespace(system_free_min=1)
    )
    calls = []
    real = shutil.disk_usage

    def disk_usage(path):
        calls.append(pathlib.Path(path))
        return real(path)

    monkeypatch.setattr(free.shutil, "disk_usage", disk_usage)

    def write_config(save_location):
        config.write_text(
            json.dumps({"file_options": {"save_location": str(save_location)}}),
            encoding="utf-8",
        )

    return SimpleNamespace(calls=calls, write_config=write_config)


def test_free_space_is_measured_on_the_save_location(measured, tmp_path):
    save = tmp_path / "downloads"
    save.mkdir()
    measured.write_config(save)

    assert free.check_free_size()
    assert measured.calls == [save]


def test_a_save_location_not_made_yet_is_measured_on_its_nearest_folder(
    measured, tmp_path
):
    # the first run creates the folder later; its disk is the parent's
    measured.write_config(tmp_path / "not" / "made" / "yet")

    assert free.check_free_size()
    assert measured.calls == [tmp_path]


def test_the_env_path_still_wins(measured, tmp_path, monkeypatch):
    save = tmp_path / "downloads"
    save.mkdir()
    measured.write_config(save)
    other = tmp_path / "other"
    other.mkdir()
    real = of_env.getattr
    monkeypatch.setattr(
        of_env,
        "getattr",
        lambda key, default=None: str(other)
        if key == "DISK_SPACE_CHECK_PATH"
        else real(key, default),
    )

    assert free.check_free_size()
    assert measured.calls == [other]

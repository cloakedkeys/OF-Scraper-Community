import pathlib

import pytest

import ofscraper.commands.scraper.actions.utils.globals as common_globals
from ofscraper.commands.scraper.actions.utils import paths


@pytest.fixture(autouse=True)
def empty_dirset(monkeypatch):
    monkeypatch.setattr(common_globals, "dirSet", set(), raising=False)


def test_file_path_records_parent(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    paths.addGlobalDir(f)
    assert common_globals.dirSet == {tmp_path.resolve()}


def test_dir_path_records_itself(tmp_path):
    paths.addGlobalDir(tmp_path)
    assert common_globals.dirSet == {tmp_path.resolve()}


def test_str_path_is_one_path(tmp_path):
    paths.addGlobalDir(str(tmp_path))
    assert common_globals.dirSet == {tmp_path.resolve()}


def test_unresolvable_path_does_not_raise(tmp_path, monkeypatch):
    # WinError 1005 from Path.resolve on some mapped/virtual drives (#39)
    def boom(self, *a, **k):
        raise OSError(1005, "The volume does not contain a recognized file system")

    monkeypatch.setattr(pathlib.Path, "resolve", boom)
    paths.addGlobalDir(tmp_path)
    paths.addLocalDir(tmp_path / "missing.jpg")
    assert common_globals.dirSet == {tmp_path.absolute()}

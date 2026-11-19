"""Manual key mode without usable CDM files.

Each DRM video used to be downloaded in full, then fail to decrypt with
"Is a directory: '.'" (a blank path) or FileNotFoundError, retried, with a
traceback per attempt. Now the download stops before it starts, with one
plain error saying what to set.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import ofscraper.commands.scraper.actions.download.managers.alt_download as alt_download
import ofscraper.commands.scraper.actions.download.utils.keyhelpers as keyhelpers
from ofscraper.commands.scraper.actions.download.utils.retries import NoRetry


def _settings(monkeypatch, key_mode="manual", client_id="", private_key=""):
    monkeypatch.setattr(
        keyhelpers.settings,
        "get_settings",
        lambda: SimpleNamespace(key_mode=key_mode, client_id=client_id, private_key=private_key),
    )


@pytest.fixture(autouse=True)
def _fresh_log_state(monkeypatch):
    monkeypatch.setattr(keyhelpers, "_logged_cdm_problem", None)


def test_problem_names_each_missing_file(monkeypatch, tmp_path):
    _settings(monkeypatch, client_id="", private_key=tmp_path / "missing.pem")
    problem = keyhelpers.cdm_files_problem()
    assert "client-id is not set" in problem
    assert "private-key file not found" in problem


def test_no_problem_when_both_files_exist(monkeypatch, tmp_path):
    for name in ("client_id.bin", "private_key.pem"):
        (tmp_path / name).write_bytes(b"x")
    _settings(monkeypatch, client_id=tmp_path / "client_id.bin", private_key=tmp_path / "private_key.pem")
    assert keyhelpers.cdm_files_problem() is None
    keyhelpers.check_manual_cdm()


def test_other_key_modes_are_not_checked(monkeypatch):
    _settings(monkeypatch, key_mode="cdrm")
    keyhelpers.check_manual_cdm()


def test_drm_video_is_not_downloaded_and_error_is_logged_once(monkeypatch, caplog):
    _settings(monkeypatch)
    monkeypatch.setattr(alt_download.stop, "started", lambda ele: None)
    monkeypatch.setattr(alt_download.common_logs, "get_medialog", lambda ele: "media")
    placeholders = Mock(side_effect=AssertionError("download started"))
    monkeypatch.setattr(alt_download.placeholder, "Placeholders", placeholders)
    manager = SimpleNamespace(ctx=SimpleNamespace(log=Mock()))

    with caplog.at_level(logging.ERROR, logger="shared"):
        for _ in range(3):
            with pytest.raises(keyhelpers.CdmFilesMissing) as e:
                asyncio.run(
                    alt_download.AltDownloadManager._alt_download_item(
                        manager, None, SimpleNamespace(), "a creator", 1
                    )
                )
            # NoRetry: download_retry gives up at once
            assert isinstance(e.value, NoRetry)

    placeholders.assert_not_called()
    errors = [r for r in caplog.records if "CDM files" in r.getMessage()]
    assert len(errors) == 1

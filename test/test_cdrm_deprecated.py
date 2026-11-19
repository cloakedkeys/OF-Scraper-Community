"""Key mode cdrm is deprecated: it sends the OnlyFans cookies to cdrm-project.com.

New configs default to manual, and a run that still uses cdrm warns on the
console and in the log. A config that already says cdrm keeps it.
"""

import io
import logging
from types import SimpleNamespace

import pytest
from rich.console import Console

import ofscraper.main.open.load  # noqa: F401  (import order, see conftest)
import ofscraper.utils.config.data as data
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.system.network as network


def test_default_key_mode_is_manual(monkeypatch):
    monkeypatch.delenv("OFSC_KEY_DEFAULT", raising=False)
    assert of_env.getattr("KEY_DEFAULT") == "manual"
    assert data.get_key_mode(config={"cdm_options": {}}) == "manual"


def test_a_config_that_says_cdrm_keeps_it():
    assert data.get_key_mode(config={"cdm_options": {"key-mode-default": "cdrm"}}) == "cdrm"


class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def screen(monkeypatch):
    import ofscraper.sites as sites

    monkeypatch.setattr(sites, "is_fansly", lambda: False)
    monkeypatch.setattr(network.time, "sleep", lambda s: None)
    buf = io.StringIO()
    con = Console(file=buf, width=300)
    monkeypatch.setattr(network.console_, "get_console", lambda: con)
    monkeypatch.setattr(network.console_, "get_shared_console", lambda: con)
    handler = _Records()
    shared = logging.getLogger("shared")
    shared.addHandler(handler)
    yield SimpleNamespace(buf=buf, records=handler.records)
    shared.removeHandler(handler)


def _key_mode(monkeypatch, mode):
    monkeypatch.setattr(
        network.settings, "get_settings", lambda: SimpleNamespace(key_mode=mode)
    )


def test_cdrm_warns_on_screen_and_in_the_log(monkeypatch, screen):
    _key_mode(monkeypatch, "cdrm")

    def offline(**kwargs):
        raise OSError("no network in tests")

    monkeypatch.setattr(network.sessionManager, "sessionManager", offline)
    network.check_cdm()
    assert "cdrm is deprecated" in screen.buf.getvalue()
    warnings = [r for r in screen.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "cdrm is deprecated" in warnings[0].getMessage()


def test_manual_does_not_warn(monkeypatch, screen):
    _key_mode(monkeypatch, "manual")
    assert network.check_cdm() is True
    assert "deprecated" not in screen.buf.getvalue()
    assert not [r for r in screen.records if r.levelno >= logging.WARNING]

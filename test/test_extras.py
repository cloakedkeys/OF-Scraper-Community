"""Optional extras installed on first use (``ofscraper.utils.system.extras``).

No package is installed: ``subprocess.run`` is replaced, and conftest sets
``OFSC_INSTALL_EXTRAS=False`` so no other test can start a real install.
"""

import pathlib
import site
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from ofscraper.utils.system import extras

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def runs(monkeypatch):
    """Turn installs on and record each command instead of running it."""
    monkeypatch.setenv("INSTALL_EXTRAS", "True")
    extras.install.cache_clear()
    extras.install_chromium.cache_clear()
    calls = []
    result = {"code": 0, "stderr": ""}

    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=result["code"], stdout="", stderr=result["stderr"])

    monkeypatch.setattr(extras.subprocess, "run", run)
    # whether this venv has pip or uv doesn't matter here
    monkeypatch.setattr(extras, "pip_command", lambda reqs: [sys.executable, "-m", "pip", "install", *reqs])
    yield SimpleNamespace(calls=calls, result=result)
    extras.install.cache_clear()
    extras.install_chromium.cache_clear()


def test_the_extras_match_pyproject():
    with open(ROOT / "pyproject.toml", "rb") as f:
        declared = tomllib.load(f)["project"]["optional-dependencies"]
    for name, requirements in extras.EXTRAS.items():
        assert declared[name] == requirements


def test_tests_never_install_anything():
    assert not extras.enabled()


def test_installs_the_extra_into_the_running_python_once(runs, monkeypatch):
    monkeypatch.setattr(extras, "pip_command", lambda reqs: [sys.executable, "-m", "pip", "install", *reqs])
    assert extras.install("postgres") is True
    assert extras.install("postgres") is True
    assert runs.calls == [[sys.executable, "-m", "pip", "install", "psycopg[binary,pool]>=3.3.9,<3.4"]]


def test_uses_uv_when_this_python_has_no_pip(monkeypatch):
    monkeypatch.setattr(extras.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(extras.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    assert extras.pip_command(["x"]) == ["/bin/uv", "pip", "install", "--python", sys.executable, "x"]


def test_without_pip_or_uv_nothing_runs(runs, monkeypatch, caplog):
    monkeypatch.setattr(extras, "pip_command", lambda reqs: None)
    with caplog.at_level("WARNING", logger="shared"):
        assert extras.install("live") is False
    assert runs.calls == []
    assert "neither pip nor uv" in caplog.text


def test_the_standalone_download_installs_nothing(runs, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert extras.install("live") is False
    assert runs.calls == []


def test_turned_off_installs_nothing(runs, monkeypatch):
    monkeypatch.setenv("INSTALL_EXTRAS", "False")
    assert extras.install("live") is False
    assert extras.install_chromium() is False
    assert runs.calls == []


def test_a_failed_install_says_why_and_is_not_retried(runs, caplog):
    runs.result.update(code=1, stderr="line 1\nERROR: no matching distribution")
    with caplog.at_level("WARNING", logger="shared"):
        assert extras.install("live") is False
        assert extras.install("live") is False
    assert len(runs.calls) == 1  # --live-watch polls again; it shouldn't reinstall each time
    assert "no matching distribution" in caplog.text


def test_a_install_that_cannot_start_is_a_failure(runs, monkeypatch):
    def broken(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(extras.subprocess, "run", broken)
    assert extras.install("live") is False


def test_chromium_is_downloaded_with_this_pythons_playwright(runs):
    assert extras.install_chromium() is True
    assert extras.install_chromium() is True
    assert runs.calls == [[sys.executable, "-m", "playwright", "install", "chromium"]]


@pytest.mark.parametrize("value, on", [(" True ", True), ("1\n", True), (" false", False), ("0", False)])
def test_ofsc_install_extras_ignores_surrounding_spaces(monkeypatch, value, on):
    from ofscraper.utils.of_env.values import system

    monkeypatch.setenv("OFSC_INSTALL_EXTRAS", value)
    assert system.load_system_config()["INSTALL_EXTRAS"] is on


def test_installs_at_the_same_time_run_pip_once(runs, monkeypatch):
    # the postgres driver can be asked for from several threads at once
    import threading
    import time

    started = []

    def slow(cmd, **kwargs):
        started.append(cmd)
        time.sleep(0.3)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(extras.subprocess, "run", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(extras.install("postgres"))) for _ in range(4)]
    threads += [threading.Thread(target=lambda: results.append(extras.install_chromium())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 8
    assert len(started) == 2  # one pip install, one Chromium download


def test_a_user_install_can_be_imported_straight_away(runs, monkeypatch, tmp_path):
    # pip installs with --user when site-packages isn't writable; on a first
    # install the user site directory didn't exist at startup, so it isn't on
    # sys.path yet
    user_site = tmp_path / "user-site"
    monkeypatch.setattr(site, "ENABLE_USER_SITE", True)
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "ofs_user_site_extra", raising=False)

    def user_install(cmd, **kwargs):
        user_site.mkdir()
        (user_site / "ofs_user_site_extra.py").write_text("FOUND = True\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(extras.subprocess, "run", user_install)
    assert extras.install("postgres") is True
    import ofs_user_site_extra

    assert ofs_user_site_extra.FOUND
    monkeypatch.delitem(sys.modules, "ofs_user_site_extra")


def test_a_disabled_user_site_is_not_added(runs, monkeypatch, tmp_path):
    user_site = tmp_path / "user-site"
    user_site.mkdir()
    monkeypatch.setattr(site, "ENABLE_USER_SITE", False)
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert extras.install("postgres") is True
    assert str(user_site) not in sys.path

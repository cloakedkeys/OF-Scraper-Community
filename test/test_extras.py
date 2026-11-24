"""Optional extras installed on first use (``ofscraper.utils.system.extras``).

No package is installed: ``subprocess.run`` is replaced, and conftest sets
``OFSC_INSTALL_EXTRAS=False`` so no other test can start a real install.
"""

import pathlib
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


def test_the_postgres_driver_comes_with_the_scraper_where_it_has_wheels():
    from packaging.requirements import Requirement

    with open(ROOT / "pyproject.toml", "rb") as f:
        project = tomllib.load(f)["project"]
    [driver] = [Requirement(r) for r in project["dependencies"] if r.startswith("psycopg")]
    assert str(driver).split(";")[0] == Requirement(extras.EXTRAS["postgres"][0]).__str__()

    def on(sys_platform, machine):
        return driver.marker.evaluate({"sys_platform": sys_platform, "platform_machine": machine})

    for where in [("win32", "AMD64"), ("darwin", "arm64"), ("darwin", "x86_64"),
                  ("linux", "x86_64"), ("linux", "aarch64")]:
        assert on(*where), where
    # no prebuilt driver: a required one would make the whole install fail there
    for where in [("win32", "ARM64"), ("android", "aarch64"), ("linux", "armv7l")]:
        assert not on(*where), where


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

"""Tests for multi-profile daemon (``--daemon N --profiles a,b``)."""

import json
import signal
import subprocess
import sys
from types import SimpleNamespace

import click
import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.commands.health as health
import ofscraper.commands.scraper.multi_profile as multi_profile
import ofscraper.main.close.exit_status as exit_status
import ofscraper.utils.args.parse.commands.main as main_cmd


def test_build_child_command_strips_daemon_and_profiles_and_adds_profile():
    raw = [
        "--daemon",
        "30",
        "--daemon-jitter=5",
        "--profiles",
        "alpha,beta",
        "--action",
        "download",
        "-u",
        "ALL",
        "--no-rich",
    ]
    cmd = multi_profile.build_child_command("alpha", raw_argv=raw, executable="/usr/bin/python3", frozen=False)
    assert cmd == [
        "/usr/bin/python3",
        "-m",
        "ofscraper",
        "--action",
        "download",
        "-u",
        "ALL",
        "--no-rich",
        "--profile",
        "alpha",
    ]

    # Standalone frozen binary invokes itself directly without `-m ofscraper`
    frozen_cmd = multi_profile.build_child_command(
        "beta",
        raw_argv=["-d", "15", "--profiles=alpha,beta", "-a", "download"],
        executable="/opt/ofscraper",
        frozen=True,
    )
    assert frozen_cmd == ["/opt/ofscraper", "-a", "download", "--profile", "beta"]


def test_children_run_in_order_and_failure_does_not_stop_next(tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("OFSC_STATUS_FILE", str(status_path))

    launched = []
    codes = {"first": int(exit_status.ERROR), "second": int(exit_status.OK), "third": int(exit_status.AUTH)}

    class FakeProc:
        def __init__(self, cmd, env=None):
            self.cmd = cmd
            self.env = env
            self.profile = cmd[-1]
            self.returncode = None
            launched.append((cmd, env))

        def wait(self, timeout=None):
            self.returncode = codes[self.profile]
            return self.returncode

        def poll(self):
            return self.returncode

    raw = ["--daemon", "60", "--profiles", "first,second,third", "--action", "download"]
    results, last_success = multi_profile.run_profiles_pass(
        ["first", "second", "third"], raw_argv=raw, popen=FakeProc
    )

    assert [cmd[-1] for cmd, _ in launched] == ["first", "second", "third"]
    for cmd, env in launched:
        assert "--daemon" not in cmd
        assert "--profiles" not in cmd
        assert "OFSC_STATUS_FILE" not in env

    assert results["first"]["status"] == int(exit_status.ERROR)
    assert results["second"]["status"] == int(exit_status.OK)
    assert results["third"]["status"] == int(exit_status.AUTH)
    assert results["third"]["auth"] == "failed"
    assert last_success is None

    # Status file lists each profile's result and reports auth=failed (health sees unhealthy)
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["mode"] == "daemon"
    assert data["auth"] == "failed"
    assert set(data["profiles"]) == {"first", "second", "third"}
    healthy, reason = health.check_file(status_path)
    assert healthy is False
    assert "login" in reason


def test_stop_signal_reaches_running_child(tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("OFSC_STATUS_FILE", str(status_path))

    received = []

    class FakeProc:
        def __init__(self, cmd, env=None):
            self.cmd = cmd
            self._done = False

        def wait(self, timeout=None):
            if not self._done:
                raise KeyboardInterrupt
            return 130

        def poll(self):
            return 130 if self._done else None

        def send_signal(self, sig):
            received.append(("signal", sig))
            self._done = True

        def terminate(self):
            received.append(("terminate",))
            self._done = True

    args = SimpleNamespace(daemon=10, daemon_jitter=0, profiles=["first", "second"], live_watch=False, profile=None)
    with pytest.raises(KeyboardInterrupt):
        multi_profile.run_multi_profile_daemon(
            args, raw_argv=["--daemon", "10", "--profiles", "first,second"], popen=FakeProc
        )

    if sys.platform == "win32":
        assert received == [("terminate",)]
    else:
        assert received == [("signal", signal.SIGINT)]

    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["state"] == "stopped"
    assert data["next_pass_at"] is None


def test_cli_validation_for_profiles():
    # Valid: --daemon + --profiles preserves order
    ctx = main_cmd.program.make_context("ofscraper", ["--daemon", "30", "--profiles", "zeta,alpha,beta"])
    params, _ = main_cmd.program.invoke(ctx)
    assert params["profiles"] == ["zeta", "alpha", "beta"]

    # Refused: --profiles without --daemon
    ctx_no_daemon = main_cmd.program.make_context("ofscraper", ["--profiles", "a,b"])
    with pytest.raises(click.UsageError, match="--daemon"):
        main_cmd.program.invoke(ctx_no_daemon)

    # Refused: --live-watch with --profiles
    ctx_live = main_cmd.program.make_context("ofscraper", ["--daemon", "30", "--profiles", "a,b", "--live-watch"])
    with pytest.raises(click.UsageError, match="--live-watch"):
        main_cmd.program.invoke(ctx_live)

    # Refused: --profile with --profiles
    ctx_both = main_cmd.program.make_context("ofscraper", ["--daemon", "30", "--profiles", "a,b", "--profile", "a"])
    with pytest.raises(click.UsageError, match="--profile"):
        main_cmd.program.invoke(ctx_both)

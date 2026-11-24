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


# --- review follow-up ---------------------------------------------------------


class _Proc:
    """A child that exits with code once wait() has been asked waits times."""

    def __init__(self, code=0, waits=1, interrupt=None):
        self.code, self.waits, self.interrupt = code, waits, interrupt
        self.returncode = None
        self.signals = []

    def wait(self, timeout=None):
        if self.interrupt is not None:
            exc, self.interrupt = self.interrupt, None
            raise exc
        if self.signals or timeout is None:
            self.waits = 0
        self.waits -= 1
        if self.waits > 0:
            raise subprocess.TimeoutExpired("ofscraper", timeout)
        self.returncode = self.code
        return self.code

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)

    def terminate(self):
        self.signals.append("terminate")

    def kill(self):
        self.signals.append("kill")


def test_heartbeat_is_refreshed_while_a_long_child_runs(tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("OFSC_STATUS_FILE", str(status_path))
    beats = []
    real_write = multi_profile.status_file.write_custom
    monkeypatch.setattr(
        multi_profile.status_file, "write_custom",
        lambda payload, path=None: beats.append(payload["state"]) or real_write(payload, path=path),
    )
    proc = _Proc(code=0, waits=4)  # three heartbeat intervals before it exits
    multi_profile.run_profiles_pass(["first"], raw_argv=[], popen=lambda cmd, env=None: proc)
    # before the child, at its start, three while it ran, and after it
    assert beats.count("running") == 6


def test_parent_status_file_is_not_a_profile_folder(tmp_path, monkeypatch):
    # OFSC_STATUS_FILE unset: a child (main_profile included) writes its own
    # profile's status.json, so the parent must write somewhere else
    import ofscraper.utils.paths.common as common_paths

    monkeypatch.delenv("OFSC_STATUS_FILE", raising=False)
    monkeypatch.setattr(common_paths, "get_config_home", lambda: tmp_path)
    monkeypatch.setattr(common_paths, "get_profile_path", lambda *a: tmp_path / "main_profile")
    multi_profile.run_profiles_pass(
        ["main_profile"], raw_argv=[], popen=lambda cmd, env=None: _Proc(code=0)
    )
    data = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert data["mode"] == "daemon"
    assert not (tmp_path / "main_profile" / "status.json").exists()
    # and ofscraper health finds it without OFSC_STATUS_FILE
    monkeypatch.setenv("HOME", str(tmp_path.parent))
    monkeypatch.setenv("OFSC_CONFIG_DIR", tmp_path.name)
    assert health.default_status_path() == tmp_path / "status.json"


def test_a_failed_login_isnt_forgotten_after_a_later_error(tmp_path, monkeypatch):
    monkeypatch.setenv("OFSC_STATUS_FILE", str(tmp_path / "status.json"))
    results, _ = multi_profile.run_profiles_pass(
        ["first"], raw_argv=[], popen=lambda cmd, env=None: _Proc(code=int(exit_status.AUTH))
    )
    results, _ = multi_profile.run_profiles_pass(
        ["first"], raw_argv=[], results=results,
        popen=lambda cmd, env=None: _Proc(code=int(exit_status.UNAVAILABLE)),
    )
    assert results["first"]["auth"] == "failed"
    assert health.check_file(tmp_path / "status.json")[0] is False


@pytest.mark.parametrize("terminal", [True, False])
def test_ctrl_c_from_the_terminal_isnt_sent_to_the_child_again(tmp_path, monkeypatch, terminal):
    # the terminal sends Ctrl+C to the child as well: a second SIGINT would
    # make it quit at once instead of keeping its .part files
    monkeypatch.setenv("OFSC_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(multi_profile, "_terminal_ctrl_c_reached_child", lambda: terminal)
    proc = _Proc(code=130, interrupt=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        multi_profile.run_profiles_pass(["first"], raw_argv=[], popen=lambda cmd, env=None: proc)
    forwarded = "terminate" if sys.platform == "win32" else signal.SIGINT
    assert proc.signals == ([] if terminal else [forwarded])


def test_sigterm_is_always_passed_on(tmp_path, monkeypatch):
    import ofscraper.utils.context.exit as exit_context

    monkeypatch.setenv("OFSC_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(multi_profile, "_terminal_ctrl_c_reached_child", lambda: True)
    proc = _Proc(code=130, interrupt=exit_context.SigtermInterrupt())
    with pytest.raises(KeyboardInterrupt):
        multi_profile.run_profiles_pass(["first"], raw_argv=[], popen=lambda cmd, env=None: proc)
    assert proc.signals == ["terminate" if sys.platform == "win32" else signal.SIGINT]


def test_daemon_pass_alert_from_the_children_results(tmp_path, monkeypatch):
    import ofscraper.main.close.alerts as alerts

    monkeypatch.setenv("OFSC_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(multi_profile.exit_context, "sigterm_as_keyboard_interrupt", lambda: None)
    outcomes = []
    monkeypatch.setattr(alerts, "after_pass", outcomes.append)
    codes = iter([int(exit_status.PARTIAL), int(exit_status.ERROR)])
    args = SimpleNamespace(daemon=10, daemon_jitter=0, profiles=["a", "b"], live_watch=False, profile=None)
    multi_profile.run_multi_profile_daemon(
        args, raw_argv=[], popen=lambda cmd, env=None: _Proc(code=next(codes)), max_passes=1
    )
    assert outcomes == [exit_status.ERROR]


def test_multi_profile_daemon_shuts_down_like_a_run(monkeypatch):
    import ofscraper.main.open.run as run
    import ofscraper.utils.settings as settings

    monkeypatch.setattr(settings, "get_args", lambda: SimpleNamespace(profiles=["a", "b"]))
    monkeypatch.setattr(multi_profile, "run_multi_profile_daemon", lambda args: None)
    shutdowns = []
    monkeypatch.setattr(run.exit_manager, "shutdown", lambda: shutdowns.append(1))
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0 and shutdowns == [1]


def test_attached_short_option_values_are_stripped():
    cmd = multi_profile.build_child_command(
        "beta", raw_argv=["-d30", "-rfoo", "-dc", "-rd", "-a", "download"],
        executable="/opt/ofscraper", frozen=True,
    )
    assert cmd == ["/opt/ofscraper", "-dc", "-rd", "-a", "download", "--profile", "beta"]

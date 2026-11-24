"""Tests for ``ofscraper health`` (ofscraper/commands/health.py)."""

from datetime import datetime, timedelta, timezone
import json
import pathlib

import pytest

import ofscraper.commands.health as health

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 23, 18, 0, 0, tzinfo=timezone.utc)


def _iso(delta_minutes=0):
    return (NOW + timedelta(minutes=delta_minutes)).isoformat()


def test_missing_status_file_is_healthy(tmp_path):
    healthy, reason = health.check_file(tmp_path / "missing.json", now=NOW)
    assert healthy is True
    assert "no status file" in reason


def test_one_off_run_is_healthy_even_if_old_or_failed_auth():
    data = {
        "version": 1,
        "mode": "once",
        "state": "stopped",
        "heartbeat": _iso(-500),
        "auth": "failed",
    }
    healthy, reason = health.evaluate(data, max_quiet_minutes=60, now=NOW)
    assert healthy is True
    assert "one-off" in reason


def test_running_daemon_with_fresh_heartbeat_is_healthy():
    data = {
        "version": 1,
        "mode": "daemon",
        "state": "running",
        "heartbeat": _iso(-15),
        "auth": "ok",
    }
    healthy, reason = health.evaluate(data, max_quiet_minutes=60, now=NOW)
    assert healthy is True
    assert "running" in reason


def test_running_daemon_with_stale_heartbeat_is_unhealthy():
    data = {
        "version": 1,
        "mode": "daemon",
        "state": "running",
        "heartbeat": _iso(-90),
        "auth": "ok",
    }
    healthy, reason = health.evaluate(data, max_quiet_minutes=60, now=NOW)
    assert healthy is False
    assert "heartbeat" in reason


def test_idle_daemon_within_schedule_is_healthy():
    data = {
        "version": 1,
        "mode": "daemon",
        "state": "idle",
        "heartbeat": _iso(-5),
        "next_pass_at": _iso(25),
        "auth": "ok",
    }
    healthy, reason = health.evaluate(data, max_quiet_minutes=60, now=NOW)
    assert healthy is True
    assert "idle" in reason


def test_idle_daemon_with_overdue_next_pass_is_unhealthy():
    data = {
        "version": 1,
        "mode": "daemon",
        "state": "idle",
        "heartbeat": _iso(-120),
        "next_pass_at": _iso(-75),
        "auth": "ok",
    }
    healthy, reason = health.evaluate(data, max_quiet_minutes=60, now=NOW)
    assert healthy is False
    assert "overdue" in reason


def test_failed_login_is_unhealthy():
    for payload in (
        {"version": 1, "mode": "daemon", "state": "idle", "auth": "failed"},
        {
            "version": 1,
            "mode": "daemon",
            "state": "idle",
            # auth "ok" after it would mean the login was checked again since
            "auth": "unknown",
            "last_pass": {"status": 3, "outcome": "failed"},
        },
        {
            "version": 1,
            "mode": "daemon",
            "state": "idle",
            "auth": "ok",
            "profiles": {"p1": {"status": 0}, "p2": {"status": 3}},
        },
    ):
        healthy, reason = health.evaluate(payload, max_quiet_minutes=60, now=NOW)
        assert healthy is False
        assert "login" in reason


def test_unknown_extra_keys_are_ignored():
    data = {
        "version": 99,
        "mode": "daemon",
        "state": "running",
        "heartbeat": _iso(-5),
        "auth": "ok",
        "future_field": {"nested": [1, 2, 3]},
        "another_unknown": True,
    }
    healthy, _ = health.evaluate(data, max_quiet_minutes=60, now=NOW)
    assert healthy is True


def test_invalid_json_is_unhealthy(tmp_path):
    bad = tmp_path / "status.json"
    bad.write_text("{not json", encoding="utf-8")
    healthy, reason = health.check_file(bad, now=NOW)
    assert healthy is False
    assert "could not read" in reason


def test_cli_reads_arg_and_env_and_exits_0_or_1(tmp_path, monkeypatch, capsys):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "daemon",
                "state": "running",
                "heartbeat": datetime.now(timezone.utc).isoformat(),
                "auth": "ok",
            }
        ),
        encoding="utf-8",
    )
    assert health.main([str(path)]) == 0
    assert "healthy" in capsys.readouterr().out

    # Now mark login failed and read via OFSC_STATUS_FILE
    path.write_text(
        json.dumps({"version": 1, "mode": "daemon", "state": "idle", "auth": "failed"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OFSC_STATUS_FILE", str(path))
    assert health.main([]) == 1
    assert "unhealthy" in capsys.readouterr().out


def test_dockerfile_sets_status_file_env_and_healthcheck():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV OFSC_STATUS_FILE=" in dockerfile
    assert "HEALTHCHECK CMD ofscraper health" in dockerfile


# --- review follow-up ---------------------------------------------------------


def test_recovered_login_is_healthy_while_the_next_pass_runs():
    data = {
        "mode": "daemon", "state": "running", "heartbeat": _iso(-1), "auth": "ok",
        "last_pass": {"status": 3},  # the pass before the login was fixed
    }
    healthy, _ = health.evaluate(data, now=NOW)
    assert healthy is True


def test_idle_daemon_with_no_next_pass_goes_stale():
    data = {"mode": "daemon", "state": "idle", "next_pass_at": None, "auth": "ok"}
    data["heartbeat"] = _iso(-5)
    assert health.evaluate(data, max_quiet_minutes=60, now=NOW)[0] is True
    data["heartbeat"] = _iso(-120)
    healthy, reason = health.evaluate(data, max_quiet_minutes=60, now=NOW)
    assert healthy is False and "heartbeat" in reason


@pytest.mark.parametrize("value", ["-5", "nan", "inf", "0"])
def test_max_quiet_must_be_a_positive_number(value, capsys):
    with pytest.raises(SystemExit) as exc:
        health.main(["--max-quiet", value])
    assert exc.value.code == 2


def test_default_path_follows_main_profile_in_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OFSC_STATUS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OFSC_CONFIG_DIR", raising=False)
    config_home = tmp_path / ".config" / "ofscraper"
    config_home.mkdir(parents=True)
    (config_home / "config.json").write_text(json.dumps({"main_profile": "other"}), encoding="utf-8")
    assert health.default_status_path() == config_home / "other_profile" / "status.json"

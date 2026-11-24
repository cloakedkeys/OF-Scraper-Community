"""Alerts (main/close/alerts.py): sent when something needs a person, once per
change of state, to Discord and alert_script."""

import json
from types import SimpleNamespace

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.main.close.alerts as alerts
import ofscraper.main.close.exit_status as exit_status
import ofscraper.main.close.final.run_record as run_record
import ofscraper.utils.checkers as checkers
import ofscraper.utils.system.free as free
import ofscraper.commands.utils.command as command


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """Alerts as they'd reach Discord and alert_script."""
    script = tmp_path / "alert.py"
    script.write_text("", encoding="utf-8")
    s = SimpleNamespace(discord_level="LOW", alert_script=str(script), site="onlyfans", system_free_min=0)
    monkeypatch.setattr(alerts.settings, "get_settings", lambda: s)
    monkeypatch.setattr(free.settings, "get_settings", lambda: s)
    import ofscraper.utils.profiles.data as profile_data

    monkeypatch.setattr(profile_data, "get_active_profile", lambda: "main_profile")
    out = SimpleNamespace(discord=[], script=[], settings=s)
    monkeypatch.setattr(run_record, "send_discord", lambda text: out.discord.append(text))
    import ofscraper.utils.system.subprocess as subprocess_

    monkeypatch.setattr(subprocess_, "run", lambda cmd, input=None, **k: out.script.append(json.loads(input)))
    alerts.reset()
    exit_status.reset()
    checkers.new_pass()
    yield out
    alerts.reset()
    exit_status.reset()


def test_alert_reaches_discord_and_the_script(sent):
    assert alerts.failing(alerts.LOGIN, "OnlyFans rejected the login; update the auth")
    assert sent.discord == ["⚠️ OnlyFans, main_profile: OnlyFans rejected the login; update the auth"]
    assert sent.script == [{
        "kind": "login", "message": "OnlyFans rejected the login; update the auth",
        "site": "onlyfans", "profile": "main_profile",
    }]


def test_no_discord_when_off_and_no_script_when_unset(sent):
    sent.settings.discord_level = "OFF"
    sent.settings.alert_script = None
    assert alerts.failing(alerts.DISK_FULL, "full")
    assert sent.discord == [] and sent.script == []


def test_summary_level_still_gets_alerts(sent):
    sent.settings.discord_level = "SUMMARY"
    alerts.failing(alerts.LOGIN, "rejected")
    assert len(sent.discord) == 1


def test_daemon_three_failing_passes_give_one_alert_then_one_recovered(sent):
    for _ in range(3):
        alerts.after_pass(exit_status.ERROR)
    assert len(sent.script) == 1 and sent.script[0]["kind"] == "daemon_pass"
    assert "unexpected error" in sent.script[0]["message"]
    alerts.after_pass(exit_status.OK)
    alerts.after_pass(exit_status.OK)
    assert len(sent.script) == 2 and "finishing again" in sent.script[1]["message"]


def test_daemon_partial_pass_is_not_an_alert(sent):
    alerts.after_pass(exit_status.PARTIAL)
    alerts.after_pass(exit_status.OK)
    assert sent.script == []


def test_login_failed_pass_gives_only_the_login_alert(sent):
    alerts.failing(alerts.LOGIN, "rejected")
    alerts.after_pass(exit_status.AUTH)
    assert [a["kind"] for a in sent.script] == ["login"]


def test_free_space_limit_alerts_once(sent, monkeypatch):
    sent.settings.system_free_min = 10
    monkeypatch.setattr(free, "get_free", lambda: 5)
    download = free.space_checker(lambda: "downloaded")
    for _ in range(2):
        with pytest.raises(Exception, match="Space min"):
            download()
    assert [a["kind"] for a in sent.script] == ["disk_full"]
    sent.settings.system_free_min = 0
    assert download() == "downloaded"


def test_free_space_limit_during_a_download_alerts(sent, monkeypatch):
    # the per-file check in the download manager, not only the decorator
    from ofscraper.commands.scraper.actions.download.managers.downloadmanager import (
        DownloadManager,
    )

    sent.settings.system_free_min = 10
    monkeypatch.setattr(free, "get_free", lambda: 5)
    with pytest.raises(Exception):
        DownloadManager(ctx=object())._downloadspace()
    assert [a["kind"] for a in sent.script] == ["disk_full"]


def test_disk_still_full_after_a_partial_pass_isnt_recovered(sent, monkeypatch):
    sent.settings.system_free_min = 10
    free_bytes = [5]
    monkeypatch.setattr(free, "get_free", lambda: free_bytes[0])
    download = free.space_checker(lambda: "downloaded")
    for _ in range(3):  # three daemon passes, the disk full throughout
        with pytest.raises(Exception, match="Space min"):
            download()
        alerts.after_pass(exit_status.PARTIAL)
    assert [a["kind"] for a in sent.script] == ["disk_full"]
    free_bytes[0] = 50  # space was freed
    alerts.after_pass(exit_status.OK)
    assert [a["kind"] for a in sent.script] == ["disk_full", "disk_full"]
    assert "running again" in sent.script[1]["message"]


# --- the login, at the start and mid-run ------------------------------------


def test_rejected_login_at_start_alerts_before_the_prompt(sent, monkeypatch):
    statuses = iter([("DOWN", None), ("UP", None)])
    monkeypatch.setattr(checkers.init, "auth_status", lambda: next(statuses))
    monkeypatch.setattr(checkers.sites, "is_fansly", lambda: False)
    order = []
    monkeypatch.setattr(alerts, "_send", lambda kind, message: order.append(("alert", kind, message)))
    monkeypatch.setattr(
        checkers.make, "make_auth", lambda **k: order.append(("prompt",)) or (None, "continue")
    )
    monkeypatch.setattr(checkers.auth_file, "read_auth", lambda: {})
    checkers.check_auth()
    assert order == [
        ("alert", "login", "OnlyFans rejected the login; update the auth"),
        ("prompt",),
        ("alert", "login", "OnlyFans login works again"),
    ]


class Forbidden(Exception):
    def __init__(self, status):
        super().__init__(f"{status} error")
        self.response = SimpleNamespace(status_code=status)


@pytest.mark.parametrize("status", [401, 403])
def test_creator_failing_with_rejected_login_stops_the_pass(sent, monkeypatch, status):
    asked = []
    monkeypatch.setattr(checkers.init, "auth_status", lambda: asked.append(1) or ("DOWN", None))
    monkeypatch.setattr(checkers.sites, "is_fansly", lambda: False)
    with pytest.raises(checkers.LoginRejected):
        command.log_user_failure("a creator", Forbidden(status))
    assert asked == [1]
    assert exit_status.code() == exit_status.AUTH
    assert [a["kind"] for a in sent.script] == ["login"]


def test_creator_failing_with_a_block_carries_on(sent, monkeypatch):
    asked = []
    monkeypatch.setattr(checkers.init, "auth_status", lambda: asked.append(1) or ("UP", None))
    command.log_user_failure("a creator", Forbidden(403))
    command.log_user_failure("another creator", Forbidden(403))
    assert asked == [1]  # once a pass
    assert sent.script == [] and exit_status.code() == exit_status.PARTIAL


def test_other_failures_dont_ask(sent, monkeypatch):
    monkeypatch.setattr(checkers.init, "auth_status", lambda: pytest.fail("asked"))
    command.log_user_failure("a creator", RuntimeError("timeout"))
    command.log_user_failure("a creator", Forbidden(500))


def test_status_found_through_the_cause():
    try:
        try:
            raise Forbidden(401)
        except Forbidden as e:
            raise RuntimeError("fetch failed") from e
    except RuntimeError as wrapped:
        assert checkers.http_status(wrapped) == 401
    aiohttp_like = Exception("x")
    aiohttp_like.status = 403
    assert checkers.http_status(aiohttp_like) == 403
    assert checkers.http_status(ValueError("x")) is None

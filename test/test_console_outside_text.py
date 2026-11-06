"""On-screen messages print outside text (names, paths, errors) as text,
not as Rich markup, so brackets in it can't crash or restyle the line."""

import io
from types import SimpleNamespace

from rich.console import Console

import ofscraper.main.open.load  # noqa: F401  (import order, see conftest)
import ofscraper.utils.auth.utils.warning.warning as auth_warning
import ofscraper.utils.me as me
import ofscraper.utils.paths.manage as manage
import ofscraper.utils.profiles.tools as profile_tools

TRICKY = "Jo [/b] [red]x[/red]"


def _console():
    buf = io.StringIO()
    return Console(file=buf, width=300, color_system=None, markup=True), buf


def test_welcome_line_with_brackets_in_the_name(monkeypatch):
    con, buf = _console()
    monkeypatch.setattr(me.console, "get_shared_console", lambda: con)
    me.print_user(TRICKY, "user[1]")
    assert f"Welcome, {TRICKY} | user[1]" in buf.getvalue()


def test_deleted_log_file_path_with_brackets(monkeypatch, tmp_path):
    con, buf = _console()
    # "/" isn't allowed in Windows names, so use tags that restyle instead
    folder = tmp_path / "logs [red]"
    folder.mkdir()
    old = folder / "old [bold].log"
    old.write_text("x")
    import os

    os.utime(old, (0, 0))
    monkeypatch.setattr(manage, "console", con)
    monkeypatch.setattr(manage.common_paths, "get_log_folder", lambda: tmp_path)
    monkeypatch.setattr(
        manage.settings, "get_settings", lambda: SimpleNamespace(logs_expire_time=1)
    )
    manage.delete_old_logs()
    manage.delete_empty_folders()
    out = buf.getvalue()
    assert not old.exists()
    assert "old [bold].log" in out
    assert "logs [red]" in out


def test_profile_names_with_brackets(monkeypatch):
    con, buf = _console()
    monkeypatch.setattr(profile_tools, "console", con)
    monkeypatch.setattr(
        profile_tools.profile_data, "get_profile_names", lambda: [TRICKY]
    )
    monkeypatch.setattr(
        profile_tools.profile_data, "get_active_profile", lambda: "a[/x]"
    )
    monkeypatch.setattr(
        profile_tools.profile_data, "get_current_config_profile", lambda: "b[/y]"
    )
    profile_tools.print_profiles()
    out = buf.getvalue()
    assert f"Profile: {TRICKY}" in out
    assert "Active profile: a[/x]" in out
    assert "Default config profile: b[/y]" in out


def test_auth_file_path_with_brackets(monkeypatch):
    con, buf = _console()
    monkeypatch.setattr(auth_warning, "console", con)
    auth_warning.authwarning("C:/cfg [/b]/auth.json")
    assert "C:/cfg [/b]/auth.json" in buf.getvalue()

"""Auth and setup messages show while a screen has quieted the shared console.

The check table, live_tui and a SUPRESS_*_DISPLAY screen set the shared
console's quiet, so plain prints don't draw over them. Auth, auth-repair and
merge messages print through the never-quiet console, so a missing or broken
auth file is still explained there. Other messages honour quiet (#872).
"""

import io
import sys
from types import SimpleNamespace

import pytest

import ofscraper.main.open.load  # noqa: F401  (import order, see conftest)
import ofscraper.utils.args.globals as global_args
import ofscraper.utils.console as console


@pytest.fixture
def screen(monkeypatch):
    """Normal output, both consoles sent to one buffer, shared console quiet."""
    monkeypatch.setattr(sys, "argv", ["ofscraper"])
    monkeypatch.setattr(global_args, "args", None)
    monkeypatch.setattr(console, "shared_console", None)
    monkeypatch.setattr(console, "other_console", None)
    buf = io.StringIO()
    shared = console.get_shared_console()
    for con in (shared, console.get_other_console()):
        con.file = buf
        con.width = 300
    assert console.get_console() is shared  # normal output uses the shared one
    shared.quiet = True  # what a quieting screen does
    return SimpleNamespace(buf=buf, shared=shared)


class _Stop(Exception):
    pass


def _auth_warning(monkeypatch):
    import ofscraper.utils.auth.utils.warning.warning as warning

    warning.authwarning("auth.json")
    return "For an example of how your auth file should look"


def _missing_auth_file(monkeypatch):
    import ofscraper.utils.auth.utils.error as error

    monkeypatch.setattr(error.make, "make_auth", lambda **k: (None, "quit"))
    assert error.handle_auth_errors(FileNotFoundError()) == "quit"
    return "You don't seem to have an `auth.json` file"


def _merge_needs_backup(monkeypatch):
    import ofscraper.utils.merge as merge

    monkeypatch.setattr(merge.postgres, "enabled", lambda: False)
    monkeypatch.setattr(merge.prompts, "backup_prompt_db", lambda: False)
    merge.merge_runner()
    return "waiting for backup confirmation"


def _active_profile(monkeypatch):
    import ofscraper.utils.profiles.tools as tools

    monkeypatch.setattr(tools.profile_data, "get_active_profile", lambda: "main")
    tools.print_current_profile()
    return "Active profile: main"


def _profile_list(monkeypatch):
    import ofscraper.utils.profiles.tools as tools

    monkeypatch.setattr(tools.profile_data, "get_profile_names", lambda: ["creator1"])
    monkeypatch.setattr(tools, "print_current_profile", lambda: None)
    monkeypatch.setattr(tools, "print_default_profile", lambda: None)
    tools.print_profiles()
    return "Profile: creator1"


def _free_trial_note(monkeypatch):
    import ofscraper.prompts.prompt_groups.model as model

    def stop(*a, **k):
        raise _Stop  # the price question that follows the note

    monkeypatch.setattr(model.promptClasses, "batchConverter", stop)
    with pytest.raises(_Stop):
        model.modify_prices_prompt(SimpleNamespace(free_trial=True))
    return "Free trial is True"


@pytest.mark.parametrize(
    "show", [_auth_warning, _missing_auth_file, _merge_needs_backup]
)
def test_auth_and_setup_messages_show_while_quiet(monkeypatch, screen, show):
    text = show(monkeypatch)
    assert text in screen.buf.getvalue()


@pytest.mark.parametrize("show", [_active_profile, _profile_list, _free_trial_note])
def test_other_messages_stay_hidden_while_quiet(monkeypatch, screen, show):
    text = show(monkeypatch)
    assert screen.buf.getvalue() == ""
    # and they do print once the screen lets go of the console
    screen.shared.quiet = None
    show(monkeypatch)
    assert text in screen.buf.getvalue()

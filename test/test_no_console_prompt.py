"""No prompt opens when there's no one to answer it (#286).

With no interactive console (output redirected, a scheduled run, a run started
from another program) a prompt can't open: prompt_toolkit raised
NoConsoleScreenBufferError on Windows and the run ended in a traceback. The
first place a scrape run hit it was the "rescan account for users" question
after a scan that found no creators.
"""

import io
import logging
import queue
from types import SimpleNamespace

import pytest

import ofscraper.main.open.load  # noqa: F401  (import order, see conftest)
import ofscraper.commands.scraper.scraper as scraper
import ofscraper.main.open.run as run
import ofscraper.managers.model as model
import ofscraper.prompts.promptConvert as promptConvert
import ofscraper.prompts.prompt_groups.misc as misc
import ofscraper.utils.interactive as interactive


@pytest.fixture
def records():
    """Collect what the "shared" logger records at ERROR and above."""
    found = []
    handler = logging.Handler(level=logging.ERROR)
    handler.emit = found.append
    logger = logging.getLogger("shared")
    logger.addHandler(handler)
    yield found
    logger.removeHandler(handler)


def _empty_scan_manager(monkeypatch, daemon=None, console=True):
    """A ModelManager whose scans find no creators."""
    mm = model.ModelManager()
    scans = []

    def scan(all_main_models=False):
        scans.append(1)
        mm._last_fetched = []

    monkeypatch.setattr(mm, "_fetch_all_subs_async", scan)
    monkeypatch.setattr(
        model.settings, "get_settings", lambda: SimpleNamespace(daemon=daemon)
    )
    monkeypatch.setattr(interactive, "is_interactive", lambda: console)
    return mm, scans


def _retry_prompt_must_not_open(monkeypatch):
    def fail():
        raise AssertionError("the retry prompt opened")

    monkeypatch.setattr(model.prompts, "retry_user_scan", fail)


# --- the retry prompt (#286's checks) ---------------------------------------


def test_no_console_no_creators_raises_without_prompting(monkeypatch):
    mm, scans = _empty_scan_manager(monkeypatch, console=False)
    _retry_prompt_must_not_open(monkeypatch)
    with pytest.raises(model.NoCreatorsFound) as info:
        mm._fetch_all_subs(force_refetch=True)
    assert scans == [1]
    assert "No creators were found" in str(info.value)
    assert "no interactive console" in str(info.value)


def test_no_console_no_creators_exits_1_with_a_clear_message(monkeypatch, records):
    mm, _ = _empty_scan_manager(monkeypatch, console=False)
    _retry_prompt_must_not_open(monkeypatch)
    monkeypatch.setattr(run, "start_manager", lambda: mm._fetch_all_subs(True))
    monkeypatch.setattr(run.exit_manager, "shutdown", lambda: None)
    with pytest.raises(SystemExit) as exit_info:
        run.main()
    assert exit_info.value.code == 1
    messages = [r.getMessage() for r in records]
    assert any("No creators were found" in m for m in messages)
    # the message, not a traceback
    assert not any("Traceback" in m for m in messages)


def test_console_still_offers_the_retry_prompt(monkeypatch):
    mm, scans = _empty_scan_manager(monkeypatch, console=True)
    answers = iter([True, False])
    asked = []

    def retry():
        asked.append(1)
        return next(answers)

    monkeypatch.setattr(model.prompts, "retry_user_scan", retry)
    mm._fetch_all_subs(force_refetch=True)  # declining still returns normally
    assert asked == [1, 1]
    assert scans == [1, 1]


@pytest.mark.parametrize("console", [True, False])
def test_daemon_never_prompts(monkeypatch, console):
    mm, _ = _empty_scan_manager(monkeypatch, daemon=5, console=console)
    _retry_prompt_must_not_open(monkeypatch)
    with pytest.raises(model.NoCreatorsFound) as info:
        mm._fetch_all_subs(force_refetch=True)
    assert "daemon" in str(info.value)


def test_creators_found_needs_no_prompt(monkeypatch):
    mm, _ = _empty_scan_manager(monkeypatch, console=False)
    _retry_prompt_must_not_open(monkeypatch)

    def scan(all_main_models=False):
        mm._last_fetched = [model.Model({"id": 1, "username": "a"})]

    monkeypatch.setattr(mm, "_fetch_all_subs_async", scan)
    mm._fetch_all_subs(force_refetch=True)
    assert [m.name for m in mm.all_subs] == ["a"]


# --- the daemon ends instead of retrying every pass -------------------------


@pytest.mark.parametrize(
    "error",
    [model.NoCreatorsFound("none"), interactive.NoConsoleError("no console")],
)
def test_daemon_stops_on_errors_that_need_a_person(monkeypatch, error):
    jobs = queue.Queue()
    monkeypatch.setattr(scraper, "jobqueue", jobs)
    monkeypatch.setattr(scraper.exit, "sigterm_as_keyboard_interrupt", lambda: None)
    monkeypatch.setattr(scraper.checkers, "check_auth", lambda: None)
    monkeypatch.setattr(scraper.actions, "select_areas", lambda: None)
    monkeypatch.setattr(scraper.live, "watch_enabled", lambda: False)
    monkeypatch.setattr(scraper, "set_schedule", lambda *a, **k: None)
    monkeypatch.setattr(
        scraper.settings, "get_settings", lambda: SimpleNamespace(output_level="NORMAL")
    )
    monkeypatch.setattr(
        scraper.manager,
        "Manager",
        SimpleNamespace(current_model_manager=SimpleNamespace(sync_models=lambda: None)),
    )

    def runner():
        raise error

    monkeypatch.setattr(scraper, "scraperManager", lambda: SimpleNamespace(runner=runner))

    class Wakeup:
        stopped = False

        def install(self):
            pass

        def restore(self):
            pass

    monkeypatch.setattr(scraper, "DaemonWakeup", Wakeup)
    with pytest.raises(type(error)):
        scraper.daemon_process()


def test_daemon_keeps_going_after_an_ordinary_error(monkeypatch):
    """Other errors still leave the daemon running for its next pass."""
    jobs = queue.Queue()
    monkeypatch.setattr(scraper, "jobqueue", jobs)
    monkeypatch.setattr(scraper.exit, "sigterm_as_keyboard_interrupt", lambda: None)
    monkeypatch.setattr(scraper.checkers, "check_auth", lambda: None)
    monkeypatch.setattr(scraper.actions, "select_areas", lambda: None)
    monkeypatch.setattr(scraper.live, "watch_enabled", lambda: False)
    monkeypatch.setattr(scraper, "set_schedule", lambda *a, **k: None)
    monkeypatch.setattr(
        scraper.settings, "get_settings", lambda: SimpleNamespace(output_level="NORMAL")
    )
    monkeypatch.setattr(
        scraper.manager,
        "Manager",
        SimpleNamespace(current_model_manager=SimpleNamespace(sync_models=lambda: None)),
    )
    logged = []
    monkeypatch.setattr(
        scraper,
        "log",
        SimpleNamespace(info=lambda *a, **k: None, traceback_=logged.append),
    )
    calls = []

    def runner():
        calls.append(1)
        if len(calls) == 1:
            jobs.put(runner)
            raise RuntimeError("a bad pass")
        raise KeyboardInterrupt

    monkeypatch.setattr(scraper, "scraperManager", lambda: SimpleNamespace(runner=runner))

    class Wakeup:
        stopped = False

        def install(self):
            pass

        def restore(self):
            pass

    monkeypatch.setattr(scraper, "DaemonWakeup", Wakeup)
    with pytest.raises(KeyboardInterrupt):
        scraper.daemon_process()
    assert calls == [1, 1]


# --- every other prompt ------------------------------------------------------


def test_prompts_refuse_to_open_without_a_console(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)
    built = []

    @promptConvert.wrapper
    def fake_prompt(*args, **kwargs):
        built.append(1)
        raise AssertionError("the prompt was built")

    with pytest.raises(interactive.NoConsoleError) as info:
        fake_prompt(message="Rescan account for users")
    assert built == []
    assert "Rescan account for users" in str(info.value)


def test_press_enter_refuses_to_wait_without_a_console(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)

    def fail(questions):
        raise AssertionError("the prompt opened")

    monkeypatch.setattr(misc, "prompt", fail)
    with pytest.raises(interactive.NoConsoleError):
        misc.press_enter_to_continue()


def test_no_console_error_exits_1_with_a_clear_message(monkeypatch, records):
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)

    @promptConvert.wrapper
    def fake_prompt(*args, **kwargs):
        raise AssertionError("the prompt was built")

    monkeypatch.setattr(run, "start_manager", lambda: fake_prompt(message="Pick one"))
    monkeypatch.setattr(run.exit_manager, "shutdown", lambda: None)
    with pytest.raises(SystemExit) as exit_info:
        run.main()
    assert exit_info.value.code == 1
    assert any("no interactive console" in r.getMessage() for r in records)


# --- loops that asked again after any error ----------------------------------


class _AskedAgain(BaseException):
    """Escapes the `except Exception` retry loops so the old code can't hang."""


def _no_console_once():
    calls = []

    def ask(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise _AskedAgain
        raise interactive.NoConsoleError("no console")

    return ask


def test_main_menu_stops_instead_of_asking_again(monkeypatch):
    import ofscraper.utils.menu as menu

    monkeypatch.setattr(menu.prompts, "main_prompt", _no_console_once())
    monkeypatch.setattr(
        menu.console, "get_shared_console", lambda: SimpleNamespace(clear_live=lambda: None)
    )
    with pytest.raises(interactive.NoConsoleError):
        menu.main_menu_action()


def test_broken_config_stops_instead_of_asking_again(monkeypatch, tmp_path):
    import ofscraper.utils.config.utils.context as context

    config = tmp_path / "config.json"
    config.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(context.common_paths, "get_config_path", lambda: config)
    monkeypatch.setattr(context.prompts, "reset_config_prompt", _no_console_once())
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    with pytest.raises(interactive.NoConsoleError):
        with context.config_context():
            pass


def test_broken_auth_stops_instead_of_asking_again(monkeypatch):
    import json

    import ofscraper.utils.auth.utils.error as error

    monkeypatch.setattr(error.prompts, "reset_auth_prompt", _no_console_once())
    monkeypatch.setattr(error, "console", SimpleNamespace(print=lambda *a, **k: None))
    with pytest.raises(interactive.NoConsoleError):
        error.handle_auth_errors(json.JSONDecodeError("bad", "{", 0))


# --- is_interactive ----------------------------------------------------------


class _Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return self._tty


@pytest.mark.parametrize(
    "stdin, stdout, expected",
    [
        (_Stream(True), _Stream(True), True),
        (_Stream(False), _Stream(True), False),
        (_Stream(True), _Stream(False), False),
        (None, _Stream(True), False),  # pythonw, or a closed handle
        (_Stream(True), None, False),
    ],
)
def test_is_interactive_needs_both_ends_on_a_terminal(monkeypatch, stdin, stdout, expected):
    monkeypatch.setattr(interactive.sys, "stdin", stdin)
    monkeypatch.setattr(interactive.sys, "stdout", stdout)
    assert interactive.is_interactive() is expected


def test_is_interactive_treats_a_closed_stream_as_no_console(monkeypatch):
    closed = _Stream(True)
    closed.close()
    monkeypatch.setattr(interactive.sys, "stdin", closed)
    monkeypatch.setattr(interactive.sys, "stdout", _Stream(True))
    assert interactive.is_interactive() is False

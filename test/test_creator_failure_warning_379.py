"""A creator's failure shows at normal output, and the run goes on (#379).

The command loops used to log a creator's error only at traceback level, so
at the normal --output level nothing said a creator had failed. That's how
the metadata crash in #376 went unnoticed.

Each test runs the real command loop with two creators, the first of which
fails, and watches the "shared" logger through handlers set to the levels the
console uses.
"""

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest

import ofscraper.commands.scraper.scraper as scraper
import ofscraper.commands.utils.command as command
import ofscraper.utils.logs.utils.level as level


class Collect(logging.Handler):
    def __init__(self, level_):
        super().__init__(level_)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self):
        return [r.getMessage() for r in self.records]


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def reset_sleep(self):
        pass


def creator(name):
    return SimpleNamespace(name=name, id=hash(name) % 1000, avatar=None)


@pytest.fixture
def logs(monkeypatch):
    level.addtraceback()
    log = logging.getLogger("shared")
    normal = Collect(level.getNumber("NORMAL"))
    traces = Collect(logging.TRACEBACK_)
    old_level = log.level
    log.setLevel(1)
    log.addHandler(normal)
    log.addHandler(traces)
    # the progress bars and per-creator banner aren't what's tested here
    monkeypatch.setattr(command.CommandManager, "_data_helper", lambda *_: None)
    monkeypatch.setattr(command.CommandManager, "_avatar_helper", lambda *_: None)
    for name in ("update_user", "update_overall", "update_task"):
        monkeypatch.setattr(command.progress_updater.activity, name, lambda **_: None)
    yield SimpleNamespace(normal=normal, traces=traces)
    log.removeHandler(normal)
    log.removeHandler(traces)
    log.setLevel(old_level)


def fail_first(done):
    async def action(ele=None, **_):
        if ele.name == "first":
            raise ValueError("boom")
        done.append(ele.name)

    return action


def check_failure_shown(logs, what=""):
    warnings = [
        r.getMessage()
        for r in logs.normal.records
        if r.levelno == logging.WARNING
    ]
    assert warnings == [f"[first]{what} failed: ValueError: boom"]
    # the full traceback stays at traceback level only
    tracebacks = [
        r.getMessage() for r in logs.traces.records if r.levelno == logging.TRACEBACK_
    ]
    assert any("Traceback" in t and "boom" in t for t in tracebacks)
    assert not any("Traceback" in m for m in logs.normal.messages())


def test_normal_loop_warns_and_goes_on(logs, monkeypatch):
    async def posts(ele, c=None):
        return SimpleNamespace()

    monkeypatch.setattr(command, "post_media_process", posts)
    done = []
    users = [creator("first"), creator("second")]
    asyncio.run(
        command.CommandManager()._process_users_normal(users, Session(), fail_first(done))
    )
    assert done == ["second"]
    check_failure_shown(logs)


def test_user_first_gather_warns_and_goes_on(logs):
    async def gather(c, ele):
        if ele.name == "first":
            raise ValueError("boom")
        return {ele.id: ele.name}

    users = [creator("first"), creator("second")]
    data = asyncio.run(
        command.CommandManager()._gather_user_first_data(users, Session(), gather)
    )
    assert list(data.values()) == ["second"]
    check_failure_shown(logs)


def test_user_first_actions_warn_and_go_on(logs):
    done = []
    data = {
        n: {"ele": creator(n), "postcollection": None} for n in ("first", "second")
    }
    asyncio.run(
        command.CommandManager()._execute_user_first_actions(data, fail_first(done))
    )
    assert done == ["second"]
    check_failure_shown(logs)


def test_scraper_action_failure_warns_and_goes_on(logs, monkeypatch):
    # the download action raises for the first creator; the scraper's own
    # action step catches it, so the warning must come from there
    done = []

    async def downloader(username=None, **_):
        if username == "first":
            raise ValueError("boom")
        done.append(username)

    async def posts(ele, c=None):
        return SimpleNamespace(
            get_media_for_processing=lambda: [],
            get_posts_to_like=lambda: [],
            get_posts_for_text_download=lambda: [],
        )

    monkeypatch.setattr(command, "post_media_process", posts)
    monkeypatch.setattr(scraper, "downloader", downloader)
    monkeypatch.setattr(
        scraper.progress_utils, "setup_live", lambda *_, **__: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        scraper.settings, "get_settings", lambda: SimpleNamespace(actions=["download"])
    )
    models = SimpleNamespace(
        get_num_scrape_selected_models=lambda: 2,
        mark_as_processed=lambda *_, **__: None,
    )
    stats = SimpleNamespace(update_and_print_stats=lambda *_, **__: None)
    monkeypatch.setattr(
        scraper.manager,
        "Manager",
        SimpleNamespace(current_model_manager=models, stats_manager=stats),
    )
    monkeypatch.setitem(scraper.ACTION_SCRIPTS, "download", lambda *_, **__: None)

    manager = scraper.scraperManager()
    users = [creator("first"), creator("second")]
    asyncio.run(
        manager._process_users_normal(users, Session(), manager._execute_user_action)
    )
    assert done == ["second"]
    check_failure_shown(logs, what=" download")


def test_no_errors_logs_no_warning(logs, monkeypatch):
    async def posts(ele, c=None):
        return SimpleNamespace()

    monkeypatch.setattr(command, "post_media_process", posts)
    done = []
    users = [creator("second"), creator("third")]
    asyncio.run(
        command.CommandManager()._process_users_normal(users, Session(), fail_first(done))
    )
    assert done == ["second", "third"]
    assert logs.normal.records == []

"""A creator's failure shows at normal output, and the run goes on (#379).

The command loops used to log a creator's error only at traceback level, so
at the normal --output level nothing said a creator had failed. That's how
the metadata crash in #376 went unnoticed.

Each test runs the real command loop with two creators, the first of which
fails, or the real daemon loop with a failing pass, and watches the "shared"
logger through handlers set to the levels the console uses. Ctrl+C still
stops every loop at once.
"""

import asyncio
import contextlib
import logging
import queue
from types import SimpleNamespace

import pytest

import ofscraper.commands.scraper.scraper as scraper
import ofscraper.commands.scraper.utils.schedule as daemon_schedule
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


def interrupt_first(done):
    async def action(ele=None, **_):
        if ele.name == "first":
            raise KeyboardInterrupt
        done.append(ele.name)

    return action


def test_ctrl_c_still_stops_every_loop(logs, monkeypatch):
    # Ctrl+C isn't an Exception, so it passes the loops' handlers and stops
    # the run at once, with no "failed" warning
    async def posts(ele, c=None):
        return SimpleNamespace()

    async def gather(c, ele):
        raise KeyboardInterrupt

    monkeypatch.setattr(command, "post_media_process", posts)
    manager = command.CommandManager()
    users = [creator("first"), creator("second")]
    data = {n: {"ele": creator(n), "postcollection": None} for n in ("first", "second")}
    done = []
    runs = [
        manager._process_users_normal(users, Session(), interrupt_first(done)),
        manager._gather_user_first_data(users, Session(), gather),
        manager._execute_user_first_actions(data, interrupt_first(done)),
    ]
    for run in runs:
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(run)
    assert done == []
    assert logs.normal.records == []


class InterruptWhenIdle(queue.Queue):
    # Ctrl+C while the daemon waits for its next job
    def get(self, *args, **kwargs):
        if self.empty():
            raise KeyboardInterrupt
        return super().get(*args, **kwargs)


def test_daemon_pass_failure_warns_and_goes_on(logs, monkeypatch):
    runs = []

    def failing_pass():
        raise ValueError("boom")

    jobs = InterruptWhenIdle()
    jobs.put(failing_pass)
    monkeypatch.setattr(scraper, "jobqueue", jobs)
    monkeypatch.setattr(scraper.exit, "sigterm_as_keyboard_interrupt", lambda: None)
    monkeypatch.setattr(scraper.checkers, "check_auth", lambda: None)
    monkeypatch.setattr(
        scraper, "scraperManager", lambda: SimpleNamespace(runner=lambda: runs.append(1))
    )
    monkeypatch.setattr(
        scraper.settings, "get_settings", lambda: SimpleNamespace(output_level="NORMAL")
    )
    monkeypatch.setattr(
        scraper.manager,
        "Manager",
        SimpleNamespace(current_model_manager=SimpleNamespace(sync_models=lambda: None)),
    )
    monkeypatch.setattr(scraper.actions, "select_areas", lambda: None)
    monkeypatch.setattr(scraper, "set_schedule", lambda *args, **kwargs: None)

    with pytest.raises(KeyboardInterrupt):
        scraper.daemon_process()
    assert runs == [1]  # the next pass still ran
    assert jobs.unfinished_tasks == 0
    warnings = [r.getMessage() for r in logs.normal.records if r.levelno == logging.WARNING]
    assert "Daemon pass failed: ValueError: boom" in warnings
    assert not any("Traceback" in m for m in logs.normal.messages())
    assert any("Traceback" in m for m in logs.traces.messages())


def test_scheduler_failure_warns_and_goes_on(logs, monkeypatch):
    wakeup = daemon_schedule.DaemonWakeup()
    joins = []

    class Jobs:
        def join(self):
            joins.append(1)
            if len(joins) == 1:
                raise ValueError("boom")
            wakeup.stopped = True  # the loop came round again; end the test

    monkeypatch.setattr(daemon_schedule, "jobqueue", Jobs())
    monkeypatch.setattr(
        daemon_schedule.settings, "get_settings", lambda: SimpleNamespace(daemon=60)
    )
    daemon_schedule.set_schedule(lambda: None, wakeup=wakeup)
    assert joins == [1, 1]
    warnings = [r.getMessage() for r in logs.normal.records if r.levelno == logging.WARNING]
    assert warnings == ["Daemon scheduler failed: ValueError: boom"]
    assert any("Traceback" in m for m in logs.traces.messages())


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

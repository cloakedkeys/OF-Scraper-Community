"""The timeline scan range line and its full-rescan hint (#430)."""

from types import SimpleNamespace

import arrow
import pytest

import ofscraper.commands.scraper.utils.prepare as prepare_mod
import ofscraper.data.api.archive as archive
import ofscraper.data.api.timeline as timeline
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.settings as settings
from ofscraper.managers.stats import StatsManager

CLI_HINT = (
    "Hint: Full rescan: add --after 2000"
    " (or --after 2000 --force-all to also re-download files)"
)
MENU_HINT = (
    'Hint: Full rescan: answer Yes to "Would you like to redownload all files"'
    " (this also re-downloads files)"
)
YEAR_2000 = arrow.get("2000").float_timestamp
PARTIAL = arrow.get("2024-03-01").float_timestamp


class Recorder:
    def __init__(self):
        self.lines = []

    def info(self, msg, *args, **kwargs):
        self.lines.extend(str(msg).strip().splitlines())

    def hints(self):
        return [
            line
            for line in self.lines
            if any(word in line.lower() for word in ("hint", "rescan", "--after"))
        ]


@pytest.fixture
def run(monkeypatch):
    """Settings for one run; prepare() starts each run like the scraper does."""
    state = SimpleNamespace(
        settings=SimpleNamespace(
            actions={"download"},
            after=None,
            # no --before: the cutoff is padded past now
            before=arrow.now().shift(days=4),
            before_original=None,
        ),
        stats=StatsManager(),
        log=Recorder(),
    )
    monkeypatch.setattr(settings, "get_settings", lambda: state.settings)
    monkeypatch.setattr(timeline, "log", state.log)
    monkeypatch.setattr(archive, "log", state.log)

    fake_manager = SimpleNamespace(
        Manager=SimpleNamespace(
            session=SimpleNamespace(aget_ofsession=lambda **kwargs: None),
            stats_manager=state.stats,
            current_model_manager=SimpleNamespace(
                prepare_scraper_activity=lambda: []
            ),
        )
    )
    monkeypatch.setattr(prepare_mod, "manager", fake_manager)
    monkeypatch.setattr(prepare_mod, "unique_name_warning", lambda: None)
    monkeypatch.setattr(
        prepare_mod, "profile_tools", SimpleNamespace(print_current_profile=lambda: None)
    )
    monkeypatch.setattr(
        prepare_mod, "init", SimpleNamespace(print_sign_status=lambda: None)
    )
    monkeypatch.setattr(
        prepare_mod,
        "actions",
        SimpleNamespace(select_areas=lambda: None, set_scrape_paid=lambda: None),
    )

    state.start = lambda menu=False: prepare_mod.prepare(menu=menu)
    state.start()
    yield state
    # leave the module state as a fresh command-line run would find it
    state.start()


def test_partial_scan_shows_the_command_line_hint(run):
    timeline.time_log("user", PARTIAL)

    assert run.log.hints() == [f"[yellow]{CLI_HINT}[/yellow]"]


def test_full_scan_shows_no_hint(run):
    timeline.time_log("user", YEAR_2000)
    timeline.time_log("user", 0)

    assert run.log.hints() == []
    assert not any("--after" in line for line in run.log.lines)


def test_menu_run_names_the_menu_choice_not_flags(run):
    run.start(menu=True)

    timeline.time_log("user", PARTIAL)

    assert run.log.hints() == [f"[yellow]{MENU_HINT}[/yellow]"]
    assert not any("--" in line for line in run.log.hints())


def test_like_only_run_shows_no_hint(run):
    for actions in ({"like"}, {"unlike"}, {"like", "unlike"}):
        run.settings.actions = actions
        timeline.time_log("user", PARTIAL)

    assert run.log.hints() == []


def test_start_given_with_after_shows_no_hint(run):
    run.settings.after = arrow.get("2024-03-01")

    timeline.time_log("user", PARTIAL)

    assert run.log.hints() == []


def test_hint_once_per_run_and_again_next_run(run):
    timeline.time_log("first", PARTIAL)
    timeline.time_log("second", PARTIAL)
    assert len(run.log.hints()) == 1

    # a second run in the same menu session
    run.start(menu=True)
    timeline.time_log("first", PARTIAL)
    timeline.time_log("second", PARTIAL)

    assert run.log.hints() == [
        f"[yellow]{CLI_HINT}[/yellow]",
        f"[yellow]{MENU_HINT}[/yellow]",
    ]


@pytest.mark.parametrize(
    "module, area", [(timeline, "timeline"), (archive, "archived")]
)
def test_range_ends_now_without_before(run, module, area):
    module.time_log("user", YEAR_2000)

    start = arrow.get(YEAR_2000).format(of_env.getattr("API_DATE_FORMAT"))
    assert run.log.lines[0] == (
        f"Setting {area} scan range for user from {start} to now"
    )


@pytest.mark.parametrize("module", [timeline, archive])
def test_range_ends_at_the_given_before(run, module):
    before = arrow.get("2025-06-01T12:00:00")
    run.settings.before = before
    run.settings.before_original = before

    module.time_log("user", YEAR_2000)

    end = before.format(of_env.getattr("API_DATE_FORMAT"))
    assert run.log.lines[0].endswith(f" to {end}")

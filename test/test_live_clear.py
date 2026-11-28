"""utils/live/clear.py hides tasks through the updater's managers (#872)."""

import pytest

import ofscraper.main.open.load  # noqa: F401  (import order: see AGENTS.md)
import ofscraper.utils.live.clear as clear
import ofscraper.utils.live.updater as updater

GROUPS = {
    "api": [updater.api.job, updater.api.overall],
    "download": [updater.download.job, updater.download.overall],
    "metadata": [updater.metadata.overall],
    "like": [updater.like.overall],
    "userlist": [updater.userlist.job, updater.userlist.overall],
    "activity": [updater.activity.counter, updater.activity.desc],
}


@pytest.fixture
def tasks():
    added = {
        name: [(progress, progress.add_task(f"{name} test", visible=True)) for progress in bars]
        for name, bars in GROUPS.items()
    }
    yield added
    for pairs in added.values():
        for progress, task_id in pairs:
            progress.remove_task(task_id)


def _visible(progress, task_id):
    return next(t for t in progress.tasks if t.id == task_id).visible


@pytest.mark.parametrize("name", sorted(GROUPS))
def test_clear_by_name_hides_only_that_group(tasks, name):
    clear.clear_tasks_by_name(name.upper())
    for group, pairs in tasks.items():
        for progress, task_id in pairs:
            assert _visible(progress, task_id) is (group != name)


def test_clear_all_hides_every_group(tasks):
    clear.clear_tasks_by_name(["all"])
    assert not any(_visible(p, t) for pairs in tasks.values() for p, t in pairs)


def test_unknown_name_is_refused():
    with pytest.raises(ValueError, match="not a valid task type"):
        clear.clear_tasks_by_name(["api", "nope"])


def test_hide_tasks_swallows_a_failing_update():
    class Broken:
        tasks = [type("T", (), {"id": 1})()]

        def update(self, *a, **k):
            raise KeyError(1)

    updater.hide_tasks(Broken())
    updater.hide_tasks(None)

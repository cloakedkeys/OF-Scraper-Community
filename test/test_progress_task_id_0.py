"""A progress task with id 0 is removed like any other (rich ids start at 0)."""

import asyncio
from contextlib import asynccontextmanager

import pytest

import ofscraper.data.api.highlights as highlights
import ofscraper.data.api.subscriptions.lists as lists
import ofscraper.utils.live.updater as progress_updater


class _Response:
    status = 200

    def __init__(self, data):
        self._data = data

    async def json_(self):
        return self._data


class _Client:
    def __init__(self, data):
        self.data = data

    @asynccontextmanager
    async def requests_async(self, url):
        yield _Response(self.data)


@pytest.fixture
def removed(monkeypatch):
    seen = []
    for manager in (progress_updater.api, progress_updater.userlist):
        monkeypatch.setattr(manager, "add_job_task", lambda *a, **k: 0, raising=False)
        monkeypatch.setattr(manager, "remove_job_task", seen.append, raising=False)
    return seen


def _drain(gen):
    async def run():
        return [batch async for batch in gen]

    return asyncio.run(run())


@pytest.mark.parametrize(
    "scan,data",
    [
        (lambda c: highlights.scrape_stories(c, 1), [{"id": 1}]),
        (lambda c: highlights.scrape_highlights_from_list(c, 1), {"stories": []}),
        (lambda c: lists.scrape_for_list(c), {"list": [], "hasMore": False}),
        (
            lambda c: lists.scrape_list_members(c, {"id": 1, "name": "list1"}),
            {"list": [], "hasMore": False},
        ),
    ],
    ids=["stories", "highlight-stories", "user-lists", "list-members"],
)
def test_task_with_id_0_is_removed(removed, scan, data):
    _drain(scan(_Client(data)))
    assert removed == [0]

"""Offset paging shared by the paid, pinned, highlights and labels scans."""

import asyncio
from contextlib import asynccontextmanager

import pytest

import ofscraper.data.api.common.incomplete as incomplete
import ofscraper.data.api.highlights as highlights
import ofscraper.data.api.labels as labels
import ofscraper.data.api.paid as paid
import ofscraper.data.api.pinned as pinned
import ofscraper.utils.live.updater as progress_utils
from ofscraper.data.api.common.offset_pages import PageEnd, offset_paginate


class _Response:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    async def json_(self):
        return self._data


class _Client:
    """Answers each request with the next page: a dict or list body, an int
    status, or an exception to raise."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.urls = []

    @asynccontextmanager
    async def requests_async(self, url):
        self.urls.append(url)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        if isinstance(page, int):
            yield _Response(page, None)
        else:
            yield _Response(200, page)


def _page(ids, has_more=True):
    return {"list": [{"id": n} for n in ids], "hasMore": has_more}


@pytest.fixture
def tasks(monkeypatch):
    open_tasks = []

    def add(name, **kwargs):
        open_tasks.append(name)
        return name

    monkeypatch.setattr(progress_utils.api, "add_job_task", add, raising=False)
    monkeypatch.setattr(
        progress_utils.api, "remove_job_task", open_tasks.remove, raising=False
    )
    monkeypatch.setattr(paid.log, "traceback_", lambda *a, **k: None, raising=False)
    return open_tasks


@pytest.fixture
def marked(monkeypatch):
    seen = []
    monkeypatch.setattr(
        incomplete, "mark_incomplete", lambda model_id, api, reason: seen.append((api, reason))
    )
    return seen


def _paginate(client, end, **kwargs):
    async def run():
        return [
            batch
            async for batch in offset_paginate(
                client, end, lambda o: f"url?offset={o}", lambda o: f"task {o}", **kwargs
            )
        ]

    return asyncio.run(run())


def test_pages_advance_by_batch_size_until_has_more_is_false(tasks):
    client = _Client(_page([1, 2]), _page([3, 4, 5]), _page([6], has_more=False))
    end = PageEnd()
    assert _paginate(client, end, offset=10) == [
        [{"id": 1}, {"id": 2}],
        [{"id": 3}, {"id": 4}, {"id": 5}],
        [{"id": 6}],
    ]
    assert client.urls == ["url?offset=10", "url?offset=12", "url?offset=15"]
    assert end.finished and end.offset == 15
    assert tasks == []


def test_a_progress_task_with_id_0_is_removed_too(monkeypatch):
    # rich hands out task ids from 0
    removed = []
    monkeypatch.setattr(progress_utils.api, "add_job_task", lambda *a, **k: 0, raising=False)
    monkeypatch.setattr(progress_utils.api, "remove_job_task", removed.append, raising=False)
    _paginate(_Client(_page([1], has_more=False)), PageEnd(), offset=0)
    assert removed == [0]


def test_empty_page_ends_cleanly(tasks):
    end = PageEnd()
    assert _paginate(_Client(_page([1]), _page([])), end) == [[{"id": 1}]]
    assert end.finished


def test_should_stop_ends_cleanly(tasks):
    end = PageEnd()
    client = _Client(_page([1]), _page([2]))
    assert _paginate(client, end, should_stop=lambda batch: True) == [[{"id": 1}]]
    assert end.finished and len(client.urls) == 1


@pytest.mark.parametrize(
    "failure,reason",
    [
        (403, "HTTP 403"),
        (["not", "a", "dict"], "unexpected response format"),
        (OSError("connection reset"), "OSError: connection reset"),
    ],
    ids=["http-error", "bad-format", "exception"],
)
def test_early_stop_is_recorded_with_its_offset(tasks, failure, reason):
    end = PageEnd()
    assert _paginate(_Client(_page([1, 2]), failure), end) == [[{"id": 1}, {"id": 2}]]
    assert not end.finished
    assert end.reason == reason and end.offset == 2 and end.url == "url?offset=2"
    assert end.status == (403 if failure == 403 else None)
    assert isinstance(end.error, OSError) == isinstance(failure, OSError)
    assert tasks == []


def _scan(gen):
    async def run():
        return [batch async for batch in gen]

    return asyncio.run(run())


@pytest.mark.parametrize("area", ["pinned", "highlights"])
@pytest.mark.parametrize(
    "failure,reason",
    [(403, "HTTP 403"), (OSError("boom"), "OSError: boom"), (["x"], None)],
    ids=["http-error", "exception", "bad-format"],
)
def test_pinned_and_highlights_mark_incomplete_except_on_bad_format(
    tasks, marked, area, failure, reason
):
    client = _Client(failure)
    if area == "pinned":
        _scan(pinned.scrape_pinned_posts(client, 1))
        api = pinned.API
    else:
        _scan(highlights.scrape_highlight_list(client, 1))
        api = highlights.API_H
    assert marked == ([(api, reason)] if reason else [])


@pytest.mark.parametrize(
    "failure,reason",
    [
        (403, "HTTP 403"),
        (OSError("boom"), "OSError: boom"),
        (["x"], "unexpected response format"),
    ],
    ids=["http-error", "exception", "bad-format"],
)
def test_label_names_mark_incomplete_on_any_early_stop(tasks, marked, failure, reason):
    _scan(labels.scrape_labels(_Client(failure), 1))
    assert marked == [(labels.API, reason)]


@pytest.mark.parametrize(
    "scan",
    [
        lambda c: pinned.scrape_pinned_posts(c, 1),
        lambda c: highlights.scrape_highlight_list(c, 1),
        lambda c: labels.scrape_labels(c, 1),
    ],
    ids=["pinned", "highlights", "labels"],
)
def test_complete_scan_marks_nothing(tasks, marked, scan):
    assert _scan(scan(_Client(_page([1]), _page([2], has_more=False))))
    assert marked == []


def test_all_paid_worker_stops_once_required_items_are_read(tasks):
    client = _Client(_page([1, 2]), _page([3, 4]), _page([5, 6]))
    assert _scan(paid.scrape_all_paid(client, offset=0, required=3)) == [
        [{"id": 1}, {"id": 2}],
        [{"id": 3}, {"id": 4}],
    ]
    assert len(client.urls) == 2

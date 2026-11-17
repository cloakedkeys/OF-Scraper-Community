"""Fansly repeat runs stop scanning once the rest is already recorded
(sites/fansly/incremental.py).

A Fansly run rescanned the whole timeline and every message each time
unless --after was given: about 365 API calls for a large creator at one
call every 6 seconds, even with nothing new. The page-by-page download now
stops an area at the first page whose posts were all recorded before the
run, once no post with media left to download is older than that page.
These tests run the real pipeline in the harness of test_fansly_pipeline.
"""

import asyncio
from types import SimpleNamespace

import arrow
import pytest

import ofscraper.data.api.common.cache.read as cache_read
import ofscraper.sites.fansly.incremental as incremental
import ofscraper.utils.logs.utils.level as level
from ofscraper.commands.scraper.actions.download import stop

from test_fansly_pipeline import (  # noqa: F401  (settings_stub, harness are fixtures)
    MODEL_ID,
    NAME,
    FakeCache,
    harness,
    page,
    post_dict,
    settings_stub,
)


def asked(harness, area):
    return [e for e in harness.events if e.startswith(f"{area} page")]


def timeline_pages(first_post, count, per_page=2):
    """``count`` pages, newest first: higher ids are newer."""
    pages = []
    pid = first_post
    for _ in range(count):
        pages.append(page(*(post_dict(pid - i, [10_000 + pid - i]) for i in range(per_page))))
        pid -= per_page
    return pages


def new_run(harness):
    harness.events.clear()
    harness.downloads.clear()
    harness.calls.clear()


@pytest.fixture
def repeat(harness):
    """A creator scanned fully once, with three timeline pages and two
    message pages, all downloaded."""
    harness.areas = ["Messages", "Timeline"]
    harness.pages["Timeline"] = timeline_pages(100, 3)
    harness.pages["Messages"] = [
        page(post_dict(60, [20_060], from_user=MODEL_ID)),
        page(post_dict(59, [20_059], from_user=MODEL_ID)),
    ]
    harness.run()
    assert len(asked(harness, "Timeline")) == 3
    assert len(asked(harness, "Messages")) == 2
    harness.downloaded = {10_000 + n for n in range(95, 101)} | {20_060, 20_059}
    new_run(harness)
    return harness


def test_a_repeat_run_with_nothing_new_asks_for_one_page_per_area(repeat):
    repeat.run()
    assert asked(repeat, "Timeline") == ["Timeline page 1"]
    assert asked(repeat, "Messages") == ["Messages page 1"]
    assert repeat.downloads == []
    # stopping early counts as a finished scan
    checked = [args for args, _ in repeat.calls["after check"]]
    assert checked == [(MODEL_ID, "Messages"), (MODEL_ID, "Timeline")]


def test_new_posts_on_the_first_page_scan_to_the_first_known_page(repeat):
    # two new posts push the old ones down a page
    repeat.pages["Timeline"] = [
        page(post_dict(102, [10_102]), post_dict(101, [10_101])),
        *timeline_pages(100, 3),
    ]
    repeat.run()
    assert asked(repeat, "Timeline") == ["Timeline page 1", "Timeline page 2"]
    assert repeat.downloads == [10_102, 10_101]


def test_a_post_with_media_left_keeps_the_scan_going_down_to_it(repeat):
    repeat.pages["Timeline"] = timeline_pages(100, 6)
    repeat.recorded["posts"].update(range(89, 95))
    repeat.downloaded |= {10_000 + n for n in range(89, 95)}
    # post 92 is on page 5 and still has media to download
    repeat.pending = {92}
    repeat.downloaded.discard(10_092)
    repeat.run()
    assert len(asked(repeat, "Timeline")) == 5
    assert repeat.downloads == [10_092]


def test_a_stopped_scan_means_a_full_scan_next_time(repeat):
    async def stop_now(ele):
        stop.request()
        return None

    # a new post: the run is stopped while it downloads
    repeat.pages["Timeline"] = [
        page(post_dict(102, [10_102]), post_dict(101, [10_101])),
        *timeline_pages(100, 3),
    ]
    repeat.areas = ["Timeline"]
    repeat.on_download[10_102] = stop_now
    with pytest.raises(stop.DownloadsStopped):
        repeat.run()
    stop.clear()
    assert repeat.cache[incremental.complete_key(MODEL_ID, "Timeline")] is None

    # the next run scans fully, though page 1 is recorded now
    new_run(repeat)
    repeat.on_download.clear()
    repeat.run()
    assert len(asked(repeat, "Timeline")) == 4

    # and the one after that stops early again
    new_run(repeat)
    repeat.downloaded |= {10_102, 10_101}
    repeat.run()
    assert asked(repeat, "Timeline") == ["Timeline page 1"]


def test_a_scan_that_failed_means_a_full_scan_next_time(repeat):
    level.addtraceback()  # an area failure logs its traceback at this level
    repeat.areas = ["Timeline"]
    new_page = page(post_dict(102, [10_102]), post_dict(101, [10_101]))
    repeat.pages["Timeline"] = [new_page, ValueError("boom")]
    repeat.run()
    assert len(asked(repeat, "Timeline")) == 2
    new_run(repeat)
    repeat.pages["Timeline"] = [new_page, *timeline_pages(100, 3)]
    repeat.run()
    assert len(asked(repeat, "Timeline")) == 4


def test_changed_access_means_a_full_scan(repeat):
    ele = SimpleNamespace(
        name=NAME, id=MODEL_ID, active=True,
        model={"fansly": {"subscribed": True, "following": True, "tierId": "5"}},
    )
    repeat.run(ele=ele)
    assert len(asked(repeat, "Timeline")) == 3  # the first run had other access
    new_run(repeat)
    repeat.run(ele=ele)
    assert asked(repeat, "Timeline") == ["Timeline page 1"]
    new_run(repeat)
    ele.model["fansly"]["tierId"] = "6"
    repeat.run(ele=ele)
    assert len(asked(repeat, "Timeline")) == 3


def test_given_after_scans_fully(repeat):
    repeat.after = 0
    repeat.run()
    assert len(asked(repeat, "Timeline")) == 3


def test_post_ids_scan_fully(repeat, settings_stub):
    settings_stub.post_id = [100]
    repeat.run()
    assert len(asked(repeat, "Timeline")) == 3


def test_the_first_run_scans_fully_and_records_each_area(harness):
    harness.areas = ["Messages", "Timeline", "Purchased"]
    harness.pages["Timeline"] = timeline_pages(100, 2)
    harness.pages["Purchased"] = [page(post_dict(500, [10_500]))]
    harness.run()
    assert set(harness.cache) == {
        incremental.complete_key(MODEL_ID, "Messages"),
        incremental.complete_key(MODEL_ID, "Timeline"),
    }


# ------------------------------------------------------------------ the decision on its own


@pytest.fixture
def decision(monkeypatch, settings_stub):
    """early_stop_for with the real switches; a finished scan with the
    current access is on record."""
    data = {}
    monkeypatch.setattr(incremental, "cache", FakeCache(data))
    monkeypatch.setattr(cache_read, "cache", FakeCache(data))
    settings_stub.incremental_downloads = True
    settings_stub.retry_permanent_skips = False
    ele = SimpleNamespace(name=NAME, id=MODEL_ID, model={"fansly": {"subscribed": True}})
    incremental.scan_finished(ele, "Timeline")

    async def ids(**k):
        return {1, 2}

    async def pending(model_id, username):
        return set()

    monkeypatch.setattr(incremental, "KNOWN_IDS", {"Timeline": ids, "Messages": ids})
    monkeypatch.setattr(incremental, "get_posts_missing_media", pending)

    def decide():
        return asyncio.run(incremental.early_stop_for(ele, "Timeline"))

    return SimpleNamespace(data=data, ele=ele, settings=settings_stub, decide=decide)


def test_with_a_finished_scan_and_no_switch_the_scan_may_stop(decision):
    assert isinstance(decision.decide(), incremental.EarlyStop)


@pytest.mark.parametrize(
    "switch",
    ["after", "incremental off", "full-scan flag", "retry permanent skips", "post id",
     "unfinished scan", "access changed"],
)
def test_each_switch_forces_a_full_scan(decision, switch):
    if switch == "after":
        decision.settings.after = arrow.get(2000, 1, 1)
    elif switch == "incremental off":
        decision.settings.incremental_downloads = False
    elif switch == "full-scan flag":
        decision.data[f"{MODEL_ID}_full_timeline_v2_scrape"] = True
    elif switch == "retry permanent skips":
        decision.settings.retry_permanent_skips = True
    elif switch == "post id":
        decision.settings.post_id = [1]
    elif switch == "unfinished scan":
        incremental.scan_started(MODEL_ID, "Timeline")
    elif switch == "access changed":
        decision.ele.model["fansly"]["subscribed"] = False
    assert decision.decide() is None


def test_other_areas_never_stop_early(decision):
    assert asyncio.run(incremental.early_stop_for(decision.ele, "Purchased")) is None
    assert asyncio.run(incremental.early_stop_for(decision.ele, "Labels")) is None


def test_the_page_rule():
    rule = incremental.EarlyStop(known_ids=[1, 2, 3, 4, 5, "6"], pending_ids=["2", "99"])
    assert rule.after_page([7, 6]) is False  # 7 is new
    assert rule.after_page([6, 5]) is False  # post 2 still has media, further down
    assert rule.after_page([3, 2]) is True  # down to post 2
    assert rule.after_page([]) is False
    # a pending post of another area doesn't hold this one
    assert incremental.EarlyStop([5, 6], ["1"]).after_page([6, 5]) is True

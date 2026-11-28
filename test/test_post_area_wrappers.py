"""The per-area differences of the post.process_* wrappers (#867): which
fetcher runs, the table they save to, which areas filter or set the after
checks, and the label a failure is logged under."""

import asyncio
from types import SimpleNamespace

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
from ofscraper.data.posts import post

MODEL_ID = 101
USERNAME = "creator1"

# wrapper: (module, fetcher, table changes, from_api, set_after_checks,
#           api attribute, responsetype, failure label)
AREAS = {
    "process_paid_post": ("paid", "get_paid_posts", "make_post_table_changes", True, False, "API", "Paid", "Purchased"),
    "process_messages": ("messages", "get_messages", "make_messages_table_changes", True, True, "API", None, "Messages"),
    "process_stories": ("highlights", "get_stories_post", "make_stories_table_changes", False, False, "API_S", "API_S", "Stories"),
    "process_highlights": ("highlights", "get_highlight_post", "make_stories_table_changes", False, False, "API_H", "API_H", "Highlights"),
    "process_timeline_posts": ("timeline", "get_timeline_posts", "make_post_table_changes", True, True, "API", "API", "Timeline"),
    "process_archived_posts": ("archive", "get_archived_posts", "make_post_table_changes", True, True, "API", "API", "Archived"),
    "process_streamed_posts": ("streams", "get_streams_posts", "make_post_table_changes", True, True, "API", "API", "Streams"),
    "process_pinned_posts": ("pinned", "get_pinned_posts", "make_post_table_changes", True, False, "API", None, "Pinned"),
}
SAVERS = {"make_post_table_changes", "make_messages_table_changes", "make_stories_table_changes"}


@pytest.fixture
def harness(monkeypatch):
    h = SimpleNamespace(fetched=[], saved=[], after=[], failures=[], filtered=[])
    monkeypatch.setattr(post.free, "check_free_size", lambda: True)
    monkeypatch.setattr(post.sites, "is_fansly", lambda: False)
    monkeypatch.setattr(
        post.settings, "get_settings", lambda: SimpleNamespace(post_id=[7])
    )
    monkeypatch.setattr(post, "set_after_checks", lambda *a: h.after.append(a))
    monkeypatch.setattr(
        post, "log_area_failure", lambda user, area, e: h.failures.append((user, area, e))
    )

    def keep_first(posts):
        h.filtered.append(len(posts))
        return posts[:1]

    monkeypatch.setattr(post.timeline, "filter_timeline_post", keep_first)
    for name in SAVERS:
        async def save(posts, _name=name, **kwargs):
            h.saved.append((_name, posts, kwargs))

        monkeypatch.setattr(post.operations, name, save)
    for module, fetcher, *_ in AREAS.values():
        async def fetch(*args, _name=f"{module}.{fetcher}", **kwargs):
            h.fetched.append((_name, args, kwargs))
            return [{"id": 1}, {"id": 2}]

        monkeypatch.setattr(getattr(post, module), fetcher, fetch)
    return h


@pytest.mark.parametrize("wrapper", AREAS)
def test_each_area_keeps_its_fetcher_table_and_checks(harness, wrapper):
    module, fetcher, saver, from_api, after, api, responsetype, _ = AREAS[wrapper]
    mod = getattr(post, module)
    posts, returned_api = asyncio.run(getattr(post, wrapper)(MODEL_ID, USERNAME, "c"))

    assert returned_api == getattr(mod, api)
    assert [name for name, *_ in harness.fetched] == [f"{module}.{fetcher}"]
    _, _, fetch_kwargs = harness.fetched[0]
    assert fetch_kwargs["c"] == "c"
    # the areas that take a post id get it from the settings at call time
    takes_post_id = module in {"messages", "timeline", "archive", "streams", "pinned"}
    assert fetch_kwargs.get("post_id") == ([7] if takes_post_id else None)

    expected_type = getattr(mod, responsetype) if responsetype in {"API", "API_S", "API_H"} else responsetype
    assert all(p._responsetype == expected_type for p in posts)
    # only the timeline filters (it drops pinned/archived copies)
    assert harness.filtered == ([2] if module == "timeline" else [])
    assert len(posts) == (1 if module == "timeline" else 2)

    [(saved_by, saved_posts, kwargs)] = harness.saved
    assert saved_by == saver
    assert saved_posts == posts
    assert kwargs == dict(model_id=MODEL_ID, username=USERNAME, **({"from_api": True} if from_api else {}))
    assert harness.after == ([(MODEL_ID, getattr(mod, api))] if after else [])
    assert harness.failures == []


@pytest.mark.parametrize("wrapper", AREAS)
def test_a_failing_fetch_is_logged_under_the_area_label(harness, monkeypatch, wrapper):
    module, fetcher, *_, label = AREAS[wrapper]
    error = RuntimeError("boom")

    async def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(getattr(post, module), fetcher, broken)
    assert asyncio.run(getattr(post, wrapper)(MODEL_ID, USERNAME, None)) is None
    assert harness.failures == [(USERNAME, label, error)]
    assert harness.saved == [] and harness.after == []


@pytest.mark.parametrize("wrapper", AREAS)
def test_each_area_checks_free_space_before_fetching(harness, monkeypatch, wrapper):
    monkeypatch.setattr(post.free, "check_free_size", lambda: False)
    monkeypatch.setattr(post.free, "alert_full", lambda *a: None)
    with pytest.raises(Exception, match="Space min"):
        getattr(post, wrapper)(MODEL_ID, USERNAME, None)
    assert harness.fetched == []


def test_paid_fetcher_takes_username_first_on_onlyfans(harness):
    asyncio.run(post.process_paid_post(MODEL_ID, USERNAME, None))
    [(_, args, _)] = harness.fetched
    assert args == (USERNAME, MODEL_ID)


@pytest.mark.parametrize(
    "wrapper, fetcher",
    [
        ("process_paid_post", "get_paid_posts"),
        ("process_messages", "get_messages"),
        ("process_timeline_posts", "get_timeline_posts"),
    ],
)
def test_the_site_is_chosen_on_each_call(harness, monkeypatch, wrapper, fetcher):
    called = []

    async def fansly(*args, **kwargs):
        called.append(args)
        return []

    monkeypatch.setattr(post.fansly_scrape, fetcher, fansly)
    fansly_now = [False]
    monkeypatch.setattr(post.sites, "is_fansly", lambda: fansly_now[0])
    asyncio.run(getattr(post, wrapper)(MODEL_ID, USERNAME, None))
    fansly_now[0] = True
    asyncio.run(getattr(post, wrapper)(MODEL_ID, USERNAME, None))
    assert len(harness.fetched) == 1  # the OnlyFans fetcher, first call only
    assert called == [(MODEL_ID, USERNAME)]

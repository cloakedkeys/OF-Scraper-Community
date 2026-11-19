"""Check mode with a Fansly profile.

The check commands only knew OnlyFans: with a Fansly profile they looked
creators up on OnlyFans and fetched OnlyFans' timeline, messages and
purchases. Now they find the creator on Fansly and use the Fansly fetchers,
skip the areas Fansly doesn't have, and refuse the stories check.
"""

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest

import ofscraper.commands.check as check

NAMES = ["creator_a", "gone", "creator_b"]
IDS = {"creator_a": 111, "creator_b": 222}


def _not_onlyfans(*args, **kwargs):
    raise AssertionError("an OnlyFans call in a Fansly check")


@pytest.fixture
def fansly_check(monkeypatch):
    fetched = []
    models_asked = []

    def fetch(area):
        async def inner(model_id, username, c=None, post_id=None):
            assert c is not None  # the check's session is passed on
            fetched.append((area, str(model_id), post_id))
            return [{"id": 5, "responseType": "post"}] if post_id else []

        return inner

    async def get_models(usernames=None, c=None):
        models_asked.append((tuple(usernames), c))
        return [
            {"username": n, "id": IDS[n]} for n in usernames if n in IDS
        ]

    async def nothing(*args, **kwargs):
        return None

    @contextlib.asynccontextmanager
    async def fake_session(**kwargs):
        yield object()

    current = SimpleNamespace(
        force=False,
        file=None,
        url=list(NAMES),
        check_usernames=list(NAMES),
        file_usernames=None,
        check_area=["Timeline", "Pinned", "Archived", "Streams"],
        timeline_strict=False,
        command="post_check",
    )
    monkeypatch.setattr(check.settings, "get_settings", lambda: current)
    monkeypatch.setattr(check.sites, "is_fansly", lambda: True)
    monkeypatch.setattr(
        check.manager,
        "Manager",
        SimpleNamespace(session=SimpleNamespace(aget_ofsession=fake_session)),
    )
    for module, name in [
        (check.auth_requests, "make_headers"),
        (check.profile, "get_id"),
        (check.profile, "scrape_profile"),
        (check.timeline, "get_timeline_posts"),
        (check.messages_, "get_messages"),
        (check.paid_, "get_paid_posts"),
        (check.pinned, "get_pinned_posts"),
        (check.archived, "get_archived_posts"),
        (check.streams, "get_streams_posts"),
    ]:
        monkeypatch.setattr(module, name, _not_onlyfans)
    monkeypatch.setattr(check, "read_check", lambda *a: None)
    monkeypatch.setattr(check, "set_check", lambda *a: None)
    for name in ("table_init_create", "make_post_table_changes", "make_messages_table_changes"):
        monkeypatch.setattr(check.operations, name, nothing)
    monkeypatch.setattr(check.fansly_scrape, "get_models", get_models)
    monkeypatch.setattr(check.fansly_scrape, "get_timeline_posts", fetch("timeline"))
    monkeypatch.setattr(check.fansly_scrape, "get_messages", fetch("messages"))
    monkeypatch.setattr(check.fansly_scrape, "get_paid_posts", fetch("paid"))
    return SimpleNamespace(settings=current, fetched=fetched, models_asked=models_asked)


def _collect(retriver):
    async def go():
        return [str(row[1]) async for row in getattr(check, retriver)()]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(go())
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@pytest.mark.parametrize(
    "retriver,areas",
    [
        ("post_check_retriver", {"timeline"}),
        ("message_check_retriver", {"messages", "paid"}),
        ("purchase_check_retriver", {"paid"}),
    ],
)
def test_check_commands_use_fansly(fansly_check, caplog, retriver, areas):
    with caplog.at_level(logging.INFO, logger="shared"):
        yielded = _collect(retriver)
    # both creators, in order; the one Fansly doesn't know is skipped
    assert yielded == ["111", "222"]
    assert "Skipping gone: Fansly has no account by that name" in caplog.text
    assert {area for area, _, _ in fansly_check.fetched} == areas
    assert {model for _, model, _ in fansly_check.fetched} == {"111", "222"}
    # looked up on Fansly with the check's own session
    assert all(c is not None for _, c in fansly_check.models_asked)


def test_post_check_skips_areas_fansly_does_not_have(fansly_check, caplog):
    with caplog.at_level(logging.INFO, logger="shared"):
        _collect("post_check_retriver")
    assert "Fansly has no Pinned, Archived, Streams area; skipped" in caplog.text


def test_stories_check_is_refused_for_fansly(fansly_check, monkeypatch, caplog):
    fansly_check.settings.command = "story_check"
    monkeypatch.setattr(check, "check_auth", _not_onlyfans)
    with caplog.at_level(logging.ERROR, logger="shared"):
        check._checker()
    assert "The stories check is OnlyFans only" in caplog.text


@pytest.mark.parametrize(
    "area,fetcher,post_id",
    [("timeline", "timeline", ["5"]), ("messages", "messages", ["5"]), ("paid", "paid", None)],
)
def test_a_failed_fansly_post_is_fetched_again_from_fansly(fansly_check, area, fetcher, post_id):
    async def go():
        return await check._fansly_refresh_post(area, "5", 111, "creator_a")

    data = asyncio.run(go())
    assert fansly_check.fetched == [(fetcher, "111", post_id)]
    if post_id:
        assert [p["id"] for p in data] == [5]


def test_an_area_without_a_single_post_lookup_falls_back(fansly_check):
    assert asyncio.run(check._fansly_refresh_post("stories", "5", 111, "creator_a")) is None

"""Refresh failed cart posts without scanning the creator's whole history."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import ofscraper.commands.check as check
from ofscraper.commands.scraper.actions.download import stop
from ofscraper.classes.of.media import Media
from test.test_check_cart_batch import batch


def _post(post_id, media_ids, area="post", link="old"):
    return {"id": post_id, "responseType": area, "media": [
        {"id": mid, "type": "photo", "files": {"full": {"url": link}}}
        for mid in media_ids
    ]}


@pytest.fixture
def refresh_batch(monkeypatch, batch):
    stop.clear()
    collection = check.PostCollection(username="a creator", model_id=1)
    collection.add_posts([_post(100, [0, 1]), _post(101, [2]), _post(102, [3])])
    monkeypatch.setitem(check.check_user_dict, "1", {"collection": collection})
    monkeypatch.setattr(check, "insert_media", AsyncMock())
    monkeypatch.setattr(Media, "filename", property(lambda self: f"file{self.id}.jpg"))
    calls = []

    def process(username, model_id, media, posts, on_result):
        if stop.requested.is_set():
            return
        calls.append(list(media))
        for item in media:
            on_result(item, "failed" if len(calls) == 1 and item.id in batch.failed_ids
                      else "downloaded")

    monkeypatch.setattr(check, "process_dicts", process)

    @asynccontextmanager
    async def session(**kwargs):
        yield "session"

    monkeypatch.setattr(check.manager.Manager, "session", SimpleNamespace(aget_ofsession=session), raising=False)
    batch.calls = calls
    batch.collection = collection
    batch.failed_ids = {0, 1}
    yield batch
    stop.clear()


def test_failed_timeline_fetches_post_once_and_retries_new_link(monkeypatch, refresh_batch):
    batch = refresh_batch
    fetch = Mock(return_value=_post(100, [0, 1], link="fresh"))
    monkeypatch.setattr(check, "get_individual_timeline_post", fetch)
    batch.go()
    fetch.assert_called_once_with(100)
    batch.refill.assert_not_called()
    assert [[m.id for m in call] for call in batch.calls] == [[0, 1, 2, 3], [0, 1]]
    assert [m.url for m in batch.calls[1]] == ["fresh", "fresh"]


def test_failed_message_uses_message_fetcher(monkeypatch, refresh_batch):
    batch = refresh_batch
    batch.collection.add_posts([_post(100, [0, 1], "message")], overwrite=True)
    fetch = AsyncMock(return_value=_post(100, [0, 1], "message", "fresh"))
    monkeypatch.setattr(check.messages_, "get_individual_messages_post", fetch)
    batch.go()
    fetch.assert_awaited_once_with(1, 100, "session")
    batch.refill.assert_not_called()
    assert [m.url for m in batch.calls[1]] == ["fresh", "fresh"]


def test_unsupported_area_falls_back_once(monkeypatch, refresh_batch):
    batch = refresh_batch
    batch.collection.add_posts([check.posts_.Post(_post(100, [0, 1]), 1, "a creator", "profile")], overwrite=True)
    batch.go()
    batch.refill.assert_called_once_with(1)
    assert len(batch.calls) == 2


@pytest.mark.parametrize("result", [None, {"error": True}, RuntimeError("fetch failed")])
def test_failed_single_fetch_falls_back_once(monkeypatch, refresh_batch, result):
    batch = refresh_batch
    fetch = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr(check, "get_individual_timeline_post", fetch)
    batch.go()
    fetch.assert_called_once_with(100)
    batch.refill.assert_called_once_with(1)
    assert len(batch.calls) == 2


def test_stop_during_refresh_restores_pending_rows(monkeypatch, refresh_batch):
    batch = refresh_batch
    batch.failed_ids = {0, 1, 2}

    def fetch(post_id):
        stop.request()
        return _post(post_id, [0, 1], link="fresh")

    fetch = Mock(side_effect=fetch)
    monkeypatch.setattr(check, "get_individual_timeline_post", fetch)
    batch.go()
    fetch.assert_called_once_with(100)
    assert len(batch.calls) == 1
    batch.refill.assert_not_called()
    assert batch.updates.call_args_list[-3:] == [
        (("0", "[added]", "bold light_goldenrod2"),),
        (("1", "[added]", "bold light_goldenrod2"),),
        (("2", "[added]", "bold light_goldenrod2"),),
    ]

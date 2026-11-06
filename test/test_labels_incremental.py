import asyncio
from urllib.parse import parse_qs, urlsplit

import ofscraper.data.api.labels as labels


class FakeResponse:
    def __init__(self, data):
        self.status = 200
        self._data = data

    async def json_(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeClient:
    """Serves a label's posts newest first, page_size at a time, by offset."""

    def __init__(self, ids, page_size=5):
        self.posts = [{"id": i} for i in sorted(ids, reverse=True)]
        self.page_size = page_size
        self.offsets = []

    def requests_async(self, url, **kwargs):
        offset = int(parse_qs(urlsplit(url).query)["offset"][0])
        self.offsets.append(offset)
        page = self.posts[offset : offset + self.page_size]
        has_more = offset + self.page_size < len(self.posts)
        return FakeResponse({"list": page, "hasMore": has_more})


LABEL = {"id": 7, "name": "favourites"}


def collect(client, known=None):
    async def run():
        out = []
        extra = {} if known is None else {"known": known}
        async for _, batch in labels.scrape_posts_labels(client, LABEL, 1, **extra):
            out.extend(p["id"] for p in batch)
        return out

    return asyncio.run(run())


def test_first_scan_pages_through_every_post():
    client = FakeClient(range(1, 21))
    assert collect(client) == list(range(20, 0, -1))
    assert client.offsets == [0, 5, 10, 15]


def test_repeat_scan_stops_at_the_first_fully_recorded_page():
    client = FakeClient(range(1, 21))
    known = {str(i) for i in range(1, 21)}
    assert collect(client, known=known) == [20, 19, 18, 17, 16]
    assert client.offsets == [0]


def test_new_posts_are_still_returned_before_stopping():
    # 21-23 were added to the label since the last scan
    client = FakeClient(range(1, 24))
    known = {str(i) for i in range(1, 21)}
    ids = collect(client, known=known)
    assert ids[:3] == [23, 22, 21]
    assert set(range(21, 24)) <= set(ids)
    # page 1: 23-19 (new posts), page 2: 18-14 (all known) -> stop
    assert client.offsets == [0, 5]


def test_known_ids_match_whatever_type_the_database_returns():
    client = FakeClient(range(1, 11))
    assert collect(client, known={str(i) for i in range(1, 11)}) == [10, 9, 8, 7, 6]


def run_known(monkeypatch, prechecks, db_ids, username="creator"):
    monkeypatch.setattr(labels, "get_after_pre_checks", lambda *a, **k: prechecks)

    async def fake_db(label, model_id=None, username=None, **kwargs):
        return db_ids[label.label_id]

    monkeypatch.setattr(labels, "get_all_labels_posts", fake_db)
    return asyncio.run(
        labels.get_known_label_posts(
            [{"id": 7}, {"id": 8}], model_id=1, username=username
        )
    )


def test_known_posts_are_read_per_label(monkeypatch):
    known = run_known(monkeypatch, None, {7: [1, 2], 8: []})
    assert known == {7: {"1", "2"}, 8: set()}


def test_full_scan_requests_ignore_recorded_posts(monkeypatch):
    # --after, incremental off, a full-scan flag or like/unlike, as for the timeline
    assert run_known(monkeypatch, 0, {7: [1], 8: [2]}) == {}


def test_without_a_username_every_label_is_scanned_fully(monkeypatch):
    # e.g. check mode, which calls get_labels without a username
    assert run_known(monkeypatch, None, {7: [1], 8: [2]}, username=None) == {}


def test_labels_are_paged_at_most_two_at_a_time(monkeypatch):
    real_getattr = labels.of_env.getattr
    monkeypatch.setattr(
        labels.of_env,
        "getattr",
        lambda key, *a, **k: 2 if key == "LABELS_CONCURRENCY" else real_getattr(key, *a, **k),
    )
    state = {"active": 0, "peak": 0}

    async def gen(label):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        for page in range(3):
            await asyncio.sleep(0)
            yield label, [{"id": label["id"] * 100 + page}]
        state["active"] -= 1

    label_list = [{"id": i, "name": str(i)} for i in range(1, 6)]
    result = asyncio.run(
        labels.process_tasks_get_posts_for_labels(
            [gen(lab) for lab in label_list], label_list
        )
    )
    assert state["peak"] == 2
    assert sum(len(r["posts"]) for r in result) == 15

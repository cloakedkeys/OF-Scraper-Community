import asyncio
from contextlib import asynccontextmanager
from math import ceil
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import ofscraper.data.api.subscriptions.subscriptions as subscriptions


WORKERS = [
    (subscriptions.scrape_subscriptions_active, "subscriptionsActiveEP"),
    (subscriptions.scrape_subscriptions_disabled, "subscriptionsExpiredEP"),
]


class SubscriptionServer:
    def __init__(self, raw_total, page_size, hidden=()):
        self.raw_total = raw_total
        self.page_size = page_size
        self.hidden = set(hidden)
        self.offsets = []

    @asynccontextmanager
    async def requests_async(self, *, url):
        query = parse_qs(urlsplit(url).query)
        offset = int(query["offset"][0])
        limit = int(query.get("limit", [self.page_size])[0])
        self.offsets.append(offset)

        async def json_():
            return {
                "list": [
                    {"id": item}
                    for item in range(offset, min(offset + limit, self.raw_total))
                    if item not in self.hidden
                ],
                "hasMore": offset + limit < self.raw_total,
            }

        yield SimpleNamespace(status=200, json_=json_)


def collect(worker, server):
    async def run():
        return [item["id"] async for batch in worker(server) for item in batch]

    return asyncio.run(run())


def override_endpoint(monkeypatch, key, endpoint):
    original = subscriptions.of_env.getattr
    monkeypatch.setattr(
        subscriptions.of_env,
        "getattr",
        lambda name, *args, **kwargs: (
            endpoint if name == key else original(name, *args, **kwargs)
        ),
    )


@pytest.mark.parametrize("worker,endpoint_key", WORKERS, ids=["active", "expired"])
@pytest.mark.parametrize("raw_total", [30, 34])
def test_short_pages_advance_by_requested_limit(worker, endpoint_key, raw_total):
    server = SubscriptionServer(raw_total, 10, hidden=range(2, raw_total, 4))

    assert collect(worker, server) == [
        item for item in range(raw_total) if item not in server.hidden
    ]
    assert server.offsets == list(range(0, raw_total, 10))
    assert len(server.offsets) == ceil(raw_total / 10)


@pytest.mark.parametrize("worker,endpoint_key", WORKERS, ids=["active", "expired"])
def test_custom_endpoint_limit(monkeypatch, worker, endpoint_key):
    override_endpoint(
        monkeypatch, endpoint_key, "https://example.test/subscriptions?limit=7&offset={}"
    )
    server = SubscriptionServer(19, 7, hidden=[2, 5, 8, 12, 16])

    assert collect(worker, server) == [
        item for item in range(19) if item not in server.hidden
    ]
    assert server.offsets == [0, 7, 14]


@pytest.mark.parametrize("worker,endpoint_key", WORKERS, ids=["active", "expired"])
def test_custom_endpoint_without_limit(monkeypatch, worker, endpoint_key):
    override_endpoint(
        monkeypatch, endpoint_key, "https://example.test/subscriptions?offset={}"
    )
    server = SubscriptionServer(8, 3)

    assert collect(worker, server) == list(range(8))
    assert server.offsets == [0, 3, 6]


@pytest.mark.parametrize("worker,endpoint_key", WORKERS, ids=["active", "expired"])
def test_empty_page_stops_even_with_has_more(worker, endpoint_key):
    server = SubscriptionServer(21, 10, hidden=range(10))

    assert collect(worker, server) == []
    assert server.offsets == [0]

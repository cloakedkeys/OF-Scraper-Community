"""Fansly: one refused link fetches new links for every waiting file signed
for the same address range.

After the address downloads leave from changes (a VPN switch), the CDN
refuses every link signed before it. Asking per file costs one
``account/media`` call each, at about ten calls a minute; one call carries up
to ``ACCOUNT_MEDIA_BATCH`` ids.

No request is sent anywhere: ``account/media`` is faked. The addresses are
documentation ranges (RFC 5737).
"""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.commands.scraper.actions.download.utils.refresh as refresh
from ofscraper.classes.of.media import Media
from ofscraper.classes.of.posts import Post
from ofscraper.sites.fansly import address, api

OLD, NEW = "192.0.2.0/24", "198.51.100.0/24"


def link(n, signed_for, tag):
    if signed_for is None:
        query = f"Expires=1900000000&Signature=sig-{tag}&Key-Pair-Id=k"
    else:
        policy = {
            "Statement": [
                {
                    "Resource": f"https://cdn3.fansly.com/100/{n}.png",
                    "Condition": {
                        "DateLessThan": {"AWS:EpochTime": 1900000000},
                        "IpAddress": {"AWS:SourceIp": signed_for},
                    },
                }
            ]
        }
        encoded = base64.b64encode(json.dumps(policy).encode()).decode()
        encoded = encoded.replace("+", "-").replace("=", "_").replace("/", "~")
        query = f"Policy={encoded}&Signature=sig-{tag}&Key-Pair-Id=k"
    return f"https://cdn3.fansly.com/100/{n}.png?{query}"


def account_media(n, signed_for, tag):
    """An accountMedia answer for image ``n``."""
    return {
        "id": str(1000 + n),
        "media": {
            "id": str(2000 + n),
            "mimetype": "image/png",
            "locations": [{"locationId": "1", "location": link(n, signed_for, tag)}],
            "variants": [],
        },
    }


def make_ele(n, signed_for=OLD, tag="old"):
    item = account_media(n, signed_for, tag)
    raw = item["media"]
    media = {
        "id": int(raw["id"]),
        "type": "photo",
        "canView": True,
        "files": {"full": {"url": raw["locations"][0]["location"]}},
        "fansly": {
            "accountMediaId": item["id"],
            "mediaId": raw["id"],
            "isPreview": False,
            "media": raw,
        },
    }
    post = Post(
        {"id": 100000000000000005, "postedAt": "2026-01-02T03:04:05+00:00", "text": ""},
        100000000000000002,
        "a-creator",
        responsetype="timeline",
    )
    return Media(media, 0, post)


@pytest.fixture
def fansly(monkeypatch):
    """``account/media``: each id answered with a link signed for ``NEW``
    (or as ``answer`` says), every call recorded."""
    calls = []
    state = SimpleNamespace(calls=calls, answer=None, error=None, gate=None)

    @asynccontextmanager
    async def session(**kwargs):
        yield "session"

    async def fake_account_media(c, ids):
        calls.append([str(i) for i in ids])
        if state.gate is not None:
            await state.gate.wait()
        if state.error:
            raise state.error
        out = []
        for i in ids:
            n = int(i) - 1000
            made = (state.answer or (lambda n: account_media(n, NEW, f"new{len(calls)}")))(n)
            if made is not None:
                out.append(made)
        return out

    async def no_sleep(seconds):
        pass

    address.reset()
    refresh.fansly_refresher.reset()
    monkeypatch.setattr(refresh.fansly_api, "account_media", fake_account_media)
    monkeypatch.setattr(refresh, "FANSLY_ASK_WAIT", 0)
    monkeypatch.setattr(
        refresh.manager, "Manager",
        SimpleNamespace(session=SimpleNamespace(aget_ofsession=session)),
    )
    yield state
    address.reset()
    refresh.fansly_refresher.reset()


def waiting(count, **kwargs):
    items = [make_ele(n, **kwargs) for n in range(count)]
    refresh.fansly_refresher.expect(items)
    return items


async def download_all(items):
    """What the download loop does with each item: the check before the
    download, a 403 when its link is still signed for the old range (the
    CDN refuses it), then ``done``."""
    for ele in items:
        await refresh.fresh_before_download(ele)
        if address._range(ele.url) == OLD:
            assert await refresh.refresh_media_link(ele, link=ele.url)
        refresh.fansly_refresher.done(ele)


def test_one_refusal_among_300_waiting_files_makes_two_calls(fansly):
    items = waiting(300)
    asyncio.run(download_all(items))
    assert [len(call) for call in fansly.calls] == [
        api.ACCOUNT_MEDIA_BATCH,
        300 - api.ACCOUNT_MEDIA_BATCH,
    ]
    assert all(address._range(ele.url) == NEW for ele in items)


def test_a_file_fetched_ahead_downloads_without_a_call_of_its_own(fansly):
    first, second = waiting(2)

    async def go():
        assert await refresh.refresh_media_link(first, link=first.url)
        refresh.fansly_refresher.done(first)
        assert address._range(second.url) == NEW
        await refresh.fresh_before_download(second)

    asyncio.run(go())
    assert fansly.calls == [[first.media["fansly"]["accountMediaId"], "1001"]]


def test_files_signed_for_a_range_that_was_not_refused_are_left_alone(fansly):
    refused = make_ele(0)
    fine = make_ele(1, signed_for=NEW, tag="fine")
    refresh.fansly_refresher.expect([refused, fine])
    before = fine.url
    assert asyncio.run(refresh.refresh_media_link(refused, link=refused.url))
    assert fansly.calls == [["1000"]]
    assert fine.url == before


def test_two_downloads_refused_together_share_one_call(fansly):
    first, second = waiting(2)

    async def go():
        fansly.gate = asyncio.Event()
        one = asyncio.ensure_future(refresh.refresh_media_link(first, link=first.url))
        await asyncio.sleep(0)
        two = asyncio.ensure_future(refresh.refresh_media_link(second, link=second.url))
        await asyncio.sleep(0)
        fansly.gate.set()
        return await asyncio.gather(one, two)

    assert asyncio.run(go()) == [True, True]
    assert len(fansly.calls) == 1


def test_the_same_link_for_one_file_sends_it_alone_to_the_old_loop(fansly):
    stuck, other = waiting(2)

    def answer(n):
        # file 0: the same link every time, until the old loop's last ask
        if n == 0 and len(fansly.calls) < refresh.FANSLY_ASKS:
            return account_media(0, OLD, "old")
        return account_media(n, NEW, f"new{len(fansly.calls)}")

    fansly.answer = answer
    assert asyncio.run(refresh.refresh_media_link(stuck, link=stuck.url))
    # the shared call, then the old loop's remaining asks, for file 0 only
    assert fansly.calls == [["1000", "1001"]] + [["1000"]] * (refresh.FANSLY_ASKS - 1)
    assert address._range(other.url) == NEW
    assert address._range(stuck.url) == NEW


def test_a_failed_call_fails_only_its_waiters_and_leaves_the_rest(fansly):
    first, second = waiting(2)
    fansly.error = RuntimeError("503")
    before = second.url
    assert asyncio.run(refresh.refresh_media_link(first, link=first.url)) is False
    assert second.url == before
    # tried again when reached
    fansly.error = None
    asyncio.run(refresh.fresh_before_download(second))
    assert address._range(second.url) == NEW


def test_a_cancelled_waiter_does_not_cancel_the_call(fansly):
    first, second = waiting(2)

    async def go():
        fansly.gate = asyncio.Event()
        one = asyncio.ensure_future(refresh.refresh_media_link(first, link=first.url))
        await asyncio.sleep(0)
        two = asyncio.ensure_future(refresh.refresh_media_link(second, link=second.url))
        await asyncio.sleep(0)
        one.cancel()
        await asyncio.sleep(0)
        fansly.gate.set()
        return await two

    assert asyncio.run(go()) is True
    assert len(fansly.calls) == 1
    assert address._range(first.url) == NEW


def test_a_link_without_a_range_takes_the_per_file_path(fansly):
    plain = make_ele(0, signed_for=None)
    other = make_ele(1)
    refresh.fansly_refresher.expect([plain, other])
    other_before = other.url
    assert asyncio.run(refresh.refresh_media_link(plain, link=plain.url))
    assert fansly.calls == [["1000"]]
    assert other.url == other_before
    assert not address.is_refused(other.url)


def test_a_finished_file_is_never_fetched_again(fansly):
    first, second, third = waiting(3)
    refresh.fansly_refresher.done(second)
    assert asyncio.run(refresh.refresh_media_link(first, link=first.url))
    assert fansly.calls == [["1000", "1002"]]


def test_onlyfans_items_never_reach_the_refresher(fansly):
    of_item = SimpleNamespace(media={"files": {}}, url=link(9, OLD, "old"))
    refresh.fansly_refresher.expect([of_item])
    assert refresh.fansly_refresher._waiting == {}
    address.refused(of_item.url)
    asyncio.run(refresh.fresh_before_download(of_item))
    assert fansly.calls == []


def test_a_refusal_marks_the_range_and_forgets_its_accepted_mark(fansly):
    old = link(0, OLD, "old")
    address._accepted[OLD] = True
    address.refused(old)
    assert address.is_refused(old)
    assert OLD not in address._accepted
    # without a range, or never seen: nothing changes
    address.refused(link(1, None, "plain"))
    assert not address.is_refused(link(1, None, "plain"))
    assert not address.is_refused(link(2, NEW, "x"))
    assert not address.is_refused(None)


def test_the_cdn_accepting_the_range_again_clears_the_refusal(fansly):
    old = link(0, OLD, "old")
    address.refused(old)

    async def accepts(c, url):
        return True

    async def go():
        original = address._cdn_accepts
        address._cdn_accepts = accepts
        try:
            return await address.usable(None, {"location": link(1, OLD, "new")})
        finally:
            address._cdn_accepts = original

    assert asyncio.run(go()) is True
    assert not address.is_refused(old)


def test_the_url_of_a_refused_request_is_read_from_the_error():
    url = link(0, OLD, "old")
    info = aiohttp.RequestInfo(URL(url), "GET", CIMultiDictProxy(CIMultiDict()), URL(url))
    error = aiohttp.ClientResponseError(info, (), status=403)
    assert address._range(refresh.refused_url(error)) == OLD
    assert refresh.refused_url(RuntimeError("x")) is None

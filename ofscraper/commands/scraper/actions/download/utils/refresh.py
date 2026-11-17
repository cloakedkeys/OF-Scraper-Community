"""Fetch a newly signed CDN link for one media item.

An OnlyFans CDN link carries a CloudFront policy bound to the IP that fetched
it. After an IP change (an ISP reconnect, a VPN switch) every link fetched
before it answers 403 for the rest of the run. Fetching the item again from the
API gives links signed for the current IP.

A normal file gets a new URL. A DRM video keeps its manifest URL but gets a new
signature (``files.drm``), which signs the manifest and segment requests.

Fansly links are signed the same way, with one difference: Fansly sometimes
gives out a link signed for another address, with no IP change on this side.
See ``_refresh_fansly``. And a refused Fansly link gets new links for every
waiting file signed for the same address range in one call
(``FanslyRefresher``).

Based on datawhores/OF-Scraper#653 by danijel1124.
"""

import asyncio
import copy
import logging
import time
from urllib.parse import urlsplit

import ofscraper.classes.of.posts as posts_
import ofscraper.data.api.messages as messages_
import ofscraper.managers.manager as manager
import ofscraper.utils.of_env.of_env as of_env
from ofscraper.data.api.common.timeline import get_individual_timeline_post
from ofscraper.data.api.highlights import get_highlightList
from ofscraper.sites.fansly import address
from ofscraper.sites.fansly import api as fansly_api
from ofscraper.sites.fansly import normalize as fansly_normalize
from ofscraper.sites.fansly import video as fansly_video

log = logging.getLogger("shared")

# how often Fansly is asked for a link that differs from the rejected one, and
# the seconds between asks
FANSLY_ASKS = 3
FANSLY_ASK_WAIT = 5

TIMELINE_AREAS = {"Timeline", "Pinned", "Archived", "Streams"}

# Stories and highlights have no single-item fetch, so a creator's whole list
# is fetched once and kept this long for the other items that fail after the
# same IP change
STORY_CACHE_SECONDS = 300
_story_cache = {}
_story_locks = {}


def _endpoint(ele):
    """How to fetch the item again, or None when it can't be."""
    area = ele.responsetype
    if area in TIMELINE_AREAS:
        return "timeline"
    if area == "Messages":
        return "messages"
    if area == "Stories":
        return "stories"
    if area == "Highlights":
        return "highlights"
    purchase = getattr(ele._post, "purchase_type", None)
    if purchase == "post":
        return "timeline"
    if purchase == "message":
        return "messages"
    return None


async def _get_json(c, url):
    async with c.requests_async(url=url) as r:
        if not (200 <= r.status < 300):
            raise Exception(f"{r.status} for {url}")
        return await r.json_()


async def _active_stories(c, model_id):
    data = await _get_json(c, of_env.getattr("highlightsWithAStoryEP").format(model_id))
    return data if isinstance(data, list) else []


async def _highlight_stories(c, model_id):
    highlight_ids = []
    offset = 0
    while True:
        data = await _get_json(
            c, of_env.getattr("highlightsWithStoriesEP").format(model_id, offset)
        )
        page = get_highlightList(data)
        if not page:
            break
        highlight_ids.extend(page)
        if not data.get("hasMore"):
            break
        offset += len(page)
    stories = []
    for highlight_id in highlight_ids:
        data = await _get_json(c, of_env.getattr("storyEP").format(highlight_id))
        stories.extend(data.get("stories") or [] if isinstance(data, dict) else [])
    return stories


async def _stories_by_id(model_id, area):
    key = (model_id, area)
    # an asyncio.Lock belongs to one event loop, and each run has its own
    lock = _story_locks.setdefault((id(asyncio.get_running_loop()), key), asyncio.Lock())
    async with lock:
        cached = _story_cache.get(key)
        if cached and time.monotonic() - cached[0] < STORY_CACHE_SECONDS:
            return cached[1]
        fetch = _active_stories if area == "stories" else _highlight_stories
        async with manager.Manager.session.aget_ofsession() as c:
            stories = await fetch(c, model_id)
        by_id = {str(s.get("id")): s for s in stories if isinstance(s, dict)}
        _story_cache[key] = (time.monotonic(), by_id)
        return by_id


async def _fetch_post(ele, endpoint):
    if endpoint == "timeline":
        return await asyncio.to_thread(get_individual_timeline_post, ele.post_id)
    if endpoint == "messages":
        async with manager.Manager.session.aget_ofsession() as c:
            return await messages_.get_individual_messages_post(
                ele.model_id, ele.post_id, c
            )
    return (await _stories_by_id(ele.model_id, endpoint)).get(str(ele.post_id))


def _same_file(old_url, new_url):
    # the parts are named before the download starts, so only a link to the
    # same file may continue them
    old, new = urlsplit(old_url), urlsplit(new_url)
    return (old.hostname, old.path) == (new.hostname, new.path)


def _use_new_url(ele, media):
    old_url, new_url = ele.url, media.url
    if not new_url or new_url == old_url:
        return "the API returned no new link"
    if not _same_file(old_url, new_url):
        return "the new link is for a different file, not using it"
    ele.url = new_url
    return None


def _use_new_signature(ele, media):
    old_mpd, new_mpd = ele.mpd, media.mpd if media.protected else None
    if not new_mpd:
        return "the API returned no DRM manifest"
    if not _same_file(old_mpd, new_mpd):
        return "the new manifest is for a different file, not using it"
    old_drm = ele.media.get("files", {}).get("drm") or {}
    new_drm = media.media.get("files", {}).get("drm") or {}
    new_signature = new_drm.get("signature")
    if not new_signature or new_signature == old_drm.get("signature"):
        return "the API returned no new signature"
    # the parsed manifest stays: same file, same segment names
    ele.media.setdefault("files", {})["drm"] = copy.deepcopy(new_drm)
    return None


def _fansly_signatures(raw):
    """The signatures on every location of a raw Fansly Media."""
    out = set()
    for obj in [raw] + list(raw.get("variants") or []):
        for location in obj.get("locations") or []:
            signature = fansly_video.cloudfront_cookies(location).get("CloudFront-Signature")
            if signature:
                out.add(signature)
    return out


SAME_LINK = "Fansly gave the same link again"
THIS_FILE_ONLY = "the CDN accepts another link signed for the same address"
FAILED_EARLIER = "fetching new links for it failed earlier"


def _raw(ele):
    return (ele.media.get("fansly") or {}).get("media")


def _fansly_ids(ele):
    """The accountMedia id to ask for, or None when the item has none."""
    details = ele.media.get("fansly") or {}
    if not details.get("media"):
        return None
    return details.get("accountMediaId")


def _apply_fansly(ele, item):
    """Give ``ele`` the links of ``item``, an ``account/media`` answer.

    Returns None when it did, otherwise why not (``SAME_LINK`` when the
    answer carries no new signature). No network.
    """
    details = ele.media.get("fansly") or {}
    old = details.get("media") or {}
    part = "preview" if details.get("isPreview") else "media"
    new = (item or {}).get(part) or {}
    if str(new.get("id")) != str(old.get("id")):
        return "the API didn't return this media"
    if not _fansly_signatures(new) - _fansly_signatures(old):
        return SAME_LINK
    new_url = fansly_normalize.file_url(new)
    if ele.url and (not new_url or not _same_file(ele.url, new_url)):
        return "the new link is for a different file, not using it"
    # the stream downloader reads its links from the raw Media
    details["media"] = new
    if new_url:
        ele.media.setdefault("files", {}).setdefault("full", {})["url"] = new_url
        ele.url = new_url
    return None


def apply_fansly_item(ele, item) -> bool:
    """True when ``ele`` now has the links of ``item`` (see ``_apply_fansly``)."""
    return _apply_fansly(ele, item) is None


def _answer_for(found, account_media_id):
    return next(
        (x for x in found or [] if str(x.get("id")) == str(account_media_id)), {}
    )


async def _refresh_fansly(ele, asked=0) -> bool:
    """Fansly: fetch the item's accountMedia again and use its links.

    Fansly keeps a signed link for a while and gives the same one to every
    request for that media, sometimes one signed for another address, which
    its CDN refuses (#536). So this asks a few times, until a link changes.
    ``asked``: asks already made elsewhere that gave the same link.
    """
    account_media_id = _fansly_ids(ele)
    if not account_media_id:
        log.debug(f"Media {ele.id}: no way to fetch a new Fansly link")
        return False
    for ask in range(asked, FANSLY_ASKS):
        if ask:
            await asyncio.sleep(FANSLY_ASK_WAIT)
        try:
            async with manager.Manager.session.aget_ofsession() as c:
                found = await fansly_api.account_media(c, [account_media_id])
        except Exception as E:
            log.debug(f"Media {ele.id}: fetching a new link failed: {E}")
            return False
        problem = _apply_fansly(ele, _answer_for(found, account_media_id))
        if problem == SAME_LINK:
            continue
        if problem:
            log.debug(f"Media {ele.id}: {problem}")
            return False
        log.info(
            f"Media {ele.id}: the download link was rejected (403), using a newly signed one"
        )
        return True
    log.info(
        f"Media {ele.id}: Fansly's CDN rejected the download link (403) and Fansly "
        f"gave the same link again {FANSLY_ASKS} times. It may be signed for "
        "another address; the next run tries again"
    )
    return False


def _is_fansly(ele):
    return isinstance(getattr(ele, "media", None), dict) and bool(ele.media.get("fansly"))


class FanslyRefresher:
    """New links for every waiting Fansly item a refusal makes stale.

    When the address downloads leave from changes (a VPN switch, a
    reconnect), the CDN refuses every link signed before it, those of files
    not started yet included. Asking for each file's link on its own costs an
    ``account/media`` call per file, and Fansly allows about ten calls a
    minute; a call costs the same with ``ACCOUNT_MEDIA_BATCH`` ids. So the
    first refusal fetches new links for the waiting items whose links are
    signed for the refused range, one call for many, and the rest find their
    links already replaced.

    One 403 is not proof that a range is dead: a deleted file answers 403
    too. So before a range is marked, one other waiting link signed for it is
    tried on the CDN (one byte). If the CDN accepts it, only this file gets a
    new link, on its own.

    The shared call does not loop. Items it gives a new link are updated;
    items it gives the same link again (Fansly hands out a link it keeps for
    a while) are left to the per-file asks, each on its own, and make no
    further shared call. A failed call is tried once more, and the waiting
    items it was for then make no shared call of their own.

    Items are added with ``expect`` and removed with ``done``; one call runs
    at a time.
    """

    def __init__(self):
        self._waiting = {}
        # id(item) -> its raw media when a shared call gave the same link
        self._stale = {}
        # id(item) -> its raw media when the shared call for it failed
        self._failed = {}
        self._lock = None
        self._loop = None

    def reset(self):
        self._waiting.clear()
        self._stale.clear()
        self._failed.clear()

    def expect(self, items):
        """Items about to be downloaded, in download order."""
        for ele in items:
            if _is_fansly(ele):
                self._waiting[id(ele)] = ele

    def done(self, ele):
        self._waiting.pop(id(ele), None)
        self._stale.pop(id(ele), None)
        self._failed.pop(id(ele), None)

    async def _range_refused(self, ele, link):
        """Whether the range ``link`` is signed for is refused: one other
        waiting link signed for it is tried on the CDN."""
        signed_for = address.link_range(link)
        other = next(
            (
                o.url
                for o in self._waiting.values()
                if o is not ele
                and o.url
                and o.url != link
                and address.link_range(o.url) == signed_for
            ),
            None,
        )
        if other is None:
            return False
        try:
            async with manager.Manager.session.aget_ofsession() as c:
                return await address.cdn_refuses(c, other) is True
        except Exception as E:
            log.debug(f"Media {ele.id}: could not try another link: {type(E).__name__}")
            return False

    async def _call(self, batch):
        """``account/media`` for the batch, tried twice; the address moves at
        most once."""
        error = None
        for attempt in range(2):
            try:
                async with manager.Manager.session.aget_ofsession() as c:
                    return await fansly_api.account_media(
                        c, list(batch), asks=2 if attempt == 0 else 1
                    )
            except Exception as E:
                error = E
        raise error

    def _get_lock(self):
        # an asyncio.Lock belongs to one event loop, and each run has its own
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop, self._lock = loop, asyncio.Lock()
        return self._lock

    async def fresh(self, ele, link=None):
        """Give ``ele`` new links, with the waiting items that need them too.

        ``link``: the link the CDN just refused; None before a download whose
        link is signed for a refused range. Returns None when ``ele`` has new
        links (from this call or one made while it waited), otherwise why
        not: ``SAME_LINK`` when Fansly gave its link again, ``THIS_FILE_ONLY``
        when only this link is refused, ``FAILED_EARLIER`` when the shared
        call for it failed; each of those leaves ``ele`` to the per-file asks.
        """
        before = (ele.media.get("fansly") or {}).get("media")
        # shielded: a cancelled download must not cancel a call that other
        # downloads wait for
        return await asyncio.shield(self._fresh(ele, link, before))

    async def _fresh(self, ele, link, before):
        async with self._get_lock():
            if (ele.media.get("fansly") or {}).get("media") is not before:
                return None
            if link is None and not address.is_refused(ele.url):
                return None
            if (
                link is not None
                and ele.url
                and ele.url != link
                and not address.is_refused(ele.url)
            ):
                # replaced after the refused request was made
                return None
            if link is not None and not address.is_refused(link):
                if not await self._range_refused(ele, link):
                    return THIS_FILE_ONLY
                address.refused(link)
            if before is not None and self._stale.get(id(ele)) is before:
                return SAME_LINK
            if before is not None and self._failed.get(id(ele)) is before:
                return FAILED_EARLIER
            first = _fansly_ids(ele)
            if not first:
                return "no way to fetch a new Fansly link"
            batch = {str(first): [ele]}
            for other in self._waiting.values():
                if len(batch) >= fansly_api.ACCOUNT_MEDIA_BATCH:
                    break
                other_id = _fansly_ids(other)
                if other is ele or not other_id or not address.is_refused(other.url):
                    continue
                batch.setdefault(str(other_id), []).append(other)
            try:
                found = await self._call(batch)
            except Exception as E:
                for items in batch.values():
                    for item in items:
                        if item is not ele:
                            self._failed[id(item)] = _raw(item)
                return f"fetching a new link failed: {E}"
            if not isinstance(found, list):
                found = []
            replaced = 0
            for account_media_id, items in batch.items():
                answer = _answer_for(found, account_media_id)
                for item in items:
                    if item is ele:
                        continue
                    problem = _apply_fansly(item, answer)
                    if problem is None:
                        replaced += 1
                    elif problem == SAME_LINK:
                        self._stale[id(item)] = _raw(item)
            if len(batch) > 1:
                log.debug(
                    f"Fansly: fetched new links for {len(batch) - 1} waiting "
                    f"{'file' if len(batch) == 2 else 'files'} with media "
                    f"{ele.id}, {replaced} replaced"
                )
            return _apply_fansly(ele, _answer_for(found, first))


fansly_refresher = FanslyRefresher()


def refused_url(exception):
    """The URL of the request an HTTP error answered, or None."""
    info = getattr(exception, "request_info", None)
    url = getattr(info, "real_url", None)
    if url is None:
        try:
            url = exception.request.url
        except Exception:
            url = None
    return str(url) if url else None


async def fresh_before_download(ele):
    """Before a Fansly download: when its link is signed for a range the CDN
    refuses, fetch new links first (usually already done by an earlier
    call), so the download doesn't spend a request on a 403."""
    if not _is_fansly(ele) or not address.is_refused(ele.url):
        return
    problem = await fansly_refresher.fresh(ele)
    if problem:
        log.debug(f"Media {ele.id}: its link is signed for a refused address; {problem}")


async def _refresh_fansly_refused(ele, link) -> bool:
    if not address.link_range(link):
        # no address range in the link: ask for this item alone
        return await _refresh_fansly(ele)
    problem = await fansly_refresher.fresh(ele, link)
    if problem == SAME_LINK:
        # the shared call was this item's first ask
        return await _refresh_fansly(ele, asked=1)
    if problem in (THIS_FILE_ONLY, FAILED_EARLIER):
        log.debug(f"Media {ele.id}: {problem}; asking for its link alone")
        return await _refresh_fansly(ele)
    if problem:
        log.debug(f"Media {ele.id}: {problem}")
        return False
    log.info(
        f"Media {ele.id}: the download link was rejected (403), using a newly signed one"
    )
    return True


async def refresh_media_link(ele, link=None) -> bool:
    """Give ``ele`` a newly signed link (or DRM signature) for the same file.

    ``link``: the link the CDN refused, when known. On Fansly a refused link
    signed for an address range also gets new links for the waiting items
    signed for that range (``FanslyRefresher``).

    Returns True if it changed. Any failure is logged and returns False, so the
    download fails as it would have without a refresh.
    """
    if ele.media.get("fansly"):
        if link:
            return await _refresh_fansly_refused(ele, link)
        return await _refresh_fansly(ele)
    protected = ele.protected
    endpoint = _endpoint(ele)
    if not endpoint or not (ele.mpd if protected else ele.url):
        log.debug(f"Media {ele.id}: no way to fetch a new link for {ele.responsetype}")
        return False
    try:
        raw = await _fetch_post(ele, endpoint)
    except Exception as E:
        log.debug(f"Media {ele.id}: fetching a new link failed: {E}")
        return False
    if (
        not isinstance(raw, dict)
        or raw.get("error")
        or str(raw.get("id")) != str(ele.post_id)
    ):
        log.debug(f"Media {ele.id}: the API didn't return item {ele.post_id}")
        return False
    post = posts_.Post(
        raw,
        ele.model_id,
        ele.username,
        ele._post._responsetype,
        purchased=getattr(ele._post, "_purchased", False),
    )
    media = next((m for m in post.all_media if str(m.id) == str(ele.id)), None)
    if not media:
        log.debug(f"Media {ele.id}: the API returned the item without this media")
        return False
    problem = (_use_new_signature if protected else _use_new_url)(ele, media)
    if problem:
        log.debug(f"Media {ele.id}: {problem}")
        return False
    log.info(
        f"Media {ele.id}: the download link was rejected (403), using a newly signed one"
    )
    return True

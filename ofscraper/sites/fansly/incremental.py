"""Fansly repeat runs: stop scanning once the rest is already recorded.

A Fansly run rescanned the creator's whole timeline and messages every time
unless ``--after`` was given, at the API's limit of about one call every
6 seconds. Pages come newest first, and Fansly post and message ids grow with
time (paging relies on it: ``before=<last id>``). So once a page holds only
posts recorded before this run, and no post with media still to download is
older than that page, the older pages hold nothing this run would download.

Only the page-by-page download (``pipeline``) uses this. A full scan happens
when any of these is true (``early_stop_for``):

- the incremental switches every other scan uses: ``--after``, incremental
  downloads off, the full-scan flag from an earlier ``--after`` run,
  ``--retry-permanent-skips``;
- ``--post-id``;
- the area's last scan didn't finish: an interrupted run saved rows for the
  newest pages only, so the newest posts look recorded while older ones were
  never seen. The mark is cleared when a scan starts and set again only after
  the scan reached its end (or stopped by this rule) and the run finished;
- the account's access to the creator changed (subscribed, following, tier)
  since that scan, so older locked posts may now be viewable.

Media added later to an already recorded post is only found by a full scan
(``--after 2000``).
"""

import logging

import ofscraper.utils.cache.cache as cache
import ofscraper.utils.settings as settings
from ofscraper.data.api.common.after import get_after_pre_checks
from ofscraper.data.api.common.pending import get_posts_missing_media
from ofscraper.db.operations_.messages import get_all_messages_ids
from ofscraper.db.operations_.posts import get_all_post_ids

log = logging.getLogger("shared")

AREAS = ("Timeline", "Messages")
KNOWN_IDS = {"Timeline": get_all_post_ids, "Messages": get_all_messages_ids}


def complete_key(model_id, area):
    return f"fansly_scan_complete_v1_{model_id}_{area.lower()}"


def access(ele):
    """What decides which posts the account can see."""
    model = getattr(ele, "model", None)
    fansly = (model.get("fansly") if isinstance(model, dict) else None) or {}
    return [
        bool(fansly.get("subscribed")),
        bool(fansly.get("following")),
        str(fansly.get("tierId") or ""),
    ]


def scan_started(model_id, area):
    """Until the scan finishes, the next run scans the area fully."""
    if area in AREAS:
        cache.set(complete_key(model_id, area), None)


def scan_finished(ele, area):
    """The area's scan reached its end (or stopped early) and its rows are saved."""
    if area in AREAS:
        cache.set(complete_key(ele.id, area), {"access": access(ele)})


def _ids(values):
    out = set()
    for value in values or []:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


class EarlyStop:
    """Decides, page by page, whether the rest of an area can be skipped."""

    def __init__(self, known_ids, pending_ids):
        self.known = _ids(known_ids)
        # only this area's posts: other areas share the media table
        self.pending = _ids(pending_ids) & self.known

    def after_page(self, page_ids) -> bool:
        """True: stop paging this area."""
        ids = _ids(page_ids)
        if not ids or not ids <= self.known:
            return False
        oldest = min(ids)
        return not any(pending < oldest for pending in self.pending)


async def early_stop_for(ele, area):
    """An ``EarlyStop`` for the area, or None for a full scan.

    Call it before the run writes any rows: rows from this run would make
    every page look recorded.
    """
    if area not in AREAS:
        return None
    model_id, username = ele.id, ele.name
    if get_after_pre_checks(model_id, area) is not None:
        return None
    if settings.get_settings().post_id:
        return None
    mark = cache.get(complete_key(model_id, area))
    if not isinstance(mark, dict):
        log.debug(f"{area}: the last scan didn't finish, scanning fully")
        return None
    if mark.get("access") != access(ele):
        log.debug(f"{area}: access to {username} changed, scanning fully")
        return None
    known = await KNOWN_IDS[area](model_id=model_id, username=username)
    pending = await get_posts_missing_media(model_id, username)
    return EarlyStop(known, pending)

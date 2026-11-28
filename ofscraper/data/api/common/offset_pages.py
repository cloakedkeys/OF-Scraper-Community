"""Offset paging shared by the paid, pinned, highlights and labels scans.

Each of these asks for a page at an offset, yields its items, and asks again
at ``offset + len(items)`` while the response says ``hasMore``. They differ in
the endpoint, the progress text, where the items sit in the response, and how
an early stop is reported. ``offset_paginate`` takes the first three as
arguments and records how paging ended on a ``PageEnd``; each caller reads it
after the loop and does its own reporting (``incomplete.mark_incomplete``,
paid's "Purchases may be incomplete" warning, labels' finished mark).
"""

import logging
import traceback
from dataclasses import dataclass

import ofscraper.utils.live.updater as progress_utils

log = logging.getLogger("shared")


@dataclass
class PageEnd:
    # offset and url of the last page asked for
    offset: int = 0
    url: str | None = None
    # the non-2xx status that stopped the scan
    status: int | None = None
    # what a request raised
    error: Exception | None = None
    # why the scan stopped early: "HTTP 403", "unexpected response format",
    # or "OSError: ..."; None when it reached the end or should_stop ended it
    reason: str | None = None

    @property
    def finished(self):
        return self.reason is None


def first_list(data):
    """The response's first list value, which is where most endpoints put
    their items."""
    lists = [x for x in data.values() if isinstance(x, list)]
    return lists[0] if lists else []


async def offset_paginate(
    c,
    end,
    url,
    task_name,
    offset=0,
    *,
    items=first_list,
    on_page=None,
    should_stop=None,
):
    """Yield each non-empty page of items, starting at ``offset``.

    ``url(offset)`` and ``task_name(offset)`` give a page's address and its
    progress text. ``items(data)`` picks the items out of a response, and
    ``on_page(data, batch)`` is called for every response, empty pages
    included, before its items are yielded. Paging ends at an empty page, at
    ``hasMore`` false, or when ``should_stop(batch)`` is true; any other stop
    is recorded on ``end``.
    """
    end.offset = offset
    while True:
        end.url = url(end.offset)
        task = None
        try:
            task = progress_utils.api.add_job_task(task_name(end.offset), visible=True)
            async with c.requests_async(url=end.url) as r:
                if not (200 <= r.status < 300):
                    end.status = r.status
                    end.reason = f"HTTP {r.status}"
                    return
                data = await r.json_()
                if not isinstance(data, dict):
                    end.reason = "unexpected response format"
                    return
                batch = items(data)
                if on_page:
                    on_page(data, batch)
                if not batch:
                    return
                yield batch
                if not data.get("hasMore") or (should_stop and should_stop(batch)):
                    return
                end.offset += len(batch)
        except Exception as E:
            end.error = E
            end.reason = f"{type(E).__name__}: {E}"
            log.traceback_(E)
            log.traceback_(traceback.format_exc())
            return
        finally:
            # rich task ids start at 0
            if task is not None:
                progress_utils.api.remove_job_task(task)

r"""

 _______  _______         _______  _______  _______  _______  _______  _______  _______
(  ___  )(  ____ \       (  ____ \(  ____ \(  ____ )(  ___  )(  ____ )(  ____ \(  ____ )
| (   ) || (    \/       | (    \/| (    \/| (    )|| (   ) || (    )|| (    \/| (    )|
| |   | || (__     _____ | (_____ | |      | (____)|| (___) || (____)|| (__    | (____)|
| |   | ||  __)   (_____)(_____  )| |      |     __)|  ___  ||  _____)|  __)   |     __)
| |   | || (                   ) || |      | (\ (   | (   ) || (      | (      | (\ (
| (___) || )             /\____) || (____/\| ) \ \__| )   ( || )      | (____/\| ) \ \__
(_______)|/              \_______)(_______/|/   \__/|/     \||/       (_______/|/   \__/

"""

import contextlib
import logging

import ofscraper.data.api.common.incomplete as incomplete
import ofscraper.data.api.common.logs.strings as common_logs
import ofscraper.utils.of_env.of_env as of_env
from ofscraper.commands.scraper.actions.download import stop
from ofscraper.data.api.common.logs.logs import trace_log_raw, trace_progress_log
from ofscraper.data.api.common.offset_pages import PageEnd, offset_paginate
from ofscraper.data.api.tasks import collect_pages
from ofscraper.utils.context.run_async import run

log = logging.getLogger("shared")
API = "Pinned"


@run
@stop.fetching
async def get_pinned_posts(model_id, c=None, post_id=None):
    post_id = post_id or []
    # 1. build the list of raw generators
    generators = [scrape_pinned_posts(c, model_id)]

    # 2. process them through the Queue engine
    data = await process_tasks_batch(generators)
    return data


async def process_tasks_batch(generators):
    def on_new(new_posts):
        log.debug(
            f"{common_logs.PROGRESS_IDS.format('Pinned')} {list(map(lambda x:x['id'],new_posts))}"
        )
        trace_progress_log(f"{API} task", new_posts)

    responseArray = await collect_pages(
        generators, "Pinned Content Pages Progress", on_new
    )
    log.debug(
        f"{common_logs.FINAL_IDS.format('Pinned')} {list(map(lambda x:x['id'],responseArray))}"
    )
    trace_log_raw(f"{API} final", responseArray, final_count=True)
    log.debug(common_logs.FINAL_COUNT_POST.format("Pinned", len(responseArray)))
    return responseArray


async def scrape_pinned_posts(c, model_id, offset=0):
    """Yield a model's pinned posts page by page."""
    end = PageEnd()

    def on_page(data, batch):
        if batch:
            trace_progress_log(f"{API} request", batch)

    pages = offset_paginate(
        c,
        end,
        lambda offset: of_env.getattr("timelinePinnedEP").format(model_id, offset),
        lambda offset: f"[Pinned] offset -> {offset}",
        offset,
        items=lambda data: data.get("list", []),
        on_page=on_page,
    )
    async with contextlib.aclosing(pages) as pages:
        async for batch in pages:
            yield batch
    # a response that isn't a JSON object ends the scan quietly, as before
    if end.status is not None:
        log.debug(f"Pinned API Error: {end.status}")
    if end.status is not None or end.error is not None:
        incomplete.mark_incomplete(model_id, API, end.reason)

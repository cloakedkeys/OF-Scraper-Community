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

import ofscraper.data.api.common.logs.strings as common_logs
import ofscraper.main.close.exit_status as exit_status
import ofscraper.managers.manager as manager
import ofscraper.utils.cache.cache as cache
import ofscraper.utils.of_env.of_env as of_env
from ofscraper.commands.scraper.actions.download import stop
from ofscraper.data.api.common.check import update_check
from ofscraper.data.api.common.logs.logs import trace_log_raw, trace_progress_log
from ofscraper.data.api.common.offset_pages import PageEnd, offset_paginate
from ofscraper.data.api.tasks import collect_pages
from ofscraper.utils.context.run_async import run

paid_content_list_name = "list"
log = logging.getLogger("shared")
API = "Purchased"


@run
@stop.fetching
async def get_paid_posts(username, model_id, c=None):
    tasks = []
    # Create the coroutine; it will return an AsyncGenerator when awaited
    tasks.append(scrape_paid(c, username))
    data = await process_tasks(tasks)
    update_check(data, model_id, None, API)
    return data


async def process_tasks(generators):
    def on_new(new_posts):
        log.debug(
            f"{common_logs.PROGRESS_IDS.format('Paid')} {list(map(lambda x:x['id'],new_posts))}"
        )
        trace_progress_log(f"{API} tasks", new_posts)

    responseArray = await collect_pages(
        generators, "Paid Content Pages Progress", on_new
    )
    trace_log_raw(f"{API} final", responseArray, final_count=True)
    log.debug(common_logs.FINAL_COUNT_POST.format("Paid", len(responseArray)))
    return responseArray


async def scrape_paid(c, username, offset=0):
    """Yield a model's purchases page by page."""
    end = PageEnd()
    pages = offset_paginate(
        c,
        end,
        lambda offset: of_env.getattr("purchased_contentEP").format(offset, username),
        lambda offset: f"scrape paid offset -> {offset} username -> {username}",
        offset,
        on_page=lambda data, batch: trace_progress_log(
            f"{API} individual requests", data
        ),
    )
    async with contextlib.aclosing(pages) as pages:
        async for batch in pages:
            yield batch
    warn_if_stopped(end, f"Purchased scan for {username}")


def warn_if_stopped(end, scan):
    # stopping early can leave a model's purchases empty or incomplete, so
    # say so outside debug logs (#34)
    if end.finished:
        return
    verb = "failed" if end.error else "stopped"
    log.warning(
        f"{scan} {verb} at offset {end.offset}: {end.reason}. "
        "Purchases may be incomplete."
    )
    exit_status.record(exit_status.PARTIAL)


@run
@stop.fetching
async def get_all_paid_posts():
    async with manager.Manager.session.aget_ofsession(
        sem_count=of_env.getattr("SCRAPE_PAID_SEMS"),
    ) as c:
        tasks = await create_tasks_scrape_paid(c)
        data = await process_tasks_all_paid(tasks)
        return create_all_paid_dict(data)


async def create_tasks_scrape_paid(c):
    """
    Creates parallel workers for scraping all paid content.
    Returns a list of coroutines (not tasks yet) to be scheduled.
    """
    min_posts = 80
    tasks = []
    allpaid = cache.get("purchased_all", default=[])

    if len(allpaid) > min_posts:
        splitArrays = [i for i in range(0, len(allpaid), min_posts)]
        for i in range(0, len(splitArrays) - 1):
            tasks.append(scrape_all_paid(c, required=min_posts, offset=splitArrays[i]))
        tasks.append(scrape_all_paid(c, offset=splitArrays[-1], required=None))
    else:
        tasks.append(scrape_all_paid(c))
    return tasks


async def process_tasks_all_paid(generators):
    # every purchase is kept, repeats included
    output = await collect_pages(
        generators,
        "[Scrape Paid] Pages Progress",
        lambda batch: trace_progress_log(f"{API} all users tasks", batch),
        key=None,
    )
    trace_log_raw(f"{API} all users final", output, final_count=True)
    stop.check()
    cache.set(
        "purchased_all",
        list(map(lambda x: x.get("id"), output)),
        expire=of_env.getattr("RESPONSE_EXPIRY"),
    )
    return output


async def scrape_all_paid(c, offset=0, required=None):
    """Yield every model's purchases page by page, from ``offset``; with
    ``required``, stop once that many have been read."""
    end = PageEnd()
    retrieved = 0

    def enough(batch):
        nonlocal retrieved
        retrieved += len(batch)
        return required is not None and retrieved >= required

    pages = offset_paginate(
        c,
        end,
        lambda offset: of_env.getattr("purchased_contentALL").format(offset),
        lambda offset: f"scrape entire paid page offset={offset}",
        offset,
        on_page=lambda data, batch: trace_progress_log(
            f"{API} all users requests", data
        ),
        should_stop=enough,
    )
    async with contextlib.aclosing(pages) as pages:
        async for batch in pages:
            yield batch
    warn_if_stopped(end, "Purchased scan (all models)")


def create_all_paid_dict(paid_content):
    user_dict = {}
    for ele in paid_content:
        user_id = ele.get("fromUser", {}).get("id") or ele.get("author", {}).get("id")
        user_dict.setdefault(str(user_id), []).append(ele)
    [update_check(val, key, None, API) for key, val in user_dict.items()]
    return user_dict


def get_individual_paid_post(username, model_id, postid):
    data = get_paid_posts(username, model_id)
    postid = int(postid)
    posts = list(filter(lambda x: int(x.get("id")) == postid, data))
    return posts[0] if posts else None

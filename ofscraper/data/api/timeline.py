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

import logging
import sys

import ofscraper.data.api.common.dated_posts as dated_posts
import ofscraper.data.api.common.logs.strings as common_logs
import ofscraper.data.api.common.scan_range as scan_range
from ofscraper.commands.scraper.actions.download import stop
from ofscraper.data.api.common.dated_posts import date_or_2000  # noqa: F401
from ofscraper.data.api.common.logs.logs import trace_progress_log
from ofscraper.data.api.tasks import collect_pages
from ofscraper.utils.context.run_async import run

# Imports marked F401 are used through this module by dated_posts, or patched
# through it (module.settings, module.progress_utils, module.incomplete)
import ofscraper.data.api.common.incomplete as incomplete  # noqa: F401
import ofscraper.utils.live.updater as progress_utils  # noqa: F401
import ofscraper.utils.of_env.of_env as of_env  # noqa: F401
import ofscraper.utils.settings as settings
from ofscraper.data.api.common.after import get_after_pre_checks  # noqa: F401
from ofscraper.data.api.common.check import update_check  # noqa: F401
from ofscraper.data.api.common.page import get_min_posts_batch  # noqa: F401
from ofscraper.data.api.common.timeline import process_posts_as_individual  # noqa: F401
from ofscraper.db.operations_.media import (  # noqa: F401
    get_media_ids_downloaded_model,
    get_timeline_media,
)
from ofscraper.db.operations_.posts import (  # noqa: F401
    get_deleted_post_ids,
    get_timeline_posts_info,
    get_youngest_timeline_date,
    mark_post_as_deleted,
)

API = "Timeline"
AREA = dated_posts.Area(
    api=API,
    endpoint="timelineEP",
    next_endpoint="timelineNextEP",
    max_individual="MAX_TIMELINE_INDIVIDUAL_SEARCH",
    cache_trace="oldtimeline",
    ghosts="deleted posts in database.",
)
log = logging.getLogger("shared")
_area = sys.modules[__name__]


@run
@stop.fetching
async def get_timeline_posts(model_id, username, c=None, post_id=None):
    return await dated_posts.get_posts(_area, model_id, username, c, post_id)


async def process_tasks(generators):
    def on_new(new_posts):
        log.debug(
            f"{common_logs.PROGRESS_IDS.format('Timeline')} {list(map(lambda x:x['id'],new_posts))}"
        )
        trace_progress_log(f"{API} task", new_posts)

    return await collect_pages(generators, "Timeline Content Pages Progress", on_new)


async def process_tasks_batch(generators):
    return await _area.process_tasks(generators)


async def get_oldtimeline(model_id, username):
    return await dated_posts.get_old_posts(
        _area, get_timeline_posts_info, model_id, username
    )


async def get_split_array(model_id, username, after):
    oldtimeline = await get_oldtimeline(model_id, username)
    return dated_posts.split_posts(_area, oldtimeline, after)


def get_tasks(splitArrays, c, model_id, username, after):
    return dated_posts.get_tasks(
        scrape_timeline_posts, splitArrays, c, model_id, username, after
    )


def scrape_timeline_posts(
    c,
    model_id,
    username,
    timestamp=None,
    required_posts=None,
    is_last_chunk=False,
    offset=False,
):
    """An async generator of one worker's pages."""
    return dated_posts.scrape_posts(
        _area, c, model_id, username, timestamp, required_posts, is_last_chunk, offset
    )


async def get_after(model_id, username):
    return await dated_posts.get_after(
        _area, get_timeline_media, get_youngest_timeline_date, model_id, username
    )


def time_log(username, after):
    scan_range.log_scan_range(log, username, "timeline", after)


def filter_timeline_post(timeline_posts):
    if settings.get_settings().timeline_strict:
        timeline_only_posts = list(filter(lambda x: x.regular_timeline, timeline_posts))
    else:
        timeline_only_posts = timeline_posts
    return timeline_only_posts

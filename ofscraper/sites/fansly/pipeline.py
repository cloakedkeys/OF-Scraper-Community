"""Fansly downloads: download each page as it is scanned.

Most Fansly links are signed for the address Fansly saw when it answered the
API call, so a link can be refused once the address changes (a VPN
reconnect, an ISP change). Scanning a large creator first and downloading
after leaves an hour between signing and use. Here a producer pages through
the areas, saves each page to the database, selects its media and puts it on
a small bounded queue; download workers take it from there. The producer
waits while the queue is full, so the links waiting are never more than
about a page plus the queue, scan and download overlap, and the first file
starts within seconds.

Used for plain Fansly download runs (``applies``). Runs that need the whole
list first (``--max-count``, a sort; ``ordered``) scan and select as before,
then download through the same queue and workers, fetching each chunk's
links again just before it is queued. Every other command takes the
scan-then-download path.
"""

import asyncio
import contextlib
import logging

import ofscraper.classes.labels as labels
import ofscraper.classes.of.posts as posts_
import ofscraper.commands.scraper.actions.utils.globals as common_globals
import ofscraper.data.api.labels as labels_api
import ofscraper.data.api.timeline as timeline
import ofscraper.db.operations as operations
import ofscraper.filters.media.filters as filters
import ofscraper.managers.manager as manager
import ofscraper.sites as sites
import ofscraper.utils.cache.cache as cache
import ofscraper.utils.live.screens as progress_utils
import ofscraper.utils.live.updater as progress_updater
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.separate as separate
import ofscraper.utils.settings as settings
import ofscraper.utils.system.free as free
from ofscraper.commands.scraper.actions.download import stop
from ofscraper.commands.scraper.actions.download.download import (
    recheck_silent,
    save_path_str,
)
from ofscraper.commands.scraper.actions.download.run import handle_one
from ofscraper.commands.scraper.actions.download.utils import refresh
from ofscraper.commands.scraper.actions.download.utils.desc import desc
from ofscraper.commands.scraper.actions.download.utils.text import textDownloader
from ofscraper.commands.scraper.actions.utils.paths import setDirectoriesDate
from ofscraper.commands.scraper.actions.utils.workers import get_max_workers
from ofscraper.commands.utils.strings import download_activity_str
from ofscraper.data.api.common.cache.write import set_after_checks
from ofscraper.data.api.common.check import update_check
from ofscraper.data.posts import post as post_area
from ofscraper.db.operations_.media import batch_mediainsert
from ofscraper.managers.postcollection import PostCollection
from ofscraper.scripts.after_download_action_script import after_download_action_script
from ofscraper.sites.fansly import api as fansly_api
from ofscraper.sites.fansly import incremental
from ofscraper.sites.fansly import scrape as fansly_scrape
from ofscraper.sites.fansly import video as fansly_video
from ofscraper.utils.args.accessors.areas import get_download_area

log = logging.getLogger("shared")

# Purchased and Messages first: they are short and hold the unlocked copies
# of paid media. Timeline before Labels: a wall is a part of the timeline,
# so its media is already seen by then. One area at a time: Fansly's rate
# limit is per account, so areas scanned together only interleave.
ORDER = ("Purchased", "Messages", "Timeline", "Labels")
# areas whose finished scan is recorded, as the scan-first path does
AFTER_CHECKED = ("Timeline", "Messages")
# queue slots per download worker: enough that a worker rarely waits, few
# enough that the links waiting stay about one page
QUEUE_PER_WORKER = 2
_END = object()


def needs_whole_list(s):
    """The option that can only be answered with the full list, or None."""
    if getattr(s, "max_post_count", None):
        return "--max-count"
    if getattr(s, "mediasort", None):
        return "--media-sort"
    if getattr(s, "media_desc", False):
        return "--media-desc"
    return None


def applies() -> bool:
    """Whether this run downloads Fansly pages as they are scanned."""
    if not sites.is_fansly():
        return False
    s = settings.get_settings()
    if s.command == "metadata":
        reason = "the metadata command"
    elif list(s.actions or []) != ["download"]:
        reason = "actions other than download"
    elif getattr(s, "users_first", False):
        reason = "--users-first"
    elif getattr(s, "text_only", False):
        reason = "--text-only"
    else:
        reason = None
    if reason:
        log.debug(f"Fansly: scanning everything before downloading because of {reason}")
        return False
    return True


def ordered() -> bool:
    """Whether the run needs the whole list before the first download: it
    then scans first and fetches each chunk's links just before queuing it."""
    reason = needs_whole_list(settings.get_settings())
    if reason:
        log.debug(
            f"Fansly: {reason} needs the whole list; scanning first, then "
            "fetching each chunk's links before it downloads"
        )
    return reason is not None


def areas_for(ele, username):
    """The areas to scan, in ``ORDER``, decided as the scan-first path does."""
    areas = post_area.site_post_areas()
    if post_area.skip_wall(ele):
        if any(a in areas for a in ("Timeline", "Labels")):
            post_area.log_skipped_wall(username)
        areas = [a for a in areas if a not in ("Timeline", "Labels")]
    elif "Labels" in areas and not post_area.wall_visible(ele):
        post_area.log_skipped_labels(username)
        areas = [a for a in areas if a != "Labels"]
    return [a for a in ORDER if a in areas]


class Selector:
    """The per-page form of ``PostCollection.get_media_to_download``.

    Keeps, for one creator's run, the media already chosen (one per media
    id, or per media id and post with ``ALLOW_DUPE_MEDIA``; the first copy
    seen wins) and the ids already downloaded, read once.
    """

    def __init__(self, model_id, username):
        self.seen = set()
        self.downloaded = filters.downloaded_media_ids(
            username=username, model_id=model_id
        )

    def select(self, posts):
        media = [m for post in posts for m in post.prepare_media_for_download()]
        media = filters.seperate_self(media)
        per_post = of_env.getattr("ALLOW_DUPE_MEDIA")
        out = []
        for item in media:
            key = (item.id, item.post_id) if per_post else item.id
            if key in self.seen:
                continue
            self.seen.add(key)
            if self.downloaded is not None and item.id in self.downloaded:
                continue
            out.append(item)
        if self.downloaded is not None:
            # --force-all downloads avatars again, as the full list does
            out = separate.seperate_avatars(out)
        return out


class _Run:
    """One creator's run: what was scanned, queued and finished."""

    def __init__(self, ele, c):
        self.ele = ele
        self.c = c
        self.username = ele.name
        self.model_id = ele.id
        self.collection = PostCollection(model_id=ele.id, username=ele.name)
        self.media = []  # every media queued, in order
        self.offered = set()  # post ids whose media was selected
        self.finished = []  # areas whose scan reached its end
        self.labels = {}  # label id -> label dict with its posts
        self.current = {}  # worker task -> the media it is downloading
        self.queue = None
        self.selector = None
        self.download_session = None
        self.task = None

    def queued(self):
        return len(self.media)


# ------------------------------------------------------------------ pages


async def _purchased_pages(run):
    async with contextlib.aclosing(
        fansly_scrape.paid_pages(run.model_id, run.username, c=run.c)
    ) as pages:
        async for page in pages:
            posts = [
                posts_.Post(x, run.model_id, run.username, responsetype="Paid")
                for x in page
            ]
            await operations.make_post_table_changes(
                posts, model_id=run.model_id, username=run.username, from_api=True
            )
            yield posts


async def _messages_pages(run):
    async with contextlib.aclosing(
        fansly_scrape.message_pages(
            run.model_id, run.username, c=run.c, post_id=settings.get_settings().post_id
        )
    ) as pages:
        async for page in pages:
            posts = [posts_.Post(x, run.model_id, run.username) for x in page]
            await operations.make_messages_table_changes(
                posts, model_id=run.model_id, username=run.username, from_api=True
            )
            yield posts


async def _timeline_pages(run):
    async with contextlib.aclosing(
        fansly_scrape.timeline_pages(
            run.model_id, run.username, c=run.c, post_id=settings.get_settings().post_id
        )
    ) as pages:
        async for page in pages:
            posts = timeline.filter_timeline_post(
                [posts_.Post(x, run.model_id, run.username, timeline.API) for x in page]
            )
            await operations.make_post_table_changes(
                posts, model_id=run.model_id, username=run.username, from_api=True
            )
            yield posts


async def _labels_pages(run):
    found = labels_api.only_asked_labels(
        await labels_api.get_labels_data(run.model_id, c=run.c)
    )
    known = await labels_api.get_known_label_posts(found, run.model_id, run.username)
    for label in found:
        seen = set()
        kept = run.labels.setdefault(label["id"], {**label, "posts": []})
        async with contextlib.aclosing(
            labels_api.scrape_posts_labels(
                run.c, label, run.model_id, known=known.get(label["id"]), track=True
            )
        ) as pages:
            async for _, batch in pages:
                new = labels_api.label_dedupe(seen, batch)
                if not new:
                    continue
                kept["posts"].extend(new)
                page = labels.Label({**label, "posts": new}, run.model_id, run.username)
                await operations.make_label_table_changes(
                    [page], model_id=run.model_id, username=run.username, from_api=True
                )
                yield page.posts


PAGES = {
    "Purchased": _purchased_pages,
    "Messages": _messages_pages,
    "Timeline": _timeline_pages,
    "Labels": _labels_pages,
}


# ------------------------------------------------------------------ producer


async def _take_page(run, area, posts, download, number):
    """Save the page's media rows, then queue what it has to download."""
    await batch_mediainsert(
        filters.dupefiltermedia([m for p in posts for m in p.all_media]),
        model_id=run.model_id,
        username=run.username,
        downloaded=False,
    )
    actions = ["download"] if download else []
    if settings.get_settings().text:
        actions.append("text")
    new = []
    for item in posts:
        kept = run.collection.add_post(item, actions=actions)
        if download and kept.id not in run.offered:
            run.offered.add(kept.id)
            new.append(kept)
    media = run.selector.select(new)
    log.info(f"[{run.username}] {area}: page {number}, {len(media)} to download")
    progress_updater.activity.update_task(
        description=f"Scanning {run.username}: {area}, page {number}", visible=True
    )
    for item in media:
        item.download_unstarted = True
        item.download_stopped = False
        run.media.append(item)
        progress_updater.download.update_overall_task(run.task, total=run.queued())
        await run.queue.put((run.download_session, item, run.model_id, run.username))
    # let idle workers start on this page before the next page is asked for
    await asyncio.sleep(0)


@free.space_checker
async def _scan_area(run, area, download, early_stop=None):
    incremental.scan_started(run.model_id, area)
    number = 0
    async with contextlib.aclosing(PAGES[area](run)) as pages:
        async for posts in pages:
            number += 1
            await _take_page(run, area, posts, download, number)
            if early_stop and early_stop.after_page([p.id for p in posts]):
                log.info(
                    f"[{run.username}] {area}: page {number} and older are already "
                    "recorded, stopping the scan"
                )
                break
    run.finished.append(area)


@stop.fetching
async def _produce(run):
    """Page through the areas, queuing each page's media as it comes."""
    run.selector = Selector(run.model_id, run.username)
    download_area = get_download_area()
    areas = areas_for(run.ele, run.username)
    # read what is recorded before this run writes any rows
    early_stops = {}
    for area in areas:
        try:
            early_stops[area] = await incremental.early_stop_for(run.ele, area)
        except Exception as E:
            log.debug(f"{area}: could not read what is recorded, scanning fully: {E}")
    for area in areas:
        try:
            await _scan_area(run, area, area in download_area, early_stops.get(area))
        except Exception as E:
            # pages already queued still download; the next area runs
            post_area.log_area_failure(run.username, area, E)


def _account_media_id(item):
    details = item.media.get("fansly") if isinstance(item.media, dict) else None
    if not details or not details.get("media"):
        return None
    return details.get("accountMediaId")


async def _fresh_links(run, chunk):
    """Give the chunk's items the links of one ``account/media`` answer.

    An item without an accountMedia id, or whose answer is missing or for
    another file, keeps the links from the scan; a 403 still refreshes it.
    """
    ids = list(dict.fromkeys(i for i in map(_account_media_id, chunk) if i))
    if not ids:
        return
    try:
        found = await fansly_api.account_media(run.c, ids)
    except Exception as E:
        log.debug(f"[{run.username}] fetching new links failed, using the scan's: {E}")
        return
    answers = {str(x.get("id")): x for x in found or [] if isinstance(x, dict)}
    fresh = 0
    for item in chunk:
        answer = answers.get(str(_account_media_id(item)))
        if answer and refresh.apply_fansly_item(item, answer):
            fresh += 1
    log.debug(f"[{run.username}] {fresh} of {len(chunk)} files have newly signed links")


@stop.fetching
async def _produce_ordered(run):
    """Queue the selected list in its order, a chunk at a time, fetching
    each chunk's links just before it is queued. The queue is bounded, so
    the next chunk's call waits until this chunk is nearly downloaded."""
    size = fansly_api.ACCOUNT_MEDIA_BATCH
    for start in range(0, len(run.media), size):
        chunk = run.media[start : start + size]
        await _fresh_links(run, chunk)
        for item in chunk:
            await run.queue.put((run.download_session, item, run.model_id, run.username))
        await asyncio.sleep(0)


# ------------------------------------------------------------------ workers


async def _worker(run):
    task = asyncio.current_task()
    while True:
        item = await run.queue.get()
        if item is _END:
            return
        if stop.requested.is_set():
            # queued but not taken: left unstarted, as an untaken list item is
            item[1].download_unstarted = True
            continue
        run.current[task] = item[1]
        try:
            await handle_one(item, run.task, run.queued)
        except asyncio.CancelledError:
            # handle_one counts a cancelled download; this is a cancel that
            # came after it, during its pause
            if not stop.requested.is_set():
                raise
        finally:
            run.current.pop(task, None)


def _cancel_streams(run, cancelled):
    """A stop request cancels the scan and the direct-file transfers, but the
    stream downloader takes no part in it. Cancel the workers inside a stream
    download, so the run doesn't wait for every video to finish. The stream
    downloader keeps its finished segments for the next run."""
    for task, ele in list(run.current.items()):
        if task not in cancelled and fansly_video.uses_stream(ele):
            cancelled.add(task)
            task.cancel()


async def _drain(run, workers):
    """End the workers once the queue is empty, cancelling streams on a stop."""
    ends = len(workers)
    cancelled = set()
    while True:
        if stop.requested.is_set():
            _cancel_streams(run, cancelled)
        while ends and not run.queue.full():
            run.queue.put_nowait(_END)
            ends -= 1
        _, pending = await asyncio.wait(workers, timeout=0.25)
        if not pending:
            return


# ------------------------------------------------------------------ run


def _zero_desc():
    return desc.format(
        stopped=0, p_count=0, v_count=0, a_count=0, skipped=0, mediacount=0,
        forced_skipped=0, sumcount=0, total_bytes_download=0, total_bytes=0,
    )


async def _download(run, produce):
    """Run ``produce`` and the download workers together. Returns True when a
    stop request ended it."""
    stopped = False
    count = get_max_workers()
    log.info(f"Downloading up to {count} {'file' if count == 1 else 'files'} at a time")
    common_globals.mainProcessVariableInit()
    with progress_utils.setup_live("download"):
        try:
            async with manager.Manager.session.get_download_session() as dc:
                run.download_session = dc
                run.task = progress_updater.download.add_overall_task(
                    _zero_desc(), total=run.queued(), visible=True
                )
                run.queue = asyncio.Queue(maxsize=QUEUE_PER_WORKER * count)
                workers = [asyncio.create_task(_worker(run)) for _ in range(count)]
                try:
                    producer = asyncio.create_task(produce(run))
                    try:
                        await producer
                    except stop.DownloadsStopped:
                        stopped = True
                    await _drain(run, workers)
                finally:
                    for task in workers:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
        finally:
            await asyncio.get_running_loop().run_in_executor(
                common_globals.thread, cache.close
            )
            common_globals.thread.shutdown()
        setDirectoriesDate()
        progress_updater.download.remove_overall_task(run.task)
    return stopped or stop.requested.is_set()


async def _commit(run):
    """What the scan-first path records once the scan's rows are saved."""
    try:
        labels_api.commit_finished_labels(run.model_id)
        for area in run.finished:
            if area in AFTER_CHECKED:
                set_after_checks(run.model_id, area)
            incremental.scan_finished(run.ele, area)
        if run.labels:
            update_check(list(run.labels.values()), run.model_id, None, labels_api.API)
        await post_area.clean_stray_media(run.username, run.model_id, run.collection)
    finally:
        labels_api.discard_finished_labels(run.model_id)


async def run(ele, c, collection=None):
    """Download one Fansly creator, each page as it is scanned.

    ``collection``: the creator's finished scan, for a run that needs the
    whole list (``ordered``). Its selected media download in order, with
    each chunk's links fetched again just before it is queued; the scan
    already recorded its areas.
    """
    run_ = _Run(ele, c)
    username, model_id = run_.username, run_.model_id
    produce = _produce
    if collection is not None:
        produce = _produce_ordered
        run_.collection = collection
        run_.media = list(collection.get_media_to_download())
        for item in run_.media:
            item.download_unstarted = True
            item.download_stopped = False
    await operations.table_init_create(model_id=model_id, username=username)
    with progress_utils.setup_live("main_activity", clear=False):
        progress_updater.activity.update_task(
            description=download_activity_str.format(username=username)
            + save_path_str(username=username, model_id=model_id),
            visible=True,
        )
        log.warning(download_activity_str.format(username=username))
        try:
            stopped = await _download(run_, produce)
        finally:
            if stop.requested.is_set() and collection is None:
                # an interrupted scan isn't recorded as finished
                labels_api.discard_finished_labels(model_id)
        if not stopped:
            # videos saved without sound: check their audio again (silent.py)
            await recheck_silent(username, model_id, run_.collection.all_media)
        if not stopped and collection is None:
            await _commit(run_)
        text_posts = run_.collection.get_posts_for_text_download()
        if settings.get_settings().text and not stopped:
            await textDownloader(text_posts, username=username)
        manager.Manager.stats_manager.update_and_print_stats(
            username, "download", run_.media
        )
        if stop.requested.is_set():
            raise stop.DownloadsStopped
        manager.Manager.current_model_manager.mark_as_processed(
            username, activity="download"
        )
        after_download_action_script(username, run_.media, text_posts, action="download")

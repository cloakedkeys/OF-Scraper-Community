"""Record OnlyFans livestreams (``--live`` / ``--live-watch``).

Based on the Live Stream Monitor plugin by cjb900 (cjb900/OF-Scraper-GUI).

OnlyFans livestreams are WebRTC (Agora), not an HLS playlist ffmpeg could
read, so a recording is made by the stream page itself: a Chromium browser
(Playwright) opens it with the auth.json login and records the player with
the browser's MediaRecorder (see ``live_capture``).

- **Who is live:** an active subscription with ``hasStream`` set, from the
  same subscriptions list the scraper reads.
- **Still live:** ``users/{username}/streams/active``, asked while recording.

Recordings go to
``{save_location}/OnlyFans/{username}/Livestreams/{username}_{YYYYMMDD_HHMMSS}_live.webm``.
The watcher, the Ctrl+C handling and the options are shared with Fansly
(``ofscraper.sites.fansly.live``).
"""

import asyncio
import datetime
import logging
import pathlib
import threading

from ofscraper.sites.fansly import live as shared

log = logging.getLogger("shared")

STREAM_ACTIVE_URL = "https://onlyfans.com/api2/v2/users/{}/streams/active?view=full_with_check"


def recording_path(save_location, username, now=None) -> pathlib.Path:
    """``{save_location}/OnlyFans/{username}/Livestreams/{username}_{YYYYMMDD_HHMMSS}_live.webm``"""
    now = now or datetime.datetime.now(tz=datetime.timezone.utc)
    username = shared.folder_name(username)
    folder = pathlib.Path(save_location) / "OnlyFans" / username / "Livestreams"
    return folder / f"{username}_{now.strftime('%Y%m%d_%H%M%S')}_live.webm"


def live_subscriptions(pages) -> list:
    """The creators in subscription pages who are live now, as watcher streams."""
    return [
        {"accountId": user.get("id"), "username": user.get("username")}
        for page in pages
        for user in page
        if user.get("hasStream") and user.get("username")
    ]


def stream_is_active(data):
    """True / False from a ``streams/active`` answer; None if it can't tell."""
    if not isinstance(data, dict):
        return None
    return bool(data.get("isActive")) and not data.get("isFinished")


async def get_online_streams(c) -> list:
    from ofscraper.data.api.subscriptions.subscriptions import scrape_subscriptions_active

    pages = [page async for page in scrape_subscriptions_active(c)]
    return live_subscriptions(pages)


async def still_live(c, username):
    """True / False from OnlyFans; None when the check fails."""
    try:
        async with c.requests_async(url=STREAM_ACTIVE_URL.format(username)) as r:
            if r.status == 404:
                return False
            if not (200 <= r.status < 300):
                return None
            return stream_is_active(await r.json_())
    except Exception as e:
        log.debug(f"{username}: live check failed: {e}")
        return None


async def record_in_thread(capture, stop: asyncio.Event, **kwargs):
    """Run the blocking ``capture(halt=...)`` in a thread; ``stop`` sets ``halt``."""
    halt = threading.Event()
    job = asyncio.ensure_future(asyncio.to_thread(capture, halt=halt, **kwargs))
    try:
        while not job.done():
            if stop.is_set():
                halt.set()
            await asyncio.wait({job}, timeout=1)
    finally:
        # also on Ctrl+C: the browser saves what it has before the thread ends
        halt.set()
        if not job.done():
            await asyncio.wait({job}, timeout=60)
    return job.result()


async def run(watch: bool = False, stop: asyncio.Event = None):
    """Record the selected creators who are live: once, or watching until stopped."""
    import ofscraper.managers.manager as manager
    import ofscraper.utils.paths.common as common_paths
    import ofscraper.utils.system.ffmpeg as ffmpeg_finder
    from ofscraper.sites.onlyfans import live_capture
    from ofscraper.utils.auth.file import read_auth

    opts = shared.options()
    if opts["chat"]:
        log.warning("--live-chat only applies to Fansly; OnlyFans recordings have no chat file")
    if not live_capture.playwright_ready():
        return
    auth = read_auth()
    save_location = common_paths.get_save_location()
    ffmpeg = ffmpeg_finder.get_ffmpeg()

    async with manager.Manager.session.aget_ofsession() as c:
        loop = asyncio.get_running_loop()

        async def fetch_online():
            online = await get_online_streams(c)
            return shared.select_streams(online, opts["usernames"], opts["excluded"])

        async def record(stream, stop_event):
            username = stream["username"]

            def check_live():
                future = asyncio.run_coroutine_threadsafe(still_live(c, username), loop)
                try:
                    return future.result(timeout=60)
                except Exception:
                    return None

            await record_in_thread(
                live_capture.capture, stop_event,
                username=username,
                out=recording_path(save_location, username),
                auth=auth,
                still_live=check_live,
                ffmpeg=ffmpeg,
            )

        watcher = shared.LiveWatcher(
            fetch_online, record,
            max_concurrent=opts["max_concurrent"],
            interval=opts["interval"],
            once=not watch,
        )
        await watcher.run(stop)

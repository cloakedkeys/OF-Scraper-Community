"""Download a Fansly video from its HLS or DASH stream.

Used only for Fansly items whose best delivery is a stream (see
``ofscraper.sites.fansly.video.uses_stream``); the direct MP4 still goes
through the normal downloader. ffmpeg copies the stream into one MP4 without
re-encoding. The finishing steps (move, collision handling, file time,
database, hash, after-download script) are the same as the normal downloader's.
"""

import asyncio
import pathlib
import subprocess
from functools import partial

import ofscraper.classes.placeholder as placeholder
import ofscraper.commands.scraper.actions.utils.general as common
import ofscraper.commands.scraper.actions.utils.globals as common_globals
import ofscraper.commands.scraper.actions.utils.log as common_logs
import ofscraper.commands.scraper.actions.utils.paths as common_paths
import ofscraper.utils.dates as dates
import ofscraper.utils.settings as settings
from ofscraper.commands.scraper.actions.download.managers.downloadmanager import (
    DownloadManager,
)
from ofscraper.db.operations_.media import mark_media_as_downloaded
from ofscraper.managers.sessionmanager.sessionmanager import HEADERS
from ofscraper.sites.fansly import auth as fansly_auth
from ofscraper.sites.fansly import headers as fansly_headers
from ofscraper.sites.fansly import video as fansly_video
from ofscraper.utils.cache.profile import set_profile_cache
from ofscraper.utils.system.ffmpeg import get_ffmpeg
from ofscraper.utils.system.ffprobe import get_media_duration, verify_media_integrity


class FanslyStreamDownloadManager(DownloadManager):
    async def stream_download(self, c, ele, username, model_id):
        await common_globals.sem.acquire()
        try:
            return await self._stream_download(c, ele, username, model_id)
        finally:
            common_globals.sem.release()

    async def _stream_download(self, c, ele, username, model_id):
        medialog = common_logs.get_medialog(ele)
        best = fansly_video.select_best(fansly_video.raw_media(ele))
        if not best or not best.is_stream:
            raise fansly_video.StreamError("no HLS or DASH stream to download")
        common_globals.log.debug(
            f"{medialog} Downloading with the Fansly stream downloader: "
            f"{best.kind} {best.width}x{best.height}"
        )
        self._downloadspace()

        try:
            manifest = await self._fetch_text(c, best.url, best.cookies)
            if best.kind == fansly_video.HLS:
                url = fansly_video.best_rendition_url(manifest, best.url)
            else:
                if fansly_video.dash_is_protected(manifest):
                    common_globals.log.info(f"{medialog} protected, not downloaded")
                    return "forced_skipped", 0
                url = best.url
        except fansly_video.StreamError as e:
            common_globals.log.warning(f"{medialog} {e}")
            return "skipped", 0

        placeholderObj = await placeholder.Placeholders(ele, "mp4").init()
        common_logs.path_to_file_logger(placeholderObj, ele)
        tempholderObj = await placeholder.tempFilePlaceholder(
            ele, f"{ele.filename or ele.id}_{ele.id}_{ele.post_id}.part"
        ).init()
        temp = pathlib.Path(tempholderObj.tempfilepath)
        temp.unlink(missing_ok=True)

        try:
            await self._run_ffmpeg(url, temp, best.cookies)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        total = temp.stat().st_size

        if await self._check_forced_skip(ele, total) == 0:
            temp.unlink(missing_ok=True)
            if ele.mediatype.capitalize() != "Forced_skipped":
                await self._force_download(ele, username, model_id)
            return ele.mediatype.capitalize(), 0

        await self._total_change_helper(total)
        await self._integrity_check(temp, ele)
        return await self._finish(temp, placeholderObj, ele, username, model_id, total)

    async def _fetch_text(self, c, url, cookies):
        # HEADERS only: the CloudFront cookies must not be replaced by the
        # session's own (none for Fansly), and CDN requests are never signed
        async with c.requests_async(
            url=url, cookies=cookies or None, actions=[HEADERS]
        ) as r:
            if not r.ok:
                raise fansly_video.StreamError(
                    f"stream manifest request failed with status {r.status}"
                )
            return await r.text_()

    async def _run_ffmpeg(self, url, temp, cookies):
        cmd = fansly_video.ffmpeg_command(
            get_ffmpeg(),
            url,
            temp,
            fansly_headers.cdn_headers(fansly_auth.read_auth()),
            cookies,
        )
        # subprocess in a worker thread: the app may run a selector event
        # loop on Windows, which can't start asyncio subprocesses
        result = await asyncio.get_event_loop().run_in_executor(
            common_globals.thread,
            partial(subprocess.run, cmd, capture_output=True),
        )
        if result.returncode != 0 or not temp.exists() or not temp.stat().st_size:
            err = (result.stderr or b"").decode("utf-8", "replace").strip()[-500:]
            raise Exception(f"ffmpeg could not copy the stream: {err or 'no output'}")

    async def _integrity_check(self, temp, ele):
        if not settings.get_settings().verify_all_integrity:
            return
        expected = ele.duration
        loop = asyncio.get_event_loop()
        if expected:
            ok = await loop.run_in_executor(
                common_globals.thread, partial(verify_media_integrity, temp, expected)
            )
        else:
            ok = (
                await loop.run_in_executor(
                    common_globals.thread, partial(get_media_duration, temp)
                )
            ) is not None
        if not ok:
            temp.unlink(missing_ok=True)
            raise Exception("Fansly stream download failed the integrity check")

    async def _finish(self, temp, placeholderObj, ele, username, model_id, total):
        loop = asyncio.get_event_loop()
        # can end up under another name if a different file already has this one (#48)
        path_to_file = await loop.run_in_executor(
            common_globals.thread,
            partial(common_paths.moveHelper, temp, placeholderObj.trunicated_filepath, ele),
        )
        common_paths.addGlobalDir(path_to_file.parent)
        if ele.postdate:
            await loop.run_in_executor(
                common_globals.thread,
                partial(
                    common_paths.set_time,
                    path_to_file,
                    dates.convert_local_time(ele.postdate),
                ),
            )
        if ele.id:
            await mark_media_as_downloaded(
                ele,
                filepath=path_to_file,
                model_id=model_id,
                username=username,
                downloaded=True,
                hashdata=await common.get_hash(path_to_file),
                size=path_to_file.stat().st_size,
            )
        await set_profile_cache(ele, common_globals.thread)
        ele.add_filepath(path_to_file)
        ele.add_size(total)
        await self._after_download_script(path_to_file)
        return ele.mediatype, total

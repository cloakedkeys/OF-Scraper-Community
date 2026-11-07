"""Downloading a file whose replies don't give its size (#260).

A server that sends a file chunked, without Content-Length or Content-Range,
used to make the normal downloader's size check crash with TypeError on
every retry, and the DRM downloader stop with "DRM segment empty size". Now
the reply is written to its end, and what was written is the file's size.
Each test runs a downloader's whole request step, retries included, through
the real session against a local server.
"""

import asyncio
import contextvars
import logging
from types import SimpleNamespace

import pytest
from aiohttp import web
from tenacity import stop_after_attempt, wait_none

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.commands.scraper.actions.download.managers.alt_download as ad
import ofscraper.commands.scraper.actions.download.managers.downloadmanager as dm
import ofscraper.commands.scraper.actions.download.managers.main_download as md
import ofscraper.commands.scraper.actions.download.utils.retries as retries
import ofscraper.commands.scraper.actions.utils.globals as common_globals
import ofscraper.utils.settings as settings
from ofscraper.managers.sessionmanager.sessionmanager import sessionManager

T = 100_000
BODY = bytes(i % 251 for i in range(T))


async def _noop(*args, **kwargs):
    return None


class _Log:
    """The downloaders' log, which also has trace and traceback_."""

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda *a, **k: SimpleNamespace(
            auto_resume=True,
            download_limit=0,
            command="download",
            ssl_verify="false",
            cached_disabled=True,
        ),
    )
    monkeypatch.setattr(common_globals, "log", _Log(), raising=False)
    for name in ("attempt", "attempt2"):
        monkeypatch.setattr(
            common_globals, name, contextvars.ContextVar(name, default=0), raising=False
        )
    for module in (md, ad):
        monkeypatch.setattr(
            module,
            "download_retry",
            lambda: retries.download_retry(stop=stop_after_attempt(2), wait=wait_none()),
        )
        monkeypatch.setattr(module, "get_download_retries", lambda: 2)
        monkeypatch.setattr(module, "send_chunk_msg", lambda *a, **k: None)
    monkeypatch.setattr(md.common_logs, "path_to_file_logger", lambda *a, **k: None)
    monkeypatch.setattr(md.common_logs, "get_medialog", lambda ele: "")
    monkeypatch.setattr(dm, "get_medialog", lambda ele: "")
    monkeypatch.setattr(ad, "temp_file_logger", lambda *a, **k: None)
    monkeypatch.setattr(ad, "get_alt_params", lambda ele: {})
    monkeypatch.setattr(ad, "get_medialog", lambda ele: "")
    monkeypatch.setattr(ad.auth_requests, "get_cookies_str", lambda: "")


def _manager(cls, saved, cached):
    mgr = cls()
    mgr._check_forced_skip = _noop
    mgr._add_download_job_task = _noop
    mgr._remove_download_job_task = _noop
    mgr._total_change_helper = _noop
    mgr._downloadspace = lambda: None

    async def _get_data(*args):
        return saved

    async def _set_data(*args):
        cached.append(args[-1])

    mgr._get_data = _get_data
    mgr._set_data = _set_data
    return mgr


def _placeholder(tmp_path, part):
    class _Placeholder:
        def __init__(self, *a):
            pass

        async def init(self):
            return SimpleNamespace(
                tempfilepath=part, trunicated_filepath=tmp_path / "f.mp4"
            )

    return _Placeholder


def _run(body, download):
    """Serve `body` chunked, with no Content-Length or Content-Range: a 206
    from the asked offset to a Range, else a 200. Returns (result or
    exception, Range headers seen)."""
    seen = []

    async def handler(request):
        rng = request.headers.get("Range")
        seen.append(rng)
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        resp = web.StreamResponse(status=206 if rng else 200)
        resp.content_type = "video/mp4"
        resp.enable_chunked_encoding()
        await resp.prepare(request)
        if body[start:]:
            await resp.write(body[start:])
        await resp.write_eof()
        return resp

    async def go():
        app = web.Application()
        app.router.add_get("/{name}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            async with sessionManager(
                retries=1, wait_min=0, wait_max=0, log=logging.getLogger("test")
            ) as c:
                try:
                    return await download(c, base), seen
                except Exception as E:
                    return E, seen
        finally:
            await runner.cleanup()

    return asyncio.run(go())


def _main(tmp_path, monkeypatch, have=0, saved=None, body=BODY):
    part = tmp_path / "f.part"
    if have:
        part.write_bytes(BODY[:have])
    cached = []
    mgr = _manager(md.MainDownloadManager, saved, cached)
    ph = _placeholder(tmp_path, part)
    monkeypatch.setattr(md.placeholder, "tempFilePlaceholder", ph)
    monkeypatch.setattr(md.placeholder, "Placeholders", ph)

    async def download(c, base):
        ele = SimpleNamespace(
            url=f"{base}/f", id=1, post_id=1, username="u", filename="f"
        )
        total, _, _ = await mgr._main_download_downloader(c, ele)
        return total

    total, seen = _run(body, download)
    return total, seen, part, cached


def _drm(tmp_path, monkeypatch, have=0, saved=None, body=BODY):
    part = tmp_path / "v.part"
    if have:
        part.write_bytes(BODY[:have])
    cached = []
    mgr = _manager(ad.AltDownloadManager, saved, cached)
    monkeypatch.setattr(
        ad.placeholder, "tempFilePlaceholder", _placeholder(tmp_path, part)
    )

    async def download(c, base):
        monkeypatch.setattr(ad, "_mpd_segment_url", lambda mpd, name: f"{base}/v")
        ele = SimpleNamespace(mpd="x", hls_header="", id=1, username="u", filename="f")
        item = {"origname": "v", "type": "video", "name": "v"}
        item = await mgr._alt_download_downloader(item, c, ele)
        return item["total"]

    total, seen = _run(body, download)
    return total, seen, part, cached


RUNS = pytest.mark.parametrize("run", [_main, _drm], ids=["normal", "drm"])


@RUNS
def test_download_without_size(tmp_path, monkeypatch, run):
    total, seen, part, cached = run(tmp_path, monkeypatch)
    assert part.read_bytes() == BODY
    assert total == T
    assert seen == [None]
    # no size is saved for the file: there was none to save
    assert [c["content-total"] for c in cached] == [None]


@RUNS
def test_resume_without_size_starts_over(tmp_path, monkeypatch, run):
    # the 206 says nothing about the file's size, so the part can't be lined
    # up with it: it's dropped and the whole file is fetched, also without a size
    saved = {"content-total": T, "content-type": "video/mp4"}
    total, seen, part, cached = run(tmp_path, monkeypatch, 30_000, saved)
    assert part.read_bytes() == BODY
    assert total == T
    assert seen == ["bytes=30000-", None]
    assert [c["content-total"] for c in cached] == [None]


@RUNS
def test_part_with_no_saved_size_is_downloaded_again(tmp_path, monkeypatch, run):
    # a part left by an earlier download whose size was never known can't be
    # checked, so the file is fetched whole, without a Range
    saved = {"content-total": None, "content-type": "video/mp4"}
    total, seen, part, _ = run(tmp_path, monkeypatch, 30_000, saved)
    assert part.read_bytes() == BODY
    assert total == T
    assert seen == [None]


@RUNS
def test_empty_reply_without_size_fails(tmp_path, monkeypatch, run):
    # 0 bytes would read as "skipped" and mark the media downloaded with no file
    result, seen, _, _ = run(tmp_path, monkeypatch, body=b"")
    assert isinstance(result, Exception)
    assert "no data and no file size" in str(result)
    assert seen == [None, None]

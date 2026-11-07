"""Resuming a download from a leftover .part file (#244).

Each test runs a downloader's _send_req_inner against a local server, with the
first R bytes of a file already in the .part. The normal downloader used to
take the 206 reply's Content-Length (only the missing bytes) as the size of
the whole file: with more than half on disk it deleted the .part, wrote only
the end of the file and passed the size check.
"""

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.commands.scraper.actions.download.managers.downloadmanager as dm
import ofscraper.commands.scraper.actions.download.managers.main_download as md
import ofscraper.commands.scraper.actions.utils.globals as common_globals
import ofscraper.utils.settings as settings

T = 1_000_000
BODY = bytes(i % 251 for i in range(T))


def _handler(mode, seen):
    async def handler(request):
        rng = request.headers.get("Range")
        seen.append(rng)
        if not rng or mode == "ignore-range":
            return web.Response(status=200, body=BODY, content_type="video/mp4")
        start, end = rng.split("=")[1].split("-")
        start = int(start)
        end = min(int(end) if end else T - 1, T - 1)
        headers = {}
        if mode != "no-content-range":
            headers["Content-Range"] = f"bytes {start}-{end}/{T}"
        return web.Response(
            status=206,
            body=BODY[start : end + 1],
            headers=headers,
            content_type="video/mp4",
        )

    return handler


class _Client:
    """Stands in for the download session: one plain aiohttp GET."""

    def __init__(self, session, url):
        self.session, self.url = session, url

    @contextlib.asynccontextmanager
    async def requests_async(self, url=None, headers=None, **kwargs):
        r = await self.session.get(self.url, headers=headers or {})
        r.iter_chunked = r.content.iter_chunked
        try:
            yield r
        finally:
            r.release()


async def _noop(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda *a, **k: SimpleNamespace(
            auto_resume=True, download_limit=0, command="download"
        ),
    )
    monkeypatch.setattr(common_globals, "log", logging.getLogger("test"), raising=False)
    monkeypatch.setattr(common_globals, "attempt", SimpleNamespace(get=lambda *a: 1), raising=False)
    monkeypatch.setattr(md, "send_chunk_msg", lambda *a, **k: None)
    monkeypatch.setattr(md.common_logs, "path_to_file_logger", lambda *a, **k: None)
    monkeypatch.setattr(md.common_logs, "get_medialog", lambda ele: "")
    monkeypatch.setattr(dm, "get_medialog", lambda ele: "")


def _manager(module_class, cached):
    mgr = module_class()
    mgr._check_forced_skip = _noop
    mgr._add_download_job_task = _noop
    mgr._remove_download_job_task = _noop
    mgr._total_change_helper = _noop

    async def _set_data(*args):
        cached.append(args[-1])

    mgr._set_data = _set_data
    return mgr


async def _serve(mode, seen, run):
    app = web.Application()
    app.router.add_get("/f", _handler(mode, seen))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            return await run(_Client(session, f"http://127.0.0.1:{port}/f"))
    finally:
        await runner.cleanup()


def _main_resume(tmp_path, have, mode="range", cached_total=T):
    part = tmp_path / "f.part"
    if have:
        part.write_bytes(BODY[:have])
    seen, cached = [], []
    mgr = _manager(md.MainDownloadManager, cached)
    tmp = SimpleNamespace(tempfilepath=part)
    ph = SimpleNamespace(trunicated_filepath=tmp_path / "f.mp4")
    ele = SimpleNamespace(url="x", id=1, username="u", filename="f")

    async def run(c):
        return await mgr._send_req_inner(
            c, ele, tmp, placeholderObj=ph, total=cached_total
        )

    total, _, _ = asyncio.run(_serve(mode, seen, run))
    return total, part.read_bytes(), seen, cached


@pytest.mark.parametrize("have", [300_000, 700_000])
def test_main_resume_appends_missing_bytes(tmp_path, have):
    total, data, seen, cached = _main_resume(tmp_path, have)
    assert seen == [f"bytes={have}-"]
    assert total == T
    assert data == BODY
    assert [c["content-total"] for c in cached] == [T]


def test_main_resume_without_content_range(tmp_path):
    total, data, _, cached = _main_resume(tmp_path, 700_000, mode="no-content-range")
    assert total == T
    assert data == BODY
    assert [c["content-total"] for c in cached] == [T]


@pytest.mark.parametrize("have", [300_000, 700_000])
def test_main_server_ignores_range_restarts(tmp_path, have):
    total, data, seen, cached = _main_resume(tmp_path, have, mode="ignore-range")
    assert seen == [f"bytes={have}-"]
    assert total == T
    assert data == BODY
    assert [c["content-total"] for c in cached] == [T]


def test_main_part_without_cached_size_restarts(tmp_path):
    # no cached size means no Range header, so the server sends everything
    total, data, seen, _ = _main_resume(tmp_path, 300_000, cached_total=None)
    assert seen == [None]
    assert total == T
    assert data == BODY


def test_main_fresh_download(tmp_path):
    total, data, seen, cached = _main_resume(tmp_path, 0, cached_total=None)
    assert seen == [None]
    assert total == T
    assert data == BODY
    assert [c["content-total"] for c in cached] == [T]


def test_mismatched_content_range_is_rejected():
    r = SimpleNamespace(
        status=206,
        headers={"content-length": "10", "content-range": "bytes 0-9/100"},
    )
    with pytest.raises(Exception, match="bytes from 50"):
        dm.DownloadManager()._full_size_from_reply(r, 50, "unused")

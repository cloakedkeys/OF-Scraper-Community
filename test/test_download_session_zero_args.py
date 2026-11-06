"""A 0 passed to the download session must be kept, not replaced.

download_session set its defaults with `x or default`, so 0 retries or a
0 s wait passed to it became the default (#174, item 1). Only None means
"use the default".
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from aiohttp import web

import ofscraper.utils.settings as settings
from ofscraper.managers.sessionmanager.ofsession import download_session

QUIET = SimpleNamespace(
    traceback_=lambda *a, **k: None,
    debug=lambda *a, **k: None,
    warning=lambda *a, **k: None,
)


@pytest.fixture(autouse=True)
def plain_settings(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: SimpleNamespace(
            ssl_verify="false",
            cached_disabled=True,
            download_limit=0,
            command="download",
        ),
    )


def failing_requests(**session_args):
    """Count requests to a server that always answers 500, and time them."""
    calls = []

    async def fail(request):
        calls.append(1)
        return web.Response(status=500, text="try again")

    async def run():
        app = web.Application()
        app.router.add_get("/f", fail)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/f"
        try:
            async with download_session(log=QUIET, **session_args) as c:
                # no auth for a local server
                c._create_headers = lambda headers, *a: headers or {}
                c._create_cookies = lambda: None
                start = time.monotonic()
                with pytest.raises(Exception):
                    async with c.requests_async(url=url):
                        pass
                return len(calls), time.monotonic() - start
        finally:
            await runner.cleanup()

    return asyncio.run(run())


def test_zero_waits_are_kept():
    calls, seconds = failing_requests(retries=3, wait_min=0, wait_max=0)
    assert calls == 3
    # the default waits are 2 to 3 s between tries
    assert seconds < 1, seconds


def test_zero_retries_is_kept():
    # stop_after_attempt(0) stops after the first try
    calls, _ = failing_requests(retries=0, wait_min=0, wait_max=0)
    assert calls == 1


def test_zero_read_timeout_is_kept():
    assert download_session(log=QUIET, read_timeout=0)._read_timeout == 0


def test_none_still_means_the_default():
    c = download_session(log=QUIET)
    assert c._retries > 0
    assert c._wait_min > 0
    assert c._read_timeout > 0

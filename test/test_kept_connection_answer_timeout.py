"""A kept connection that takes a request and never answers is given up on
soon, not after the whole read timeout (#636).

When a kept connection's network path dies without the connection being
closed (a VPN reconnects), the next request over it gets nothing back. It
waited for the read timeout (30 seconds for downloads) before it was retried.
On a connection used before, the answer must now start within
KEPT_ANSWER_TIMEOUT; after that, the read timeout applies as before.

A server that holds the second request without answering stands in for the
dead path. Fixtures and helpers come from test_cdn_connection_reuse.
"""

import asyncio
import logging
import time

import pytest
from aiohttp import web

import ofscraper.managers.sessionmanager.sessionmanager as sm
from test_cdn_connection_reuse import _profile, ssl_verify  # noqa: F401

HOST = "cdn2.onlyfans.com"
BODY = b"x" * 100

# each ssl_verify mode, a host resolved to the local server, no auth read
pytestmark = pytest.mark.usefixtures("ssl_verify")


@pytest.fixture(autouse=True)
def short_bound(monkeypatch):
    monkeypatch.setattr(sm, "KEPT_ANSWER_TIMEOUT", 0.3)


def _fetch(handle, count):
    """Send count requests to HOST, one after another, through a session
    whose read timeout is 30 s. The client port of each request the server
    got, the bodies read, and the seconds it took."""
    ports = []

    async def go():
        held = asyncio.Event()

        async def handler(request):
            ports.append(request.transport.get_extra_info("peername")[1])
            return await handle(request, len(ports), held)

        app = web.Application()
        app.router.add_get("/{path}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        bodies = []
        start = time.monotonic()
        try:
            async with sm.sessionManager(
                retries=2, wait_min=0, wait_max=0, read_timeout=30,
                log=logging.getLogger("test"),
            ) as c:
                for i in range(count):
                    async with c.requests_async(url=f"http://{HOST}:{port}/f{i}") as r:
                        bodies.append(await r.read_())
        finally:
            seconds = time.monotonic() - start
            held.set()
            await runner.cleanup()
        return bodies, seconds

    # a hang fails here rather than after the 30 s read timeout
    bodies, seconds = asyncio.run(asyncio.wait_for(go(), 15))
    return ports, bodies, seconds


def test_a_kept_connection_that_never_answers_is_retried_soon(request):
    _profile(request, HOST)

    async def handle(request, n, held):
        if n == 2:
            # the request arrived over the kept connection; no answer comes
            await held.wait()
        return web.Response(body=BODY)

    ports, bodies, seconds = _fetch(handle, 2)
    assert bodies == [BODY, BODY]
    first, unanswered, retried = ports
    assert unanswered == first
    # the silent connection was closed; the retry went over a new one
    assert retried != first
    assert seconds < 10


def test_a_slow_answer_is_still_waited_for(request):
    # an answer that starts slowly on a new connection, and one whose body
    # comes slowly on a kept connection: the read timeout applies to both
    _profile(request, HOST)

    async def handle(request, n, held):
        if n == 1:
            await asyncio.sleep(1)
            return web.Response(body=BODY)
        slow = web.StreamResponse()
        slow.content_length = len(BODY)
        await slow.prepare(request)
        for part in (BODY[:50], BODY[50:]):
            await asyncio.sleep(1)
            await slow.write(part)
        return slow

    ports, bodies, _ = _fetch(handle, 2)
    assert bodies == [BODY, BODY]
    # one connection, no retry
    assert len(ports) == 2 and ports[0] == ports[1]

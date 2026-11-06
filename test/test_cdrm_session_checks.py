"""An error from cdrm-project must not trigger OnlyFans' error handling.

key_helper_cdrm passed skip_expection_check=True, which requests_async never
read: the option was renamed in 2024 when "skip the rate-limit and auth
checks" became the `exceptions` list, and the call wasn't updated. So a 400
from cdrm-project printed the OnlyFans auth warning and paused 8 s, and a
429 slowed every download through the download session's rate-limit
back-off (#174, item 3).
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from aiohttp import web

import ofscraper.commands.scraper.actions.download.utils.keyhelpers as keyhelpers
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.settings as settings
from ofscraper.managers.sessionmanager.ofsession import download_session
from ofscraper.managers.sessionmanager.sessionmanager import SessionSleep

QUIET = SimpleNamespace(
    traceback_=lambda *a, **k: None,
    debug=lambda *a, **k: None,
    warning=lambda *a, **k: None,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
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
    # no OnlyFans auth, and one try
    monkeypatch.setattr(keyhelpers, "log", QUIET)
    monkeypatch.setattr(keyhelpers.auth_requests, "make_headers", lambda: {})
    monkeypatch.setattr(keyhelpers.auth_requests, "get_cookies_str", lambda: "")
    monkeypatch.setattr(keyhelpers.auth_requests, "create_sign", lambda *a: None)
    monkeypatch.setattr(keyhelpers, "get_cmd_download_req_retries", lambda: 1)


def cdrm_error(monkeypatch, status):
    """Ask a local stand-in for cdrm-project, which answers `status`."""

    async def answer(request):
        return web.Response(status=status, text="error")

    async def run():
        app = web.Application()
        app.router.add_post("/api/decrypt", answer)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/decrypt"
        real = of_env.getattr
        monkeypatch.setattr(
            of_env, "getattr", lambda k, d=None: url if k == "CDRM" else real(k, d)
        )
        warnings = []
        sleeper = SessionSleep(sleep=0, min_sleep=0)
        try:
            # one try, set on the session so this doesn't depend on #154
            async with download_session(
                log=QUIET,
                rate_limit_sleeper=sleeper,
                retries=1,
                wait_min=0,
                wait_max=0,
            ) as c:
                c._create_headers = lambda headers, *a: headers or {}
                c._create_cookies = lambda: None
                c.print_auth_warning = warnings.append
                start = time.monotonic()
                with pytest.raises(Exception):
                    await keyhelpers.key_helper_cdrm(c, "pssh", "licence", "1")
                return time.monotonic() - start, warnings, sleeper.sleep
        finally:
            await runner.cleanup()

    return asyncio.run(run())


def test_cdrm_400_is_not_treated_as_an_onlyfans_auth_error(monkeypatch):
    seconds, warnings, _ = cdrm_error(monkeypatch, 400)
    assert warnings == []
    assert seconds < 4, seconds


def test_cdrm_429_does_not_slow_the_download_session(monkeypatch):
    _, _, sleep = cdrm_error(monkeypatch, 429)
    assert not sleep, sleep

"""Requests without their own read timeout must still time out.

The session took its default read timeout from OF_READ_TIMEOUT, which was
never defined, so it was None: a server that accepted the connection and
then sent nothing hung the run forever, on both the async (aiohttp) and the
sync (httpx) path (#156).
"""

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import aiohttp
import httpx
import pytest

import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.settings as settings
from ofscraper.managers.sessionmanager.sessionmanager import sessionManager
from ofscraper.utils.of_env.values.req.req import load_network_config


@pytest.fixture(autouse=True)
def plain_settings(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: SimpleNamespace(ssl_verify="false", cached_disabled=True),
    )


@pytest.fixture
def one_second_default(monkeypatch):
    """Set the default through its environment variable, as a user would."""
    monkeypatch.setenv("OFSC_READ_TIMEOUT", "1")
    network = load_network_config()
    real = of_env.getattr

    def getattr_(key, default=None):
        if key in network:
            return network[key]
        return real(key, default)

    monkeypatch.setattr(of_env, "getattr", getattr_)


class SilentServer:
    """Accepts connections, reads the request and never answers."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/api"
        self.held = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
                self.held.append(conn)
            except OSError:
                pass

    def close(self):
        self.stop.set()
        self.thread.join(2)
        for conn in self.held:
            conn.close()
        self.sock.close()


QUIET = SimpleNamespace(traceback_=lambda *a, **k: None, debug=lambda *a, **k: None)


def run_with_cap(fetch, cap=10):
    """Run fetch in a thread; report a hang instead of hanging the test."""
    server = SilentServer()
    result = {}

    def worker():
        start = time.monotonic()
        try:
            fetch(server.url)
            result["error"] = None
        except Exception as E:
            result["error"] = E
        result["seconds"] = time.monotonic() - start

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(cap)
    hung = thread.is_alive()
    attempts = len(server.held)
    server.close()
    thread.join(5)
    return hung, result, attempts


def test_default_read_timeout_is_set():
    assert load_network_config()["OF_READ_TIMEOUT"] > 0
    assert sessionManager()._read_timeout > 0


def test_async_request_to_a_silent_server_times_out_and_retries(one_second_default):
    def fetch(url):
        async def go():
            async with sessionManager(
                retries=2, wait_min=0, wait_max=0, log=QUIET
            ) as c:
                async with c.requests_async(url=url):
                    pass

        asyncio.run(go())

    hung, result, attempts = run_with_cap(fetch)
    assert not hung, "the request was still waiting for a response after 10 s"
    assert isinstance(result["error"], aiohttp.ServerTimeoutError), result
    assert attempts == 2
    assert result["seconds"] < 6


def test_sync_request_to_a_silent_server_times_out_and_retries(one_second_default):
    def fetch(url):
        with sessionManager(retries=2, wait_min=0, wait_max=0, log=QUIET) as c:
            with c.requests(url):
                pass

    hung, result, attempts = run_with_cap(fetch)
    assert not hung, "the request was still waiting for a response after 10 s"
    assert isinstance(result["error"], httpx.ReadTimeout), result
    assert attempts == 2
    assert result["seconds"] < 6

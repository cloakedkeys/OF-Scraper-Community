import logging
from types import SimpleNamespace

import httpx
import pytest

import ofscraper.data.api.init as init
import ofscraper.utils.checkers as checkers


class AuthPrompted(Exception):
    pass


@pytest.fixture
def auth_check(monkeypatch):
    monkeypatch.setattr(
        init.settings, "get_settings", lambda: SimpleNamespace(anon=False)
    )
    monkeypatch.setattr(checkers.auth_file, "read_auth", lambda: {})
    # traceback_ is added to loggers by the app's log setup
    monkeypatch.setattr(init.log, "traceback_", lambda *a, **k: None, raising=False)

    def make_auth(*args, **kwargs):
        raise AuthPrompted

    monkeypatch.setattr(checkers.make, "make_auth", make_auth)

    def run(error):
        def scrape_user():
            raise error

        monkeypatch.setattr(init.me, "scrape_user", scrape_user)
        checkers.check_auth()

    return run


def _request(url):
    return httpx.Request("GET", url)


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            httpx.ConnectTimeout(
                "timed out",
                request=_request("https://raw.githubusercontent.com/rules.json"),
            ),
            "timed out",
        ),
        (
            httpx.ConnectError(
                "connection refused",
                request=_request("https://onlyfans.com/api2/v2/users/me"),
            ),
            "connection failed",
        ),
    ],
)
def test_network_error_is_reported_as_network_problem(
    auth_check, caplog, error, reason
):
    host = error.request.url.host
    with caplog.at_level(logging.INFO, logger="shared"):
        # the user is not asked to enter their auth again
        with pytest.raises(SystemExit):
            auth_check(error)

    assert f"Couldn't reach {host} ({reason})" in caplog.text
    assert "Auth Failed" not in caplog.text


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_auth_still_asks_for_auth(auth_check, caplog, status):
    request = _request("https://onlyfans.com/api2/v2/users/me")
    error = httpx.HTTPStatusError(
        "rejected", request=request, response=httpx.Response(status, request=request)
    )
    with caplog.at_level(logging.INFO, logger="shared"):
        with pytest.raises(AuthPrompted):
            auth_check(error)

    assert "Auth Failed" in caplog.text
    assert "Couldn't reach" not in caplog.text

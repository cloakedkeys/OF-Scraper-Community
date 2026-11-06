from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import ofscraper.data.api.init as init
import ofscraper.data.api.me as me
import ofscraper.utils.auth.request as auth_requests


@pytest.fixture
def me_replies(monkeypatch):
    """/me answers with the given JSON through the real me.scrape_user."""
    monkeypatch.setattr(
        init.settings, "get_settings", lambda: SimpleNamespace(anon=False)
    )
    monkeypatch.setattr(init.log, "traceback_", lambda *a, **k: None, raising=False)
    # signing isn't part of this test
    monkeypatch.setattr(auth_requests, "get_request_auth", lambda: None)

    def set_reply(data):
        @contextmanager
        def requests(url, *args, **kwargs):
            yield SimpleNamespace(json_=lambda: data)

        @contextmanager
        def get_ofsession(*args, **kwargs):
            yield SimpleNamespace(requests=requests)

        monkeypatch.setattr(
            me.manager,
            "Manager",
            SimpleNamespace(session=SimpleNamespace(get_ofsession=get_ofsession)),
        )

    return set_reply


def test_logged_out_session_is_reported_as_bad_auth(me_replies):
    me_replies({"isAuth": False})
    assert init.getstatus() == "DOWN"


def test_logged_in_session_passes(me_replies):
    me_replies({"isAuth": True, "id": 1, "username": "u1", "name": "n1"})
    assert init.getstatus() == "UP"

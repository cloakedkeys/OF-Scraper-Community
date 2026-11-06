"""The OnlyFans session cookie must never reach a log file unmasked (#165)."""

import logging

import pytest

import ofscraper.utils.auth.file as auth_file
import ofscraper.utils.logs.utils.sensitive as sensitive
from ofscraper.utils.logs.classes.classes import DiscordFormatter, LogFileFormatter

# fake value with regex metacharacters, so an unescaped pattern would misbehave
SESS = "fake.sess+value/with?regex*chars"


@pytest.fixture(autouse=True)
def clean_patterns(monkeypatch):
    monkeypatch.setattr(sensitive, "_USER_ADDED_PATTERNS", {})
    monkeypatch.setattr(sensitive.paths, "get_username", lambda: None)


@pytest.fixture
def fake_auth(monkeypatch):
    auth = {"sess": SESS, "auth_id": "1", "auth_uid": "", "user_agent": "ua", "x-bc": "bc"}
    monkeypatch.setattr(auth_file.auth_dict, "get_auth_dict", lambda *a, **k: dict(auth))
    monkeypatch.setattr(auth_file.auth_schema, "auth_schema", lambda a: dict(a))
    monkeypatch.setattr(auth_file.auth_schema, "auth_key_missing", lambda a: False)
    monkeypatch.setattr(auth_file.auth_schema, "auth_key_null", lambda a: False)
    return auth


def formatted(formatter, msg):
    record = logging.LogRecord("shared", logging.DEBUG, __file__, 1, msg, None, None)
    return formatter.format(record)


# the forms the value is logged in: the failed-request header dump in
# sessionmanager (a dict repr holding the cookie header), a cookie dict, JSON
LINES = [
    "requests headers " + str({"cookie": f"sess={SESS}; auth_id=1", "user-agent": "ua"}),
    "cookies " + str({"sess": SESS, "auth_id": "1"}),
    '{"sess": "' + SESS + '"}',
]


@pytest.mark.parametrize("formatter", [LogFileFormatter, DiscordFormatter])
@pytest.mark.parametrize("line", LINES)
def test_sess_is_masked_once_auth_is_read(fake_auth, formatter, line):
    auth_file.read_auth()
    out = formatted(formatter(), line)
    assert SESS not in out
    assert "{sess}" in out
    assert "LOGGING ERROR" not in out


def test_reading_auth_again_does_not_add_patterns(fake_auth):
    # add_cookies() reads auth on every request
    for _ in range(5):
        auth_file.read_auth()
    assert len(sensitive._USER_ADDED_PATTERNS) == 1


def test_empty_sess_registers_nothing(fake_auth):
    fake_auth["sess"] = ""
    auth_file.read_auth()
    assert sensitive._USER_ADDED_PATTERNS == {}

"""No part of the PostgreSQL password in a message, whatever it is made of
(#647). The addresses and passwords here are made up."""

import logging
import re
import sys
import urllib.parse

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.db.postgres as postgres
import ofscraper.utils.logs.utils.sensitive as sensitive
from ofscraper.utils.logs.classes.classes import SensitiveFormatter

UNREADABLE = "the configured database"

# (address, its password, whether PostgreSQL reads it as it was meant)
ADDRESSES = [
    ("postgresql://scraper:12345?xyz@db.example:5432/library", "12345?xyz", True),
    ("postgresql://scraper:Kq#Wz8@db.example:5432/library", "Kq#Wz8", True),
    ("postgresql://scraper:Ab:Cd9@db.example/library", "Ab:Cd9", True),
    ("postgresql://scraper:Zq@db.example/library", "Zq", True),
    # a word the driver's own message has
    ("postgresql://scraper:failed@db.example/library", "failed", True),
    ("postgresql://scraper:Mn%40Rt%3Fu%23v@db.example/library", "Mn@Rt?u#v", True),
    ("host=db.example dbname=library user=scraper password=Hg@Lm?k#9", "Hg@Lm?k#9", None),
    ("host=db.example dbname=library password='Rf Yt@Ou'", "Rf Yt@Ou", None),
    # not as meant: what follows the first @ is taken for the server
    ("postgresql://scraper:p@ssw@rd@db.example/library", "p@ssw@rd", False),
    # not as meant: the / ends the login part before the @
    ("postgresql://scraper:Xy/Zw42@db.example/library", "Xy/Zw42", False),
    # not as meant: PostgreSQL refuses a space in a URL
    ("postgresql://scraper:Qj Vwx@db.example/library", "Qj Vwx", False),
]


@pytest.fixture(params=["driver", "no driver"])
def driver(request, monkeypatch):
    if request.param == "driver":
        pytest.importorskip("psycopg")
    else:
        monkeypatch.setitem(sys.modules, "psycopg", None)
        monkeypatch.setitem(sys.modules, "psycopg.conninfo", None)
    return request.param


def said(address, password):
    """What the driver or Python could quote back for this address."""
    after_at = address.split("@", 1)[-1].split("/")[0]
    try:
        python_reading = urllib.parse.urlsplit(address).netloc
    except ValueError:
        python_reading = address
    return [
        f'missing "=" after "{address}" in connection info string',
        f"authentication failed (given {password})",
        f"given {urllib.parse.quote(password, safe='')}",
        f"failed to resolve host '{after_at}'",
        f"Can't use the PostgreSQL database at {python_reading}: failed",
    ]


def pieces(password):
    return [piece for piece in re.split(r"[^A-Za-z0-9]+", password) if len(piece) >= 2]


def assert_none_of(password, text):
    for part in [password, urllib.parse.quote(password, safe=""), *pieces(password)]:
        assert part not in text


@pytest.mark.parametrize("address, password, as_meant", ADDRESSES)
def test_no_part_of_the_password_is_shown(driver, address, password, as_meant):
    for text in said(address, password):
        assert_none_of(password, postgres.masked(text, address))
    shown = postgres.describe(address)
    assert_none_of(password, shown)
    if as_meant is False:
        # nothing of an address that wasn't read as it was meant
        assert shown == UNREADABLE
    elif as_meant:
        assert shown.startswith("db.example")


def test_a_normal_address_still_names_the_server(driver):
    address = "postgresql://scraper:s3cret@db.example:5433/library"
    assert postgres.describe(address) == "db.example:5433/library"
    masked = postgres.masked(f"could not reach db.example:5433 (s3cret, {address})", address)
    assert masked == "could not reach db.example:5433 ({hidden}, {hidden})"


def test_the_password_is_hidden_wherever_it_is(driver):
    address = "postgresql://scraper:Zq@db.example/library"
    assert postgres.masked("Zqx given Zq.", address) == "{hidden}x given {hidden}."


def test_a_piece_of_the_password_is_hidden_only_where_it_stands_alone(driver):
    address = "postgresql://scraper:s3cr%40t-word@db.example/library"
    assert postgres.masked("password word", address) == "password {hidden}"


def fragments(password):
    """Every 2+ character stretch of the password with a letter or digit."""
    return {
        password[i:j]
        for i in range(len(password))
        for j in range(i + 2, len(password) + 1)
        if re.search(r"[A-Za-z0-9]", password[i:j])
    }


def assert_no_fragment(password, text, own=""):
    """No fragment of the password in `text`, but for those the message's
    own words (`own`) have."""
    shown = text.replace("{hidden}", "\0")
    assert [part for part in fragments(password) if part in shown and part not in own] == []


@pytest.mark.parametrize("password", ["YD%BDS", "k%aCs", "%f01w", "98%B3X"])
def test_a_percent_escape_that_isnt_utf8_is_not_an_error(driver, monkeypatch, password):
    address = f"postgresql://scraper:{password}@127.0.0.1:5432/library"
    assert postgres.describe(address) == UNREADABLE
    assert_no_fragment(password, postgres.masked(f"given {password} for {address}", address))
    # and the log lines hide it from then on
    monkeypatch.setattr(sensitive, "_USER_ADDED_PATTERNS", {})
    postgres.POSTGRES._hide_password(address)
    record = logging.LogRecord("shared", logging.ERROR, __file__, 1, f"tried {password}", None, None)
    assert_no_fragment(password, SensitiveFormatter("%(message)s").format(record))


@pytest.mark.parametrize(
    "address, password, said",
    [
        # an unquoted password with a space and an =: its second word is
        # read as an option's name
        ("host=db.example dbname=library password=DP UJ=R44", "DP UJ=R44", 'invalid connection option "UJ"'),
        ("host=db.example dbname=library password=55B Z#N", "55B Z#N", 'missing "=" after "Z#N" in connection info string'),
        ("postgresql://scraper:F& Z=@LK@db.example/library", "F& Z=@LK", 'unexpected spaces found in "F& Z="'),
        # a quoted part of the address, cut where the driver cut it
        ("postgresql://scraper:B+E8W @+@N@db.example/library", "B+E8W @+@N", "failed to resolve host '+@N@db.example'"),
        (
            "postgresql://scraper:@:?@R:/S%@db.example/library",
            "@:?@R:/S%",
            'missing key/value separator "=" in URI query parameter: "@R:/S%@db.example/library"',
        ),
    ],
)
def test_what_the_driver_quotes_of_an_unreadable_address_is_hidden(driver, address, password, said):
    assert_no_fragment(password, postgres.masked(said, address))
    assert postgres.describe(address) == UNREADABLE


def test_a_password_that_looks_like_a_query_is_not_trusted(driver):
    # PostgreSQL would read the password after the ? instead
    address = "postgresql://scraper:Secret/Pw?password=1@db.example/library"
    assert postgres.describe(address) == UNREADABLE
    own = 'connection to server at "{}" failed (port "{}", dbname "{}")'
    said = own.format("scraper", "Secret", "Pw?password=1@db.example/library")
    assert_no_fragment("Secret/Pw?password=1", postgres.masked(said, address), own)


def test_a_one_character_password_is_not_hidden_from_every_log_line(driver, monkeypatch):
    monkeypatch.setattr(sensitive, "_USER_ADDED_PATTERNS", {})
    address = "postgresql://scraper:!@db.example/library"
    postgres.POSTGRES._hide_password(address)
    assert re.escape("!") not in sensitive._USER_ADDED_PATTERNS
    # a message still hides it
    assert postgres.masked("given !", address) == "given {hidden}"

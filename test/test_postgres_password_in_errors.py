"""No part of the PostgreSQL password in a message, whatever it is made of
(#647). The addresses and passwords here are made up."""

import re
import sys
import urllib.parse

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.db.postgres as postgres

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


def test_letters_alone_are_hidden_only_where_they_stand_alone(driver):
    address = "postgresql://scraper:Zq@db.example/library"
    assert postgres.masked("Zqx given Zq.", address) == "Zqx given {hidden}."
    # a piece of the password, inside a word of the message
    address = "postgresql://scraper:s3cr%40t-word@db.example/library"
    assert postgres.masked("password word", address) == "password {hidden}"

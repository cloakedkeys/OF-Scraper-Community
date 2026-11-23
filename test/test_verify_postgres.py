"""ofscraper verify on PostgreSQL: the same findings as from the file.

The server is a stand-in (postgres_stand_in.py); test_verify_postgres_live.py
runs check_same_findings against a real server in CI. The files are real
ones, under pytest's tmp_path.
"""

import pytest

import ofscraper.commands.verify as verify
import ofscraper.db.postgres as postgres
from test.test_verify import library  # noqa: F401  (the fixture)

URL = "postgresql://scraper:s3cret-word@db.example:5433/library"


@pytest.fixture(autouse=True)
def no_url(monkeypatch):
    monkeypatch.delenv(postgres.URL_KEY, raising=False)


@pytest.fixture
def server(monkeypatch):
    pytest.importorskip("psycopg")
    from test.postgres_stand_in import Server

    server = Server()
    monkeypatch.setattr(postgres, "Reader", server.reader)
    monkeypatch.setattr(postgres, "Writer", server.writer)
    yield server
    server.close()


def without_names(report):
    for creator in report["creators"]:
        creator.pop("database")
        creator.pop("creator", None)
    return report


def check_same_findings(database_url, db):
    from_file = without_names(verify.verify([db]))
    from_postgres = without_names(verify.verify_postgres(database_url, "5", site="onlyfans"))
    assert from_postgres == from_file
    assert from_file["totals"]["missing"] == 3


def test_the_same_rows_give_the_same_findings_from_either(library, server, tmp_path):  # noqa: F811
    db, _ = library
    server.bring_in(tmp_path / ".data", tmp_path / "staging")
    check_same_findings(URL, db)


def test_unknown_creator(library, server, tmp_path):  # noqa: F811
    server.bring_in(tmp_path / ".data", tmp_path / "staging")
    with pytest.raises(Exception, match="no creator named nobody"):
        verify.verify_postgres(URL, "nobody")

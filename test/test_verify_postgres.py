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


def medias(database_url):
    import ofscraper.db.creator_rows as creator_rows

    with creator_rows.in_postgres(database_url, ["onlyfans"], ["5"]) as (rows,):
        return sorted(tuple(r) for r in rows.select("medias", ["media_id", "post_id", "downloaded"]))


def check_apply_and_undo(database_url, library, tmp_path):
    db, lib = library
    before = medias(database_url)
    with pytest.raises(ValueError, match="undo file"):
        verify.verify_postgres(database_url, "5", apply=True)
    undo_dir = tmp_path / "undo"
    undo_dir.mkdir()
    report = verify.verify_postgres(database_url, "5", apply=True, undo_dir=undo_dir)
    (creator,) = report["creators"]
    assert creator["media_reset"] == 2 and creator["files_moved_aside"] == 1
    reset = {m for m, _, d in medias(database_url) if d == 0} - {m for m, _, d in before if d == 0}
    assert reset == {2, 3}
    assert (lib / "empty.jpg.bad").exists()
    undo_file = creator["undo_file"]
    assert verify.undo_postgres(database_url, "5", undo_file)["rows_to_restore"] == 2
    assert verify.undo_postgres(database_url, "5", undo_file, apply=True)["files_to_rename_back"] == 1
    assert medias(database_url) == before
    assert (lib / "empty.jpg").exists() and not (lib / "empty.jpg.bad").exists()


def test_apply_and_undo(library, server, tmp_path):  # noqa: F811
    server.bring_in(tmp_path / ".data", tmp_path / "staging")
    check_apply_and_undo(URL, library, tmp_path)


def test_a_row_changed_meanwhile_changes_nothing(library, server, tmp_path, monkeypatch):  # noqa: F811
    import ofscraper.db.creator_rows as creator_rows

    db, lib = library
    server.bring_in(tmp_path / ".data", tmp_path / "staging")
    before = medias(URL)
    real = creator_rows.PostgresRows.change
    monkeypatch.setattr(creator_rows.PostgresRows, "change", lambda *a: False)
    with pytest.raises(postgres.ChangedMeanwhile):
        verify.verify_postgres(URL, "5", apply=True, undo_dir=tmp_path)
    monkeypatch.setattr(creator_rows.PostgresRows, "change", real)
    assert medias(URL) == before
    assert (lib / "empty.jpg").exists() and not list(tmp_path.glob("*.verify-undo-*"))

"""The optional PostgreSQL engine (#608): everything that needs no server.

The engine itself is run against a real server in test_db_postgres_live,
which CI runs with a PostgreSQL container.
"""

import asyncio
import builtins
import contextlib
import logging
import sqlite3

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.classes.placeholder as placeholder
import ofscraper.db.operations as operations
import ofscraper.db.operations_.wrapper as wrapper
import ofscraper.db.postgres as postgres
import ofscraper.main.open.run as run
import ofscraper.utils.logs.classes.classes as log_classes
from ofscraper.db.difference import ALL_FLAGS
from ofscraper.db.transition import modify_tables
from test.db_scenario import check, scenario

URL = "postgresql://scraper:s3cr%40t-word@db.example:5433/library"


@pytest.fixture
def sqlite_file(tmp_path, monkeypatch):
    """Every creator's database is one SQLite file, as with a `metadata`
    folder that doesn't name the creator."""
    path = tmp_path / "user_data.db"
    monkeypatch.delenv(postgres.URL_KEY, raising=False)
    monkeypatch.setattr(
        placeholder.databasePlaceholder,
        "databasePathHelper",
        lambda self, model_id, model_username: path,
    )
    return path


def test_the_scenario_on_sqlite(sqlite_file):
    check(asyncio.run(scenario()))


# --- which engine ---


def test_sqlite_is_used_without_a_url(monkeypatch):
    monkeypatch.delenv(postgres.URL_KEY, raising=False)
    assert postgres.url() == ""
    assert wrapper.backend_for({}) is wrapper.SQLITE


def test_a_url_selects_postgresql_but_a_named_file_is_still_sqlite(monkeypatch):
    pytest.importorskip("psycopg")
    monkeypatch.setenv(postgres.URL_KEY, f"  {URL}  ")
    assert postgres.url() == URL
    assert wrapper.backend_for({}) is postgres.POSTGRES
    assert wrapper.backend_for({"model_id": 5}) is postgres.POSTGRES
    # merge, the upgrade of one file and the tests name their file
    assert wrapper.backend_for({"db_path": "some.db"}) is wrapper.SQLITE
    psycopg = pytest.importorskip("psycopg")
    assert psycopg.Error in wrapper.DB_ERRORS
    assert sqlite3.Error in wrapper.DB_ERRORS


def test_a_missing_driver_is_one_sentence(monkeypatch):
    real_import = builtins.__import__

    def no_driver(name, *args, **kwargs):
        if name.split(".")[0] in ("psycopg", "psycopg_pool"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_driver)

    with pytest.raises(postgres.PostgresUnavailable) as raised:
        postgres._driver()
    message = str(raised.value)
    assert postgres.URL_KEY in message
    assert "[postgres]" in message
    assert "\n" not in message
    # not an error a retry can fix
    assert wrapper._retryable(raised.value) is False


def test_the_startup_check_does_nothing_for_sqlite(monkeypatch):
    monkeypatch.delenv(postgres.URL_KEY, raising=False)
    monkeypatch.setattr(
        postgres.POSTGRES, "check", lambda: pytest.fail("no server to check")
    )
    run.check_database()


def test_the_startup_check_asks_the_server_when_a_url_is_set(monkeypatch, caplog):
    monkeypatch.setenv(postgres.URL_KEY, URL)
    monkeypatch.setattr(postgres.POSTGRES, "check", lambda: "16.4")

    with caplog.at_level(logging.INFO, logger="shared"):
        run.check_database()

    assert "db.example:5433/library" in caplog.text
    assert "16.4" in caplog.text
    assert "s3cr" not in caplog.text
    assert "scraper" not in caplog.text


# --- the password ---


@pytest.mark.parametrize(
    "url, password",
    [
        (URL, "s3cr@t-word"),
        ("host=db.example dbname=library user=scraper password=s3cr@t-word", "s3cr@t-word"),
        ("host=db.example password='two words'", "two words"),
        ("postgresql://db.example/library", None),
        ("", None),
    ],
)
def test_the_password_is_found_in_either_spelling(url, password):
    assert postgres._password(url) == password


def test_messages_name_the_server_and_never_the_login():
    assert postgres.describe(URL) == "db.example:5433/library"
    assert postgres.describe("host=db.example password=x") == "the configured database"
    masked = postgres.masked(f"could not connect to {URL}: s3cr@t-word", URL)
    assert "s3cr" not in masked
    assert "{hidden}" in masked


def test_the_password_is_hidden_from_every_log_line(monkeypatch):
    import ofscraper.utils.logs.utils.sensitive as sensitive

    monkeypatch.setattr(sensitive, "_USER_ADDED_PATTERNS", {})
    postgres.POSTGRES._hide_password(URL)

    record = logging.LogRecord(
        "shared", logging.ERROR, __file__, 1,
        f"connection to {URL} failed (password s3cr@t-word)", None, None,
    )
    line = log_classes.SensitiveFormatter("%(message)s").format(record)

    assert "s3cr" not in line
    assert "db.example" in line


# --- the SQL and the values ---


def test_placeholders_are_translated_outside_quotes():
    assert (
        postgres.translate("SELECT a FROM t WHERE b=(?) AND c = ? AND d LIKE '100%?'")
        == "SELECT a FROM t WHERE b=(%s) AND c = %s AND d LIKE '100%%?'"
    )


def test_a_row_reads_like_an_sqlite_row():
    row = postgres._row_class(("media_id", "Post_ID"))((1, 2))
    assert row[0] == 1
    assert row["post_id"] == 2 and row["POST_ID"] == 2
    assert dict(row) == {"media_id": 1, "Post_ID": 2}
    assert row.keys() == ["media_id", "Post_ID"]
    media_id, post_id = row
    assert (media_id, post_id) == (1, 2)
    assert row == (1, 2)
    with pytest.raises(IndexError):
        row["nothing"]


@pytest.mark.parametrize(
    "text, iso",
    [
        ("2024-01-02 03:04:05+00", "2024-01-02T03:04:05+00:00"),
        ("2024-01-02 03:04:05.25+00", "2024-01-02T03:04:05.25+00:00"),
        ("2024-01-02 03:04:05+05:30", "2024-01-02T03:04:05+05:30"),
    ],
)
def test_a_timestamp_comes_back_as_iso_text(text, iso):
    assert postgres.iso(text) == iso


@pytest.mark.parametrize("text, value", [("5", 5), ("5.00", 5), ("4.99", 4.99), ("-2", -2)])
def test_a_numeric_comes_back_as_sqlite_gave_it(text, value):
    result = postgres.number(text)
    assert result == value and type(result) is type(value)


@pytest.mark.parametrize(
    "value, saved",
    [
        ("2023-01-01T12:00:00+00:00", "2023-01-01T12:00:00+00:00"),
        ("2023-06-01T10:00:00+02:00", "2023-06-01T10:00:00+02:00"),
        # no offset is UTC, as the app reads it back
        ("2023-01-01T12:00:00", "2023-01-01T12:00:00+00:00"),
        ("2023-01-01 12:00:00", "2023-01-01T12:00:00+00:00"),
        # seconds since 1970, as a number or as text
        (1700000000, "2023-11-14T22:13:20+00:00"),
        ("1700000000", "2023-11-14T22:13:20+00:00"),
        ("", None),
        (None, None),
    ],
)
def test_a_date_is_saved_as_one_format(value, saved):
    assert postgres.date_value(value) == saved


def test_sqlite_saves_a_date_as_it_came(tmp_path):
    with contextlib.closing(sqlite3.connect(tmp_path / "a.db")) as conn:
        assert wrapper.date_value(conn, "2023-01-01 12:00:00") == "2023-01-01 12:00:00"
        assert wrapper.date_value(conn, 1700000000) == 1700000000
        assert wrapper.date_value(conn, None) is None


def test_values_go_in_as_untyped_text_and_come_out_as_sqlite_gave_them():
    psycopg = pytest.importorskip("psycopg")
    from psycopg.adapt import PyFormat, Transformer

    adapters = postgres._adapters()
    transformer = Transformer(adapters)

    def sent(value):
        dumper = transformer.get_dumper(value, PyFormat.AUTO)
        return dumper.oid, bytes(dumper.dump(value))

    # oid 0: the server reads the text as its column's type
    assert sent(1) == (0, b"1")
    assert sent(True) == (0, b"1")
    assert sent(False) == (0, b"0")
    assert sent(4.99) == (0, b"4.99")
    assert sent(712345678901234567) == (0, b"712345678901234567")
    assert sent("text") == (0, b"text")

    def read(type_name, data):
        loader = transformer.get_loader(adapters.types[type_name].oid, psycopg.pq.Format.TEXT)
        return loader.load(data)

    assert read("bool", b"t") == 1 and type(read("bool", b"t")) is int
    assert read("bool", b"f") == 0
    assert read("timestamptz", b"2024-01-02 03:04:05+00") == "2024-01-02T03:04:05+00:00"
    assert read("numeric", b"4.99") == 4.99
    assert read("int8", b"712345678901234567") == 712345678901234567


@pytest.mark.parametrize("site", ["onlyfans", "fansly", "a_site2"])
def test_a_site_names_its_schema(site):
    assert postgres.schema_name(site) == site


@pytest.mark.parametrize("site", ["", None, "two words", "x;drop", '"quoted"', "1site"])
def test_a_schema_name_is_never_built_from_odd_text(site):
    with pytest.raises(postgres.PostgresUnavailable):
        postgres.schema_name(site)


# --- the tables ---


def sqlite_columns(tmp_path):
    path = tmp_path / "current.db"

    async def make():
        await operations.create_tables(db_path=path)
        await modify_tables(db_path=path, new_db=True)

    asyncio.run(make())
    with contextlib.closing(sqlite3.connect(path)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: [row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")]
            for table in tables
        }


def test_postgresql_has_the_tables_and_columns_sqlite_has(tmp_path):
    # a column added to SQLite needs adding to postgres.TABLES too, with the
    # step that adds it to a PostgreSQL database made before
    expected = sqlite_columns(tmp_path)
    assert set(postgres.TABLES) == set(expected)
    for table, columns in expected.items():
        assert set(postgres.TABLE_COLUMNS[table]) == set(columns), table


def test_a_new_postgresql_database_has_had_every_upgrade_step(tmp_path):
    # the flags a current SQLite file has are the ones PostgreSQL starts with
    path = tmp_path / "current.db"

    async def make():
        await operations.create_tables(db_path=path)
        await modify_tables(db_path=path, new_db=True)

    asyncio.run(make())
    with contextlib.closing(sqlite3.connect(path)) as conn:
        done = {row[0] for row in conn.execute("SELECT flag_name FROM schema_flags")}
    assert done == set(ALL_FLAGS)


def test_sqlite_files_are_still_prepared_the_old_way(sqlite_file):
    assert wrapper.SQLITE.born_current is False

    async def start():
        await operations.table_init_create(model_id=5, username="a_creator")

    asyncio.run(start())

    with contextlib.closing(sqlite3.connect(sqlite_file)) as conn:
        assert conn.execute("SELECT model_id FROM models").fetchall() == [(5,)]

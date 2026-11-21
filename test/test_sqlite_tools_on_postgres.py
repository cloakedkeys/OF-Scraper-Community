"""With PostgreSQL on, a tool that still reads user_data.db files says so (#631).

The sentence and how a tool run on its own finds out that PostgreSQL is on.
Each tool's own test is beside that tool's other tests. No server is needed:
only the setting is read.
"""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

import ofscraper.db.postgres as postgres
from ofscraper.db.operations_.media import mediaCreate

URL = "postgresql://scraper:s3cret-word@db.example:5433/library"
TOOL = [sys.executable, "-m", "ofscraper.commands.recover_collisions"]


@pytest.fixture(autouse=True)
def no_url(monkeypatch):
    monkeypatch.delenv(postgres.URL_KEY, raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder of the test's own, so that the tool looks for the
    scraper's config.json there."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def database(tmp_path):
    db = tmp_path / "user_data.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(mediaCreate)
    return db


def config_json(home, env_files):
    path = home / ".config" / "ofscraper" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"advanced_options": {"env_files": [str(f) for f in env_files]}}),
        encoding="utf-8",
    )
    return path


def run_tool(database):
    """The tool as a user starts it: a process of its own. No one is there
    to answer a question."""
    return subprocess.run(
        TOOL + [str(database)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


# --- the sentence ---


def test_nothing_is_said_while_postgresql_is_off():
    assert postgres.sqlite_files_notice("stash_sync") == ""


def test_the_notice_says_what_is_read_and_what_is_missed(monkeypatch):
    monkeypatch.setenv(postgres.URL_KEY, URL)

    notice = postgres.sqlite_files_notice("stash_sync")

    assert notice == (
        "PostgreSQL is on (OFSC_DATABASE_URL is set), but stash_sync still"
        " reads the user_data.db files, not PostgreSQL. Anything scraped since"
        " PostgreSQL was turned on is not in those files, so stash_sync will"
        " not see it. See POSTGRESQL.md."
    )


def test_the_notice_never_holds_the_address(monkeypatch):
    monkeypatch.setenv(postgres.URL_KEY, URL)

    notice = postgres.sqlite_files_notice("stash_sync")

    assert "s3cret-word" not in notice
    assert "db.example" not in notice


def test_the_notice_asks_no_server(monkeypatch):
    monkeypatch.setenv(postgres.URL_KEY, URL)

    def asked(*args, **kwargs):
        raise AssertionError("the server was asked")

    monkeypatch.setattr(postgres.POSTGRES, "connect", asked)
    monkeypatch.setattr(postgres.POSTGRES, "check", asked)

    assert postgres.sqlite_files_notice("stash_sync")


# --- a tool run on its own ---


def test_a_tool_sees_a_url_in_the_environment(home, database, monkeypatch):
    monkeypatch.setenv(postgres.URL_KEY, URL)

    result = run_tool(database)

    assert result.returncode == 0, result.stderr
    assert result.stderr.startswith("warning: PostgreSQL is on")
    assert "recover_collisions still reads the user_data.db files" in result.stderr
    # the report is still the only thing on stdout
    assert json.loads(result.stdout)["applied"] is False


def test_a_tool_sees_a_url_in_an_env_file_config_json_names(home, database):
    env_file = home / "postgres.env"
    env_file.write_text(f"{postgres.URL_KEY}={URL}\n", encoding="utf-8")
    config = config_json(home, [env_file])
    before = config.read_bytes()

    result = run_tool(database)

    assert result.returncode == 0, result.stderr
    assert "recover_collisions still reads the user_data.db files" in result.stderr
    assert json.loads(result.stdout)["applied"] is False
    assert config.read_bytes() == before


def test_a_tool_says_nothing_while_postgresql_is_off(home, database):
    config = config_json(home, [])
    before = config.read_bytes()

    result = run_tool(database)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["applied"] is False
    assert config.read_bytes() == before


def test_a_tool_makes_no_config_json_where_there_is_none(home, database):
    result = run_tool(database)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["applied"] is False
    assert list(home.iterdir()) == []


def test_a_tool_still_runs_when_an_env_file_is_missing(home, database, monkeypatch):
    monkeypatch.setenv(postgres.URL_KEY, URL)
    config_json(home, [home / "not-there.env"])

    result = run_tool(database)

    assert result.returncode == 0, result.stderr
    assert "recover_collisions still reads the user_data.db files" in result.stderr
    assert json.loads(result.stdout)["applied"] is False


@pytest.mark.parametrize("written", ["{", "[]"])
def test_a_tool_asks_nothing_about_a_config_json_it_cant_read(home, database, written):
    config = config_json(home, [])
    config.write_text(written, encoding="utf-8")

    result = run_tool(database)

    # the scraper would ask what to do with the file; the tool leaves it
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["applied"] is False
    assert config.read_text(encoding="utf-8") == written

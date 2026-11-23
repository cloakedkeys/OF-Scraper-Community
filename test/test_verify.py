"""ofscraper verify: the library checked against the database, offline."""

import json
import os
import sqlite3
from contextlib import closing

import pytest

import ofscraper.commands.verify as verify
from ofscraper.db.operations_.media import mediaCreate
from ofscraper.db.operations_.posts import postCreate


def snapshot(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def make_library(tmp_path, rows, posts=()):
    """A creator's database at tmp_path/.data/5/user_data.db, its files under
    tmp_path/lib. rows: (media_id, post_id, filename, recorded size, bytes on
    disk or None for no file, api_type, extra columns)."""
    db = tmp_path / ".data" / "5" / "user_data.db"
    db.parent.mkdir(parents=True)
    lib = tmp_path / "lib"
    lib.mkdir()
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(mediaCreate)
        conn.execute(postCreate)
        for post_id, deleted in posts:
            conn.execute("INSERT INTO posts (post_id, text, model_id, is_deleted) VALUES (?, '', 5, ?)", (post_id, deleted))
        for media_id, post_id, name, size, content, api_type, extra in rows:
            if content is not None and name:
                (lib / name).write_bytes(content)
            conn.execute(
                "INSERT INTO medias (media_id, post_id, directory, filename, size, api_type, media_type,"
                " downloaded, model_id, unlocked, skip_permanent) VALUES (?,?,?,?,?,?,'Images',?,5,?,?)",
                (media_id, post_id, str(lib) if name else None, name, size, api_type,
                 extra.get("downloaded", 1), extra.get("unlocked", 1), extra.get("skip_permanent", 0)),
            )
    return db, lib


@pytest.fixture
def library(tmp_path):
    return make_library(tmp_path, [
        (1, 100, "ok.jpg", 3, b"abc", "Timeline", {}),
        (2, 101, "gone.jpg", 3, None, "Timeline", {}),
        (3, 102, "empty.jpg", 3, b"", "Messages", {}),
        (4, 103, "edited.jpg", 3, b"abcdef", "Timeline", {}),
        (5, 104, None, None, None, "Timeline", {"skip_permanent": 1}),
        (6, 105, "story.jpg", 3, None, "Stories", {}),
        (7, 106, "notyet.jpg", 3, None, "Timeline", {"downloaded": 0}),
        # one media in two posts: one row's file is fine, so the media is
        (8, 107, "shared.jpg", 3, b"abc", "Timeline", {}),
        (8, 108, "shared.jpg", 3, None, "Archived", {}),
        (9, 109, "deletedpost.jpg", 3, None, "Timeline", {}),
    ], posts=[(109, 1)])


def test_findings(library):
    db, lib = library
    report = verify.verify([db])
    (creator,) = report["creators"]
    assert {k: creator[k] for k in ("missing", "empty", "size_differs", "no_file_recorded", "ok")} == {
        "missing": 3, "empty": 1, "size_differs": 1, "no_file_recorded": 1, "ok": 2,
    }
    assert creator["skip_permanent"] == 1
    assert creator["media_checked"] == 8  # media 7 isn't marked downloaded
    by_media = {f["media_id"]: f for f in creator["findings"]}
    assert set(by_media) == {"2", "3", "4", "6", "9"}
    assert by_media["2"]["finding"] == "missing" and by_media["2"]["path"] == str(lib / "gone.jpg")
    assert by_media["4"] == {
        "finding": "size_differs", "media_id": "4", "post_id": "103", "area": "Timeline",
        "path": str(lib / "edited.jpg"), "recorded_size": 3, "size": 6,
    }
    # a reset can't bring these back
    assert by_media["6"]["not_recoverable"] == "stories expire"
    assert by_media["9"]["not_recoverable"] == "the post was deleted"
    assert "not_recoverable" not in by_media["2"]
    assert creator["not_recoverable"] == 2
    assert report["totals"]["missing"] == 3 and report["applied"] is False


def test_the_preview_changes_nothing(library, tmp_path):
    before = snapshot(tmp_path)
    verify.verify([tmp_path / ".data"])
    assert snapshot(tmp_path) == before


def test_path_map_reads_paths_recorded_elsewhere(tmp_path):
    db, lib = make_library(tmp_path, [(1, 100, "ok.jpg", 3, b"abc", "Timeline", {})])
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE medias SET directory = '/data/lib'")
    assert verify.verify([db])["totals"]["missing"] == 1
    report = verify.verify([db], mappings=[("/data/lib", str(lib))])
    assert report["totals"] == {**report["totals"], "ok": 1, "missing": 0}


def test_relative_paths_need_path_root(tmp_path):
    db, lib = make_library(tmp_path, [(1, 100, "ok.jpg", 3, b"abc", "Timeline", {})])
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE medias SET directory = 'lib'")
    with pytest.raises(ValueError, match="--path-root"):
        verify.verify([db])
    assert verify.verify([db], path_root=tmp_path)["totals"]["ok"] == 1


def test_sample_limits_the_list_not_the_counts(tmp_path):
    db, _ = make_library(tmp_path, [(i, 100 + i, f"{i}.jpg", 3, None, "Timeline", {}) for i in range(1, 6)])
    (creator,) = verify.verify([db], sample=2)["creators"]
    assert creator["missing"] == 5 and len(creator["findings"]) == 2


def test_missing_creator_folder_is_listed(tmp_path):
    db, lib = make_library(tmp_path, [(1, 100, "gone.jpg", 3, None, "Timeline", {})])
    lib.rmdir()
    (creator,) = verify.verify([db])["creators"]
    assert creator["folders_missing"] == [str(lib)]


def test_command_line(library, capsys, tmp_path):
    db, _ = library
    assert verify.main([str(tmp_path / ".data")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["missing"] == 3
    with pytest.raises(SystemExit) as e:
        verify.main([])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        verify.main([str(tmp_path / "nothing-here")])
    assert e.value.code == 1


def test_routed_as_ofscraper_verify(library, capsys, tmp_path):
    import ofscraper.__main__ as entrypoint

    assert entrypoint.main(["verify", str(tmp_path / ".data")]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["empty"] == 1


def test_postgres_needs_creator(monkeypatch):
    monkeypatch.setenv("OFSC_DATABASE_URL", "postgresql://localhost/x")
    with pytest.raises(SystemExit) as e:
        verify.main(["--postgres"])
    assert e.value.code == 2
    monkeypatch.delenv("OFSC_DATABASE_URL")
    with pytest.raises(SystemExit) as e:
        verify.main(["--postgres", "--creator", "a creator"])
    assert e.value.code == 2

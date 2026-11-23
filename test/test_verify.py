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


# --- --apply ---

def downloaded(db):
    with closing(sqlite3.connect(db)) as conn:
        return dict(conn.execute("SELECT id, downloaded FROM medias ORDER BY id"))


def by_media_id(db):
    with closing(sqlite3.connect(db)) as conn:
        return {m: d for m, d in conn.execute("SELECT media_id, MIN(downloaded) FROM medias GROUP BY media_id")}


def test_apply_resets_missing_and_empty_and_moves_empty_aside(library):
    db, lib = library
    report = verify.verify([db], apply=True)
    (creator,) = report["creators"]
    # 2 missing and 3 empty; 6 (a story) and 9 (deleted post) can't come back
    assert creator["media_reset"] == 2 and creator["files_moved_aside"] == 1
    state = by_media_id(db)
    assert state[2] == 0 and state[3] == 0
    assert state[1] == 1 and state[4] == 1 and state[6] == 1 and state[9] == 1
    # no_file_recorded and skip_permanent rows are never changed
    assert state[5] == 1
    assert not (lib / "empty.jpg").exists() and (lib / "empty.jpg.bad").exists()
    assert (lib / "edited.jpg").exists()
    assert os.path.exists(creator["undo_file"])


def test_reset_size_differs_moves_the_file_aside(library):
    db, lib = library
    verify.verify([db], apply=True, reset_size_differs=True)
    assert by_media_id(db)[4] == 0
    assert (lib / "edited.jpg.bad").read_bytes() == b"abcdef"


def test_a_bad_name_that_is_taken_gets_a_number(library):
    db, lib = library
    (lib / "empty.jpg.bad").write_bytes(b"older")
    verify.verify([db], apply=True)
    assert (lib / "empty.jpg.bad").read_bytes() == b"older"
    assert (lib / "empty.jpg.2.bad").exists()


def test_a_media_in_several_posts_has_every_row_reset_and_restored(tmp_path):
    db, lib = make_library(tmp_path, [
        (1, 100, "gone.jpg", 3, None, "Timeline", {}),
        (1, 101, "gone.jpg", 3, None, "Archived", {}),
        (1, 102, "gone.jpg", 3, None, "Messages", {}),
        (2, 103, "ok.jpg", 3, b"abc", "Timeline", {}),
    ])
    before = downloaded(db)
    report = verify.verify([db], apply=True)
    assert set(downloaded(db).values()) == {0, 1}
    assert [d for d in downloaded(db).values()].count(0) == 3
    undo_file = report["creators"][0]["undo_file"]
    assert verify.undo(undo_file)["rows_to_restore"] == 3
    assert downloaded(db) != before  # the preview changed nothing
    assert verify.undo(undo_file, apply=True)["rows_to_restore"] == 3
    assert downloaded(db) == before


def test_undo_puts_files_back(library):
    db, lib = library
    before = downloaded(db)
    report = verify.verify([db], apply=True, reset_size_differs=True)
    result = verify.undo(report["creators"][0]["undo_file"], apply=True)
    assert result["files_to_rename_back"] == 2
    assert (lib / "empty.jpg").exists() and (lib / "edited.jpg").read_bytes() == b"abcdef"
    assert not list(lib.glob("*.bad"))
    assert downloaded(db) == before


def test_an_interrupted_apply_changes_nothing(library, tmp_path, monkeypatch):
    db, lib = library
    before = snapshot(tmp_path)
    real = sqlite3.Connection.execute

    class Stopping(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("UPDATE medias"):
                raise KeyboardInterrupt
            return real(self, sql, *args)

    real_connect = sqlite3.connect
    monkeypatch.setattr(verify.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Stopping, **k))
    with pytest.raises(KeyboardInterrupt):
        verify.verify([db], apply=True)
    monkeypatch.undo()
    assert snapshot(tmp_path) == before


def test_an_undo_file_left_by_a_stopped_apply_restores_the_start(library, tmp_path):
    """Stopped after the renames and the row changes were sent, before the
    commit was known: what the undo file names is put back."""
    db, lib = library
    before = downloaded(db)
    report = verify.verify([db], apply=True)
    undo_file = report["creators"][0]["undo_file"]
    # as if the commit had arrived but this run never heard: undo puts it back
    verify.undo(undo_file, apply=True)
    assert downloaded(db) == before and (lib / "empty.jpg").exists()
    # and running it again finds nothing more to do
    assert verify.undo(undo_file) == {"applied": False, "rows_to_restore": 0, "files_to_rename_back": 0}


def test_the_guard_leaves_a_creator_with_a_missing_folder_alone(tmp_path):
    db, lib = make_library(tmp_path, [(i, 100 + i, f"{i}.jpg", 3, b"abc", "Timeline", {}) for i in range(1, 4)])
    before = downloaded(db)
    for f in lib.iterdir():
        f.unlink()
    lib.rmdir()
    (creator,) = verify.verify([db], apply=True)["creators"]
    assert "isn't there" in creator["left_alone"] and creator["media_reset"] == 0
    assert downloaded(db) == before and creator["undo_file"] is None
    (creator,) = verify.verify([db], apply=True, allow_mostly_missing=True)["creators"]
    assert creator["media_reset"] == 3


def test_the_guard_leaves_a_creator_with_most_files_missing_alone(tmp_path):
    rows = [(i, 100 + i, f"{i}.jpg", 3, None if i <= 6 else b"abc", "Timeline", {}) for i in range(1, 11)]
    db, lib = make_library(tmp_path, rows)
    report = verify.verify([db], apply=True)
    (creator,) = report["creators"]
    assert "6 of 10 files are missing" in creator["left_alone"]
    assert report["totals"]["creators_left_alone"] == 1
    assert set(by_media_id(db).values()) == {1}
    assert verify.verify([db], apply=True, allow_mostly_missing=True)["totals"]["media_reset"] == 6


def test_the_preview_says_what_apply_would_do(library, tmp_path):
    db, _ = library
    before = snapshot(tmp_path)
    (creator,) = verify.verify([db])["creators"]
    assert creator["media_to_reset"] == 2 and creator["files_to_move_aside"] == 1
    assert snapshot(tmp_path) == before


def test_command_line_apply_and_undo(library, capsys, tmp_path):
    db, lib = library
    before = downloaded(db)
    assert verify.main([str(db), "--apply"]) == 0
    undo_file = json.loads(capsys.readouterr().out)["creators"][0]["undo_file"]
    assert verify.main(["--undo", undo_file, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["rows_to_restore"] == 2
    assert downloaded(db) == before
    for wrong in (["--undo", undo_file, str(db)], ["--undo", undo_file, "--reset-size-differs"]):
        with pytest.raises(SystemExit) as e:
            verify.main(wrong)
        assert e.value.code == 2

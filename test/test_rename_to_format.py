"""rename_to_format (#764) on a user_data.db. Every file it renames is a
small one made here, under pytest's tmp_path, and the database is a real
one made with the scraper's own tables."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import ofscraper.classes.placeholder as placeholder
import ofscraper.commands.rename_to_format as tool
import ofscraper.db.postgres as postgres
from ofscraper.classes.of.media import Media
from ofscraper.classes.of.posts import Post
from ofscraper.db.operations_.labels import labelsCreate
from ofscraper.db.operations_.media import mediaCreate
from ofscraper.db.operations_.messages import messagesCreate
from ofscraper.db.operations_.others import schemaCreate, siteInsert
from ofscraper.db.operations_.posts import postCreate
from ofscraper.db.operations_.profile import profilesCreate
from ofscraper.db.operations_.stories import storiesCreate

ROOT = Path(__file__).resolve().parents[1]
CREATOR = 10
NAME = "alice"
OLD_DIRS = "{model_username}/{responsetype}/{mediatype}/"
OLD_FILES = "{filename}.{ext}"


@pytest.fixture(autouse=True)
def no_url(monkeypatch):
    monkeypatch.delenv(postgres.URL_KEY, raising=False)


class Library:
    """A creator's user_data.db, the scraper's config.json and the folder
    their files are in."""

    def __init__(self, tmp_path, site=None, names=((NAME, "2025-06-11T10:20:30+00:00"),)):
        self.tmp = tmp_path
        self.root = tmp_path / "media"
        self.root.mkdir()
        self.config = tmp_path / "config" / "config.json"
        self.config.parent.mkdir()
        self.formats(OLD_DIRS, OLD_FILES)
        self.db = tmp_path / ".data" / str(CREATOR) / "user_data.db"
        self.db.parent.mkdir(parents=True)
        self.count = 0
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for create in (mediaCreate, postCreate, messagesCreate, storiesCreate, labelsCreate,
                           profilesCreate, schemaCreate):
                conn.execute(create)
            if site:
                conn.execute(siteInsert, (site,))
            conn.executemany(
                "INSERT INTO profiles (user_id, username, first_seen_at, last_seen_at) VALUES (?,?,?,?)",
                [(CREATOR, name, last, last) for name, last in names],
            )

    def formats(self, dir_format, file_format, **more):
        options = {"save_location": str(self.root), "dir_format": dir_format,
                   "file_format": file_format, "date": "YYYY-MM-DD", **more}
        self.config.write_text(json.dumps({"file_options": options}), encoding="utf-8")

    def post(self, post_id, table="posts", text="a caption", price=0, date="2024-05-06T07:08:09+00:00"):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                f"INSERT INTO {table} (post_id, text, price, archived, created_at, model_id) VALUES (?,?,?,?,?,?)",
                (post_id, text, price, 0, date, CREATOR),
            )

    def add(self, filename, folder="alice/Posts/Images", link=None, post_id=None, area="Timeline",
            kind="Images", posted_at="2024-05-06T07:08:09+00:00", content=None, on_disk=True,
            downloaded=1, media_id=None):
        """A media row that records `filename` in `folder` (under the media
        folder), and the file itself. Returns the file's path."""
        self.count += 1
        media_id = media_id or self.count
        path = self.root / folder / filename
        content = f"content of media {self.count}".encode() if content is None else content
        if on_disk:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        stem = Path(filename).stem
        link = link if link is not None else f"https://cdn.example/files/x/y/{stem}.{Path(filename).suffix[1:]}?Tag=1"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO medias (media_id, post_id, link, directory, filename, size, api_type, media_type,"
                " downloaded, created_at, posted_at, model_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (media_id, post_id or 500 + self.count, link, str(path.parent), filename, len(content), area, kind,
                 downloaded, posted_at, posted_at, CREATOR),
            )
        return path

    def rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT media_id, directory, filename FROM medias ORDER BY id").fetchall()

    def run(self, *args, apply=False):
        return tool.rename(self.db, apply=apply, config=self.config, **dict(args))

    def files(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())


def test_a_preview_lists_the_new_names_and_changes_nothing(tmp_path):
    library = Library(tmp_path)
    library.post(501)
    path = library.add("abcdef.jpg", post_id=501)
    library.formats(OLD_DIRS, "{date}_{filename}.{ext}")
    rows = library.rows()

    result = library.run()

    assert result["to_rename"] == 1 and not result["applied"]
    assert result["renames"] == [{"from": str(path), "to": str(path.with_name("2024-05-06_abcdef.jpg"))}]
    assert library.files() == ["alice/Posts/Images/abcdef.jpg"]
    assert library.rows() == rows
    assert list(library.db.parent.iterdir()) == [library.db]


def test_apply_renames_records_the_new_names_and_writes_the_undo_file_first(tmp_path, monkeypatch):
    library = Library(tmp_path)
    library.post(501)
    path = library.add("abcdef.jpg", post_id=501)
    library.formats("{model_username}/{date}/", "{post_id}_{filename}.{ext}")
    seen = []
    real = tool.rename_file

    def rename_file(old, new):
        # the undo file names the rename before it is made
        (undo_file,) = library.db.parent.glob("*.rename-undo-*.json")
        seen.append(json.loads(undo_file.read_text(encoding="utf-8"))["files"][0]["to"])
        real(old, new)

    monkeypatch.setattr(tool, "rename_file", rename_file)
    result = library.run(apply=True)

    new = library.root / "alice" / "2024-05-06" / "501_abcdef.jpg"
    assert seen == [str(new)]
    assert result["renamed"] == 1 and result["rows_changed"] == 1
    assert library.files() == ["alice/2024-05-06/501_abcdef.jpg"]
    assert new.read_bytes() == b"content of media 1"
    assert library.rows() == [(1, str(new.parent), new.name)]
    # the emptied old folders go; alice holds the new one
    assert result["folders_removed"] == [str(path.parent), str(path.parent.parent)]
    assert library.root.is_dir()
    assert Path(result["undo_file"]).is_file()

    # a second run finds nothing to do
    again = library.run(apply=True)
    assert again["unchanged"] == 1 and again["to_rename"] == 0 and again["undo_file"] is None


def test_undo_puts_back_the_names_and_the_rows(tmp_path):
    library = Library(tmp_path)
    library.post(501)
    library.add("abcdef.jpg", post_id=501)
    library.add("other.jpg", post_id=501)
    rows = library.rows()
    library.formats("{model_username}/All/", "{media_id}.{ext}")
    undo_file = library.run(apply=True)["undo_file"]
    assert library.files() == ["alice/All/1.jpg", "alice/All/2.jpg"]

    preview = tool.undo(undo_file)
    assert (preview["files_to_rename_back"], preview["rows_to_restore"]) == (2, 2)
    assert library.files() == ["alice/All/1.jpg", "alice/All/2.jpg"]

    done = tool.undo(undo_file, apply=True)
    assert (done["renamed_back"], done["rows_restored"]) == (2, 2)
    assert library.files() == ["alice/Posts/Images/abcdef.jpg", "alice/Posts/Images/other.jpg"]
    assert library.rows() == rows
    # a second undo changes nothing
    assert tool.undo(undo_file, apply=True)["rows_restored"] == 0
    assert library.rows() == rows


def test_names_match_what_the_download_code_builds(tmp_path):
    """The rows are written as a download writes them, from the scraper's
    own Post and Media, and the new names are those the same code gives
    under the new formats."""
    library = Library(tmp_path)
    api_post = {"id": 777, "postedAt": "2024-02-03T04:05:06+00:00", "price": 0, "text": "Hello <b>there</b> you",
                "responseType": "post", "media": [
                    {"id": 11, "type": "photo", "canView": True, "createdAt": "2024-02-01T00:00:00+00:00",
                     "files": {"full": {"url": "https://cdn.example/files/a/b/0abc123_photo.jpg?Tag=2"}}},
                    {"id": 12, "type": "video", "canView": True,
                     "source": {"source": "https://cdn.example/files/a/b/9xyz_source.mp4?Tag=3"}},
                ]}
    library.post(777, text=api_post["text"], date=api_post["postedAt"])

    def download_paths():
        post = Post(api_post, CREATOR, NAME, responsetype="timeline")
        out = []
        with tool.scraper_settings(config=library.config), tool._offline(placeholder):
            for media, ext in zip(post.media, ("jpg", "mp4")):
                names = asyncio.run(placeholder.Placeholders(media, ext).init(create=False))
                out.append((media, names.trunicated_filepath))
        return out

    for media, path in download_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
        with closing(sqlite3.connect(library.db)) as conn, conn:
            conn.execute(
                "INSERT INTO medias (media_id, post_id, link, directory, filename, size, api_type, media_type,"
                " downloaded, created_at, posted_at, model_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (media.id, media.post_id, media.link, str(path.parent), path.name, 4,
                 media.responsetype.capitalize(), media.mediatype.capitalize(), 1, media.date, media.postdate, CREATOR),
            )
    library.formats("{model_username}/{value}/{response_type}-{media_type}/{date}/",
                    "{text}_{media_id}_{only_filename}_{quality}_{filename}.{ext}", text_length=2)
    expected = sorted(str(path) for _media, path in download_paths())

    result = library.run(apply=True)

    assert result["left"] == [] and result["renamed"] == 2
    assert sorted(str(Path(d, f)) for _id, d, f in library.rows()) == expected
    assert sorted(str(library.root / f) for f in library.files()) == expected


def test_a_name_that_needs_a_value_the_database_lacks_is_left(tmp_path):
    library = Library(tmp_path)
    library.post(501)
    image = library.add("abcdef.jpg", post_id=501)
    # the old name doesn't show the video's quality
    video = library.add("clip.mp4", folder="alice/Posts/Videos", post_id=502, kind="Videos",
                        link="https://cdn.example/files/x/clip_source.mp4")
    story = library.add("story.jpg", folder="alice/Stories/Images", post_id=503, area="Stories")
    library.post(503, table="stories", text="title")
    library.formats(OLD_DIRS, "{text}_{quality}_{current_price}.{ext}")

    result = library.run(apply=True)

    left = {entry["file"]: entry["why"] for entry in result["left"]}
    assert left == {
        str(image): tool.NEEDS.format("{current_price}"),
        str(video): tool.NEEDS.format("{current_price}, {quality}, {text}"),
        str(story): tool.NEEDS.format("{current_price}, {text}"),
    }
    assert result["renamed"] == 0 and result["undo_file"] is None
    assert library.files() == ["alice/Posts/Images/abcdef.jpg", "alice/Posts/Videos/clip.mp4",
                               "alice/Stories/Images/story.jpg"]


def test_a_count_is_only_given_to_a_post_with_one_media(tmp_path):
    library = Library(tmp_path)
    library.post(501)
    single = library.add("a.jpg", post_id=501)
    first = library.add("b.jpg", post_id=600)
    library.add("c.jpg", post_id=600)
    library.formats(OLD_DIRS, "{post_id}.{ext}")

    result = library.run()

    assert result["renames"] == [{"from": str(single), "to": str(single.with_name("501.jpg"))}]
    assert {entry["file"] for entry in result["left"]} == {str(first), str(first.with_name("c.jpg"))}
    assert all(entry["why"] == tool.NEEDS.format("{count}") for entry in result["left"])


def test_an_existing_file_at_the_new_place_is_never_replaced(tmp_path):
    library = Library(tmp_path)
    path = library.add("abcdef.jpg")
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    stranger = path.with_name("1.jpg")
    stranger.write_bytes(b"not ours")

    result = library.run(apply=True)

    assert result["left"] == [{"file": str(path), "why": tool.TAKEN, "new_place": str(stranger)}]
    assert stranger.read_bytes() == b"not ours"
    assert path.read_bytes() == b"content of media 1"
    assert library.rows()[0][2] == "abcdef.jpg"


def test_a_place_taken_while_it_runs_is_not_replaced(tmp_path, monkeypatch):
    library = Library(tmp_path)
    path = library.add("abcdef.jpg")
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    real = tool.rename_file

    def someone_writes_first(old, new):
        Path(new).write_bytes(b"written meanwhile")
        real(old, new)

    monkeypatch.setattr(tool, "rename_file", someone_writes_first)
    result = library.run(apply=True)

    assert result["renamed"] == 0
    assert result["left"][0]["file"] == str(path) and "could not be renamed" in result["left"][0]["why"]
    assert path.with_name("1.jpg").read_bytes() == b"written meanwhile"
    assert path.read_bytes() == b"content of media 1"
    assert library.rows()[0][2] == "abcdef.jpg"


def test_files_that_would_get_one_name_are_all_left(tmp_path):
    library = Library(tmp_path)
    library.post(501)
    library.post(502)
    first = library.add("a.jpg", post_id=501)
    second = library.add("b.jpg", post_id=502)
    library.formats(OLD_DIRS, "{date}.{ext}")

    result = library.run(apply=True)

    assert sorted(entry["file"] for entry in result["left"]) == [str(first), str(second)]
    assert {entry["why"] for entry in result["left"]} == {tool.SAME_TARGET}
    assert library.files() == ["alice/Posts/Images/a.jpg", "alice/Posts/Images/b.jpg"]


def test_a_new_place_another_row_records_is_left(tmp_path):
    library = Library(tmp_path)
    moving = library.add("abcdef.jpg")
    # the row of a file that is gone records the place this one would get
    library.add("1.jpg", on_disk=False, downloaded=0, media_id=99)
    library.formats(OLD_DIRS, "{media_id}.{ext}")

    result = library.run(apply=True)

    assert {"file": str(moving), "why": tool.RECORDED, "new_place": str(moving.with_name("1.jpg"))} in result["left"]
    assert moving.is_file()


def test_rows_that_name_one_file_differently_leave_it(tmp_path):
    library = Library(tmp_path)
    path = library.add("shared.jpg", post_id=501, media_id=5)
    # the same media in another post records the same file
    library.add("shared.jpg", post_id=502, media_id=5, on_disk=False)
    library.formats(OLD_DIRS, "{post_id}.{ext}")

    result = library.run(apply=True)

    assert result["left"] == [{"file": str(path), "why": tool.DIFFER,
                               "new_names": [str(path.with_name("501.jpg")), str(path.with_name("502.jpg"))]}]
    assert path.is_file()


def test_rows_that_agree_on_one_file_are_all_changed(tmp_path):
    library = Library(tmp_path)
    path = library.add("shared.jpg", post_id=501, media_id=5)
    library.add("shared.jpg", post_id=502, media_id=5, on_disk=False)
    library.formats(OLD_DIRS, "{media_id}.{ext}")

    result = library.run(apply=True)

    assert result["renamed"] == 1 and result["rows_changed"] == 2
    assert [row[2] for row in library.rows()] == ["5.jpg", "5.jpg"]
    assert path.with_name("5.jpg").is_file()


def test_a_run_that_stopped_is_finished_by_running_it_again(tmp_path):
    library = Library(tmp_path)
    moved = library.add("abcdef.jpg")
    gone = library.add("gone.jpg", on_disk=False)
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    # renamed by a run that stopped before its rows were saved
    moved.rename(moved.with_name("1.jpg"))

    result = library.run(apply=True)

    assert result["already_there"] == 1 and result["rows_changed"] == 1
    assert result["left"] == [{"file": str(gone), "why": tool.NOT_FOUND}]
    assert library.rows()[0][2] == "1.jpg"


def test_a_file_at_the_new_place_with_another_size_is_left(tmp_path):
    library = Library(tmp_path)
    path = library.add("abcdef.jpg")
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    path.unlink()
    path.with_name("1.jpg").write_bytes(b"something else entirely")

    result = library.run(apply=True)

    assert result["left"][0]["why"] == tool.OTHER_SIZE
    assert library.rows()[0][2] == "abcdef.jpg"


@pytest.mark.skipif(not hasattr(os, "link") or os.name == "nt", reason="hard links where a rename is made with one")
def test_a_link_left_by_a_stopped_rename_is_finished(tmp_path):
    library = Library(tmp_path)
    path = library.add("abcdef.jpg")
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    os.link(path, path.with_name("1.jpg"))

    result = library.run(apply=True)

    assert result["renamed"] == 1 and result["left"] == []
    assert library.files() == ["alice/Posts/Images/1.jpg"]


def test_a_rename_that_fails_is_listed_and_the_rest_go_on(tmp_path, monkeypatch):
    library = Library(tmp_path)
    first = library.add("a.jpg")
    library.add("b.jpg")
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    real = tool.rename_file

    def fails_once(old, new):
        if old == str(first):
            raise PermissionError(13, "Permission denied")
        real(old, new)

    monkeypatch.setattr(tool, "rename_file", fails_once)
    result = library.run(apply=True)

    assert result["renamed"] == 1 and result["to_rename"] == 1
    assert result["left"] == [{"file": str(first), "why": "it could not be renamed (Permission denied)"}]
    assert [row[2] for row in library.rows()] == ["a.jpg", "2.jpg"]


def test_a_stop_part_way_puts_the_files_back_and_changes_no_row(tmp_path, monkeypatch):
    library = Library(tmp_path)
    library.add("a.jpg")
    library.add("b.jpg")
    rows = library.rows()
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    real = tool.rename_file
    calls = []

    def stopped(old, new):
        calls.append(old)
        if len(calls) == 2:
            raise KeyboardInterrupt
        real(old, new)

    monkeypatch.setattr(tool, "rename_file", stopped)
    with pytest.raises(KeyboardInterrupt):
        library.run(apply=True)

    monkeypatch.setattr(tool, "rename_file", real)
    assert library.files() == ["alice/Posts/Images/a.jpg", "alice/Posts/Images/b.jpg"]
    assert library.rows() == rows
    assert list(library.db.parent.glob("*.json")) == []


def test_folders_above_the_save_location_and_folders_with_other_files_stay(tmp_path):
    library = Library(tmp_path)
    path = library.add("abcdef.jpg")
    (path.parent / "notes.txt").write_text("mine", encoding="utf-8")
    library.formats("{model_username}/", "{media_id}.{ext}")

    result = library.run(apply=True)

    assert result["folders_removed"] == []
    assert sorted(library.files()) == ["alice/1.jpg", "alice/Posts/Images/notes.txt"]


def test_fansly_names_use_the_media_id_as_the_download_does(tmp_path):
    library = Library(tmp_path, site="fansly")
    library.root = library.root / "Fansly"
    library.formats(OLD_DIRS, OLD_FILES)
    library.post(501)
    path = library.add("123456789.jpg", post_id=501, media_id=123456789,
                       link="https://cdn.example/fansly/abc/other-name.jpg")
    library.formats("{model_username}/{site_name}/", "{date}_{filename}.{ext}")

    result = library.run()

    assert result["creator"]["site"] == "fansly"
    assert result["renames"] == [{"from": str(path),
                                  "to": str(library.root / "alice" / "Fansly" / "2024-05-06_123456789.jpg")}]


def test_the_username_is_the_name_seen_last_or_the_one_given(tmp_path):
    library = Library(tmp_path, names=(("old", "2023-01-01T00:00:00+00:00"), (NAME, "2025-01-01T00:00:00+00:00")))
    path = library.add("abcdef.jpg", folder="old/Posts/Images")

    assert library.run()["renames"] == [{"from": str(path), "to": str(library.root / "alice/Posts/Images/abcdef.jpg")}]
    assert library.run(("username", "old"))["unchanged"] == 1
    with pytest.raises(ValueError, match="--username: someone"):
        library.run(("username", "someone"))


def test_a_naming_script_is_refused(tmp_path):
    library = Library(tmp_path)
    library.add("abcdef.jpg")
    library.config.write_text(json.dumps({"file_options": {"save_location": str(library.root)},
                                          "advanced_options": {"naming_script": str(tmp_path / "name.py")}}))
    with pytest.raises(ValueError, match="naming_script"):
        library.run(apply=True)
    assert library.files() == ["alice/Posts/Images/abcdef.jpg"]


def test_a_missing_config_is_refused_and_none_is_made(tmp_path):
    library = Library(tmp_path)
    library.add("abcdef.jpg")
    missing = tmp_path / "nowhere" / "config.json"
    with pytest.raises(ValueError, match="no config.json"):
        tool.rename(library.db, config=missing)
    assert not missing.parent.exists()


def test_settings_are_put_back_after_a_run(tmp_path):
    import ofscraper.utils.args.globals as global_args
    import ofscraper.utils.settings as settings

    library = Library(tmp_path)
    library.add("abcdef.jpg")
    before = global_args.args, settings.settings
    library.run()
    assert (global_args.args, settings.settings) == before


def test_the_command_line_previews_applies_and_undoes(tmp_path, capsys):
    library = Library(tmp_path)
    library.add("abcdef.jpg")
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    common = [str(library.db), "--config", str(library.config)]

    assert tool.main(common) == 0
    assert json.loads(capsys.readouterr().out)["to_rename"] == 1
    assert tool.main([*common, "--apply"]) == 0
    undo_file = json.loads(capsys.readouterr().out)["undo_file"]
    assert library.files() == ["alice/Posts/Images/1.jpg"]
    assert tool.main(["--undo", undo_file, "--apply"]) == 0
    assert library.files() == ["alice/Posts/Images/abcdef.jpg"]
    with pytest.raises(SystemExit):
        tool.main(["--undo", undo_file, str(library.db)])


def test_it_runs_as_an_ofscraper_tool(tmp_path):
    library = Library(tmp_path)
    library.add("abcdef.jpg")
    library.formats(OLD_DIRS, "{media_id}.{ext}")
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONPATH": str(ROOT)}
    env.pop(postgres.URL_KEY, None)
    proc = subprocess.run(
        [sys.executable, "-m", "ofscraper", "rename_to_format", str(library.db), "--config", str(library.config)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["to_rename"] == 1
    assert list(home.iterdir()) == []


def test_text_and_labels_come_from_the_database_where_it_holds_them(tmp_path):
    library = Library(tmp_path)
    library.post(501, text="from the post")
    library.post(502, table="messages", text="from the message")
    post = library.add("a.jpg", post_id=501)
    message = library.add("b.jpg", folder="alice/Messages/Images", post_id=502, area="Messages")
    labelled = library.add("c.jpg", post_id=503)
    with closing(sqlite3.connect(library.db)) as conn, conn:
        conn.execute("INSERT INTO labels (label_id, name, type, post_id, model_id) VALUES (1,'Best','custom',503,?)",
                     (CREATOR,))
    library.post(503)
    library.formats("{model_username}/{label}/", "{text}.{ext}")

    result = library.run()

    assert sorted((r["from"], r["to"]) for r in result["renames"]) == sorted([
        (str(post), str(library.root / "alice/None/from the post.jpg")),
        (str(message), str(library.root / "alice/None/from the message.jpg")),
    ])
    assert result["left"] == [{"file": str(labelled), "why": tool.NEEDS.format("{label}")}]


def test_text_saved_sanitized_is_not_used(tmp_path):
    library = Library(tmp_path)
    library.post(501, text="caption")
    path = library.add("a.jpg", post_id=501)
    library.formats(OLD_DIRS, "{text}.{ext}")
    config = json.loads(library.config.read_text(encoding="utf-8"))
    config["advanced_options"] = {"sanitize_text": True}
    library.config.write_text(json.dumps(config), encoding="utf-8")

    result = library.run()

    assert result["left"] == [{"file": str(path), "why": tool.NEEDS.format("{text}")}]

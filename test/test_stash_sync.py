import itertools
import sqlite3
from contextlib import closing

import pytest

from ofscraper.commands import stash_sync
from ofscraper.commands.stash_sync import StashError, map_path, read_model, sync
from ofscraper.db.operations_.media import mediaCreate
from ofscraper.db.operations_.messages import messagesCreate
from ofscraper.db.operations_.posts import postCreate
from ofscraper.db.operations_.profile import profilesCreate


class FakeStash:
    """Just enough of Stash's GraphQL API for stash_sync."""

    def __init__(self, scenes=(), images=()):
        ids = itertools.count(100)
        self.next_id = lambda: str(next(ids))
        self.items = {"scene": {}, "image": {}}
        for kind, paths in (("scene", scenes), ("image", images)):
            for path in paths:
                self.add(kind, path)
        self.entities = {"studio": {}, "tag": {}, "performer": {}}
        self.created = []
        self.updates = []

    def add(self, kind, path, **fields):
        item = {"id": self.next_id(), "title": "", "details": "", "date": None, "code": "", "urls": [],
                "organized": False, "studio": None, "performers": [], "tags": [], "path": path, **fields}
        self.items[kind][item["id"]] = item
        return item

    def call(self, query, variables=None):
        variables = variables or {}
        for kind, find, key in (("scene", "findScenes", "scenes"), ("image", "findImages", "images")):
            if find in query:
                found = [dict(i, files=[{"path": i["path"]}], visual_files=[{"path": i["path"]}])
                         for i in self.items[kind].values() if i["path"] == variables["path"]]
                return {find: {key: found}}
            if f"{kind}Update" in query:
                change = dict(variables["input"])
                self.updates.append((kind, change))
                item = self.items[kind][change.pop("id")]
                if "studio_id" in change:
                    item["studio"] = {"id": change.pop("studio_id")}
                if "performer_ids" in change:
                    item["performers"] = [{"id": i} for i in change.pop("performer_ids")]
                if "tag_ids" in change:
                    item["tags"] = [{"id": i} for i in change.pop("tag_ids")]
                item.update(change)
                return {f"{kind}Update": {"id": item["id"]}}
        for entity, plural in (("studio", "Studios"), ("tag", "Tags"), ("performer", "Performers")):
            if f"find{plural}" in query:
                found = [{"id": i, "name": n} for n, i in self.entities[entity].items() if n == variables["name"]]
                return {f"find{plural}": {plural.lower(): found}}
            if f"{entity}Create" in query:
                new = self.next_id()
                self.entities[entity][variables["input"]["name"]] = new
                self.created.append((entity, variables["input"]))
                return {f"{entity}Create": {"id": new}}
        raise AssertionError(f"unexpected query: {query}")


@pytest.fixture
def library(tmp_path):
    db = tmp_path / ".data" / "123" / "user_data.db"
    db.parent.mkdir(parents=True)
    media_dir = tmp_path / "creator"
    with closing(sqlite3.connect(db)) as conn, conn:
        for create in (mediaCreate, postCreate, messagesCreate, profilesCreate):
            conn.execute(create)
        conn.execute("INSERT INTO profiles (user_id, username) VALUES (123, 'creator')")
        conn.execute("INSERT INTO posts (post_id, text, model_id) VALUES (1001, 'First line &amp; more<br />second line', 123)")
        conn.execute("INSERT INTO messages (post_id, text, model_id) VALUES (2002, '<p>hi there</p>', 123)")
        rows = [
            (1, 1001, "post.mp4", "Timeline", "Videos", 1),
            (2, 1001, "post.jpg", "Timeline", "Images", 1),
            (3, 2002, "msg.jpg", "Messages", "Images", 1),
            (3, 2003, "msg.jpg", "Paid", "Images", 1),  # same file, recorded again as a purchase
            (4, "0123abcdef", "avatar.jpg", "Profile", "Images", 1),
            (5, 1001, "audio.mp3", "Timeline", "Audios", 1),
            (6, 1001, "skipped.jpg", "Timeline", "Images", 0),
        ]
        for media_id, post_id, name, api_type, media_type, downloaded in rows:
            conn.execute(
                "INSERT INTO medias (media_id, post_id, directory, filename, api_type, media_type, downloaded, "
                "posted_at, created_at, model_id) VALUES (?,?,?,?,?,?,?,?,?,123)",
                (media_id, post_id, str(media_dir), name, api_type, media_type, downloaded,
                 "2025-02-27T23:12:05+00:00", "2025-02-27T23:12:05+00:00"),
            )
    return db, media_dir


def test_read_model_describes_each_file(library):
    db, media_dir = library
    username, files = read_model(db)
    assert username == "creator"
    by_name = {f["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]: f for f in files}
    assert set(by_name) == {"post.mp4", "post.jpg", "msg.jpg", "avatar.jpg", "audio.mp3"}

    post = by_name["post.mp4"]["meta"]
    assert post == {
        "code": "1001",
        "details": "First line & more\nsecond line",
        "title": "First line & more",
        "date": "2025-02-27",
        "url": "https://onlyfans.com/1001/creator",
    }
    msg = by_name["msg.jpg"]
    assert msg["api_types"] == {"Messages", "Paid"}
    assert msg["meta"]["details"] == "hi there"
    assert "url" not in msg["meta"]  # messages have no public page
    assert by_name["avatar.jpg"]["meta"]["code"] is None


def test_preview_changes_nothing_and_apply_fills_everything(library):
    db, media_dir = library
    stash = FakeStash(scenes=[str(media_dir / "post.mp4")], images=[str(media_dir / "post.jpg"), str(media_dir / "msg.jpg")])

    preview = sync([db], stash)
    assert stash.updates == [] and stash.created == []
    assert preview["totals"] == {"files": 5, "to_update": 3, "not_in_stash": 1, "unsupported_type": 1}

    report = sync([db], stash, apply=True)
    assert report["totals"]["updated"] == 3
    scene = next(iter(stash.items["scene"].values()))
    performer = stash.entities["performer"]["creator"]
    assert scene["title"] == "First line & more"
    assert scene["code"] == "1001" and scene["date"] == "2025-02-27"
    assert scene["urls"] == ["https://onlyfans.com/1001/creator"]
    assert scene["studio"] == {"id": stash.entities["studio"]["OnlyFans"]}
    assert scene["performers"] == [{"id": performer}]
    assert scene["tags"] == [{"id": stash.entities["tag"]["OnlyFans Timeline"]}]
    msg = next(i for i in stash.items["image"].values() if i["path"].endswith("msg.jpg"))
    assert {t["id"] for t in msg["tags"]} == {stash.entities["tag"]["OnlyFans Messages"], stash.entities["tag"]["OnlyFans Paid"]}
    assert ("performer", {"name": "creator", "urls": ["https://onlyfans.com/creator"]}) in stash.created

    again = sync([db], stash, apply=True)
    assert again["totals"]["up_to_date"] == 3 and "updated" not in again["totals"]


def test_existing_stash_values_are_kept(library):
    db, media_dir = library
    stash = FakeStash()
    stash.entities["performer"]["someone else"] = "7"
    stash.entities["tag"]["mine"] = "8"
    item = stash.add("scene", str(media_dir / "post.mp4"), title="My title", date="2020-01-01",
                     urls=["https://example.com"], performers=[{"id": "7"}], tags=[{"id": "8"}])

    sync([db], stash, apply=True)
    assert item["title"] == "My title" and item["date"] == "2020-01-01"
    assert item["details"] == "First line & more\nsecond line"  # was empty, so filled
    assert item["urls"] == ["https://example.com", "https://onlyfans.com/1001/creator"]
    assert [p["id"] for p in item["performers"]] == ["7", stash.entities["performer"]["creator"]]
    assert [t["id"] for t in item["tags"]][0] == "8"


def test_organized_only_when_asked(library):
    db, media_dir = library
    stash = FakeStash(scenes=[str(media_dir / "post.mp4")])
    sync([db], stash, apply=True)
    item = next(iter(stash.items["scene"].values()))
    assert not item["organized"]
    sync([db], stash, apply=True, organized=True)
    assert item["organized"] is True


def test_nothing_matched_creates_nothing_and_hints(library):
    # e.g. Stash in Docker sees the files under /data and --path-map was forgotten
    db, _ = library
    stash = FakeStash(scenes=["/data/creator/post.mp4"])
    report = sync([db], stash, apply=True)
    assert stash.created == [] and stash.updates == []
    model = report["models"][0]
    assert model["not_in_stash"] == 4 and model["performer_exists"] is False
    assert "--path-map" in model["hint"]


def test_path_map_finds_files_stash_sees_elsewhere(library):
    db, media_dir = library
    stash = FakeStash(scenes=["/data/creator/post.mp4"])
    report = sync([db], stash, apply=True, mappings=[(str(media_dir.parent), "/data")])
    assert report["totals"]["updated"] == 1


def test_map_path():
    assert map_path(r"C:\dl\a\b.jpg", [(r"C:\dl", "/data")]) == "/data/a/b.jpg"
    assert map_path(r"C:\dl2\b.jpg", [(r"C:\dl", "/data")]) == r"C:\dl2\b.jpg"
    assert map_path("/srv/dl/b.jpg", [("/srv/dl", "/media")]) == "/media/b.jpg"


def test_no_tags(library):
    db, media_dir = library
    stash = FakeStash(scenes=[str(media_dir / "post.mp4")])
    sync([db], stash, apply=True, tags=False)
    assert stash.entities["tag"] == {}


def test_unreachable_stash_exits_with_error(library, capsys):
    db, _ = library
    assert stash_sync.main([str(db), "--url", "http://127.0.0.1:9/graphql"]) == 1
    assert "can't reach Stash" in capsys.readouterr().err


def test_locked_database_is_retried(monkeypatch):
    replies = iter([
        b'{"errors": [{"message": "beginning transaction: database is locked"}], "data": null}',
        b'{"data": {"ok": true}}',
    ])

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    monkeypatch.setattr(stash_sync.urllib.request, "urlopen", lambda *a, **k: Response(next(replies)))
    monkeypatch.setattr(stash_sync.time, "sleep", lambda s: None)
    assert stash_sync.StashClient().call("{ ok }") == {"ok": True}


def test_graphql_errors_are_raised(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"errors": [{"message": "bad field"}]}'

    monkeypatch.setattr(stash_sync.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(StashError, match="bad field"):
        stash_sync.StashClient().call("{ x }")

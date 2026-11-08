import asyncio
import http.server
import itertools
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest
from test_fansly_api import DM_CREATOR, DM_CREATOR_ID, FakeSession, both_routes, fansly  # noqa: F401

import ofscraper.classes.placeholder as placeholder
import ofscraper.data.posts.post as post
import ofscraper.db.operations as operations
from ofscraper.commands import stash_sync
from ofscraper.commands.stash_sync import StashError, map_path, read_model, sync
from ofscraper.db.operations_.media import batch_mediainsert, mark_media_as_downloaded, mediaCreate
from ofscraper.db.operations_.messages import messagesCreate
from ofscraper.db.operations_.others import schemaCreate, siteInsert
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
        self.galleries = {}
        self.created = []
        self.updates = []

    def add(self, kind, path, **fields):
        item = {"id": self.next_id(), "title": "", "details": "", "date": None, "code": "", "urls": [],
                "organized": False, "studio": None, "performers": [], "tags": [], "galleries": [],
                "path": path, **fields}
        self.items[kind][item["id"]] = item
        return item

    @staticmethod
    def _apply(item, change):
        for key, field in (("studio_id", None), ("performer_ids", "performers"), ("tag_ids", "tags"),
                           ("gallery_ids", "galleries")):
            if key in change:
                value = change.pop(key)
                if field is None:
                    item["studio"] = {"id": value}
                else:
                    item[field] = [{"id": i} for i in value]
        item.update(change)

    def call(self, query, variables=None):
        variables = variables or {}
        if "findGalleries" in query:
            found = [dict(g) for g in self.galleries.values() if g["code"] == variables["code"]]
            return {"findGalleries": {"galleries": found}}
        if "galleryCreate" in query:
            gallery = {"id": self.next_id(), "title": "", "details": "", "date": None, "code": "", "urls": [],
                       "organized": False, "studio": None, "performers": [], "tags": []}
            change = dict(variables["input"])
            self.created.append(("gallery", dict(change)))
            self._apply(gallery, change)
            self.galleries[gallery["id"]] = gallery
            return {"galleryCreate": {"id": gallery["id"]}}
        if "galleryUpdate" in query:
            change = dict(variables["input"])
            self.updates.append(("gallery", dict(change)))
            self._apply(self.galleries[change.pop("id")], change)
            return {"galleryUpdate": {"id": variables["input"]["id"]}}
        for kind, find, key in (("scene", "findScenes", "scenes"), ("image", "findImages", "images")):
            if find in query:
                found = [dict(i, files=[{"path": i["path"]}], visual_files=[{"path": i["path"]}])
                         for i in self.items[kind].values() if i["path"] == variables["path"]]
                return {find: {key: found}}
            if f"{kind}Update" in query:
                change = dict(variables["input"])
                self.updates.append((kind, change))
                item = self.items[kind][change.pop("id")]
                self._apply(item, dict(change))
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


def test_map_path_accepts_either_separator(monkeypatch):
    # As on Linux CI: a Windows path from the database still maps
    monkeypatch.setattr(stash_sync.os, "sep", "/")
    assert map_path(r"C:\dl\a\b.jpg", [(r"C:\dl", "/data")]) == "/data/a/b.jpg"
    assert map_path("C:\\dl/a/b.jpg", [(r"C:\dl", "/data")]) == "/data/a/b.jpg"
    assert map_path(r"C:\dl", [("C:\\dl\\", "/data")]) == "/data"
    assert map_path("/srv/dl", [("/srv/dl", "/media")]) == "/media"
    assert map_path("/srv/dlx/b.jpg", [("/srv/dl", "/media")]) == "/srv/dlx/b.jpg"


@pytest.mark.parametrize("sep", ["\\", "/"])
def test_map_path_windows_prefix_ignores_case_and_slashes(monkeypatch, sep):
    monkeypatch.setattr(stash_sync.os, "sep", sep)
    stored = r"C:\dl\Sub\b.jpg"
    for local in (r"c:\dl", "C:/dl", "c:/DL/", r"C:\dl"):
        assert map_path(stored, [(local, "/data")]) == "/data/Sub/b.jpg", local
    assert map_path("C:/dl/Sub/b.jpg", [(r"C:\DL", "/data")]) == "/data/Sub/b.jpg"
    assert map_path(r"C:\dl", [("c:/dl", "/data")]) == "/data"
    assert map_path(r"\\NAS\Share\dl\a.jpg", [(r"\\nas\share", "/data")]) == "/data/dl/a.jpg"
    assert map_path(r"\\nas\share\dl\a.jpg", [("//nas/share/", "/data")]) == "/data/dl/a.jpg"
    # partial folder names still don't match
    assert map_path(r"C:\dl2\x.jpg", [("c:/dl", "/data")]) == r"C:\dl2\x.jpg"
    assert map_path(r"\\nas\share2\x.jpg", [(r"\\NAS\share", "/data")]) == r"\\nas\share2\x.jpg"


@pytest.mark.parametrize("sep", ["\\", "/"])
def test_map_path_posix_prefix_unchanged(monkeypatch, sep):
    monkeypatch.setattr(stash_sync.os, "sep", sep)
    assert map_path("/srv/DL/b.jpg", [("/srv/dl", "/media")]) == "/srv/DL/b.jpg"
    assert map_path("/srv/dlx/b.jpg", [("/srv/dl", "/media")]) == "/srv/dlx/b.jpg"
    assert map_path("/srv/dl/b.jpg", [("/srv/dl/", "/media")]) == "/media/b.jpg"
    assert map_path("/srv/dl/b.jpg", [("/", "/media")]) == "/media/srv/dl/b.jpg"


# --- Fansly -----------------------------------------------------------------

FRIEND = "samplefriend"
MENTION_POST = 100000000000000004


def mention_routes(path, query):
    """The Fansly fixtures, with one post mentioning another creator."""
    data = both_routes(path, query)
    response = data.get("response")
    for item in response.get("posts") or [] if isinstance(response, dict) else []:
        if int(item["id"]) == MENTION_POST:
            item["content"] += f" with @{FRIEND}"
    return data


@pytest.fixture
def fansly_library(fansly, monkeypatch, tmp_path):
    """A Fansly creator's database written by the real Fansly scrape and download code."""
    db = tmp_path / ".data" / "Fansly" / str(DM_CREATOR_ID) / "user_data.db"
    db.parent.mkdir(parents=True)
    monkeypatch.setattr(placeholder.databasePlaceholder, "databasePathHelper", lambda self, mid, name: db)
    monkeypatch.setattr(operations, "create_backup", lambda *a, **k: None)
    media_dir = tmp_path / "fansly"

    async def scrape():
        c = FakeSession(mention_routes)
        await operations.table_init_create(model_id=DM_CREATOR_ID, username=DM_CREATOR)
        timeline, _ = await post.process_timeline_posts(DM_CREATOR_ID, DM_CREATOR, c)
        messages, _ = await post.process_messages(DM_CREATOR_ID, DM_CREATOR, c)
        media = [m for p in timeline + messages for m in p.all_media]
        await batch_mediainsert(media, model_id=DM_CREATOR_ID, username=DM_CREATOR, downloaded=False)
        for m in media:
            ext = "mp4" if m.mediatype.lower() == "videos" else "jpg"
            await mark_media_as_downloaded(
                m, model_id=DM_CREATOR_ID, username=DM_CREATOR, filepath=media_dir / f"{m.id}.{ext}", downloaded=True
            )

    asyncio.run(scrape())
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT DISTINCT directory, filename, media_type FROM medias WHERE downloaded = 1").fetchall()
    files = [(os.path.join(d, f), stash_sync.KINDS[t]) for d, f, t in rows]
    return db, media_dir, files


@pytest.fixture
def stash_server():
    """FakeStash behind a local HTTP GraphQL endpoint."""
    stash = FakeStash()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.dumps({"data": stash.call(body["query"], body["variables"])}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stash, f"http://127.0.0.1:{server.server_port}/graphql"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def named(stash, item):
    """An item or gallery with its studio, performers, tags and galleries by name."""
    names = {i: n for found in stash.entities.values() for n, i in found.items()}
    return {
        "studio": names.get((item["studio"] or {}).get("id")),
        "performers": sorted(names[p["id"]] for p in item["performers"]),
        "tags": sorted(names[t["id"]] for t in item["tags"]),
        "galleries": sorted(stash.galleries[g["id"]]["code"] for g in item.get("galleries", [])),
        **{k: item[k] for k in ("title", "details", "date", "code", "urls", "organized")},
    }


def by_file(stash, name):
    return next(i for kind in stash.items.values() for i in kind.values() if Path(i["path"]).name == name)


def test_fansly_and_onlyfans_end_to_end(library, fansly_library, stash_server, tmp_path, capsys):
    of_db, of_dir = library
    _, _, fansly_files = fansly_library
    stash, url = stash_server
    for path, kind in fansly_files:
        stash.add(kind, path)
    of_files = (("post.mp4", "scene"), ("post.jpg", "image"), ("msg.jpg", "image"))
    for name, kind in of_files:
        stash.add(kind, str(of_dir / name))
    data = str(tmp_path / ".data")

    # preview creates nothing
    assert stash_sync.main([data, "--url", url]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert stash.created == [] and stash.updates == []
    assert preview["totals"]["galleries_to_create"] == 5

    assert stash_sync.main([data, "--url", url, "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["updated"] == len(fansly_files) + 3
    assert report["totals"]["galleries_created"] == 5  # 3 posts, 2 messages
    assert report["totals"]["not_in_stash"] == 1  # the OnlyFans avatar, not added to Stash here

    created = [c for c in stash.created if c[0] in ("studio", "performer")]
    assert ("studio", {"name": "Fansly", "urls": ["https://fansly.com"]}) in created
    assert ("studio", {"name": "OnlyFans", "urls": ["https://onlyfans.com"]}) in created
    assert ("performer", {"name": DM_CREATOR, "urls": [f"https://fansly.com/{DM_CREATOR}"]}) in created
    assert ("performer", {"name": FRIEND, "urls": [f"https://fansly.com/{FRIEND}"]}) in created

    post_url = f"https://fansly.com/post/{MENTION_POST}"
    text = f"Sample text 2 #weekend #behindthescenes with @{FRIEND}"
    assert named(stash, by_file(stash, "100000000000000026.mp4")) == {
        "studio": "Fansly",
        "performers": sorted([DM_CREATOR, FRIEND]),
        "tags": ["Fansly Timeline", "behindthescenes", "weekend"],
        "galleries": [str(MENTION_POST)],
        "title": text,
        "details": text,
        "date": "2026-11-06",
        "code": "100000000000000026",
        "urls": [post_url],
        "organized": False,
    }
    # in two posts: the earlier one's details, and both galleries
    shared = named(stash, by_file(stash, "100000000000000019.mp4"))
    assert shared["urls"] == ["https://fansly.com/post/100000000000000001"]
    assert shared["code"] == "100000000000000019"
    assert shared["performers"] == [DM_CREATOR]
    assert shared["galleries"] == ["100000000000000001", str(MENTION_POST)]
    assert shared["tags"] == ["Fansly Timeline", "outfit", "studio"]
    assert "Fansly Preview" in named(stash, by_file(stash, "100000000000000051.jpg"))["tags"]
    message = named(stash, by_file(stash, "100000000000000229.jpg"))
    assert message["urls"] == []  # a message's page needs its conversation id
    assert message["code"] == "100000000000000229"
    assert message["tags"] == ["Fansly Messages", "behindthescenes", "studio"]
    assert message["galleries"] == ["100000000000000142"]

    galleries = {g["code"]: named(stash, g) for g in stash.galleries.values()}
    assert galleries[str(MENTION_POST)] == {
        "studio": "Fansly",
        "performers": sorted([DM_CREATOR, FRIEND]),
        "tags": ["Fansly Timeline", "behindthescenes", "weekend"],
        "galleries": [],
        "title": text,
        "details": text,
        "date": "2026-11-06",
        "code": str(MENTION_POST),
        "urls": [post_url],
        "organized": False,
    }
    assert galleries["100000000000000142"]["urls"] == []
    assert galleries["100000000000000142"]["tags"] == ["Fansly Messages", "behindthescenes", "studio"]

    # OnlyFans items come out exactly as from a run over the OnlyFans database alone
    alone = FakeStash()
    for name, kind in of_files:
        alone.add(kind, str(of_dir / name))
    sync([of_db], alone, apply=True)
    for name, _ in of_files:
        assert named(stash, by_file(stash, name)) == named(alone, by_file(alone, name))

    # a second run changes nothing
    created, updates = len(stash.created), len(stash.updates)
    assert stash_sync.main([data, "--url", url, "--apply"]) == 0
    again = json.loads(capsys.readouterr().out)["totals"]
    assert again["up_to_date"] == len(fansly_files) + 3
    assert again["galleries_up_to_date"] == 5
    assert "updated" not in again and "galleries_created" not in again
    assert (len(stash.created), len(stash.updates)) == (created, updates)


def test_fansly_existing_values_are_kept(fansly_library, tmp_path):
    _, _, fansly_files = fansly_library
    stash = FakeStash()
    for path, kind in fansly_files:
        stash.add(kind, path)
    item = by_file(stash, "100000000000000026.mp4")
    item.update(title="mine", code="custom")
    sync([tmp_path / ".data"], stash, apply=True)
    assert (item["title"], item["code"]) == ("mine", "custom")
    # the empty ones are still filled
    assert named(stash, item)["studio"] == "Fansly"
    assert item["urls"] == [f"https://fansly.com/post/{MENTION_POST}"]


def test_site_flag_is_read_in_any_case(tmp_path):
    db = tmp_path / "user_data.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(schemaCreate)
        conn.execute(siteInsert, ("Fansly",))
    assert stash_sync.site_of(db) == "fansly"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DELETE FROM schema_flags")
    assert stash_sync.site_of(db) == "onlyfans"


def test_mentions():
    text = "hi @friend_1 and @Friend_1, mail me@example.com, see @other. and @creator"
    assert stash_sync.mentions(text, "Creator") == ["friend_1", "other"]


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

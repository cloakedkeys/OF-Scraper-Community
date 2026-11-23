"""--dry-run: the scan runs on throwaway copies and nothing is downloaded or
changed (utils/dry_run.py)."""

import asyncio
import contextlib
import os
import sqlite3
from types import SimpleNamespace

import pytest
from click.exceptions import UsageError

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.classes.placeholder as placeholder
import ofscraper.commands.scraper.actions.download.download as download
import ofscraper.db.operations_.media as media_ops
import ofscraper.scripts.after_download_action_script as after_download
import ofscraper.scripts.after_like_action_script as after_like
import ofscraper.scripts.final_script as final_script
import ofscraper.sites.fansly.pipeline as pipeline
import ofscraper.utils.cache.cache as cache
import ofscraper.utils.dry_run as dry_run
import ofscraper.utils.paths.common as common_paths
import ofscraper.utils.paths.paths as paths
import ofscraper.utils.settings as settings
from ofscraper.utils.args.parse.group_bundles.main import check_dry_run


def snapshot(root):
    """Every file under root with its bytes and modified time."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            out[str(path.relative_to(root))] = (path.read_bytes(), st.st_mtime_ns)
        else:
            out[str(path.relative_to(root))] = None
    return out


def make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute(media_ops.mediaCreate)
        conn.execute(
            "INSERT INTO medias (media_id, post_id, api_type, media_type,"
            " created_at, posted_at, model_id) VALUES (1, 100, 'timeline',"
            " 'Images', '2024-01-01', '2024-01-02', 5)"
        )
        conn.commit()


def media_ids(path):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return sorted(r[0] for r in conn.execute("SELECT media_id FROM medias"))


@pytest.fixture
def library(monkeypatch, tmp_path):
    """A profile under tmp_path with one creator's database and a cache,
    using the real path helpers."""
    monkeypatch.setattr(placeholder.log, "trace", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(placeholder.me, "parse_user", lambda: ("1", "me"))
    monkeypatch.setattr(placeholder.me, "get_id", lambda: 1)
    monkeypatch.setattr(placeholder.profile_data, "get_active_profile", lambda: "main_profile")
    monkeypatch.setattr(placeholder.common_paths, "get_config_home", lambda: tmp_path / "cfg")
    monkeypatch.setattr(
        placeholder.common_paths, "get_save_location", lambda *a, **k: str(tmp_path / "data")
    )
    monkeypatch.setattr(
        placeholder.data, "get_metadata", lambda *a, **k: "{configpath}/{profile}/.data/{model_username}"
    )
    profile = tmp_path / "cfg" / "main_profile"
    monkeypatch.setattr(common_paths, "get_profile_path", lambda *a, **k: profile)
    s = settings.get_settings()
    monkeypatch.setattr(s, "cached_disabled", False, raising=False)
    monkeypatch.setattr(s, "dry_run", False, raising=False)
    monkeypatch.setattr(cache, "cache", None)
    make_db(profile / ".data" / "a creator" / "user_data.db")
    cache.set("scan_mark", "real", auto_close=True)
    cache.cache.close()
    cache.cache = None
    yield SimpleNamespace(root=tmp_path, profile=profile, settings=s)
    dry_run.cleanup()
    if cache.cache is not None:
        cache.cache.close()
        cache.cache = None


def test_dry_run_changes_nothing(library, monkeypatch):
    # Given a library with a creator's database and a cache
    before = snapshot(library.root)
    monkeypatch.setattr(library.settings, "dry_run", True)

    # When a dry run's scan writes to the database and the cache, as a scan does
    real = library.profile / ".data" / "a creator" / "user_data.db"
    copy = placeholder.databasePlaceholder().databasePathHelper(5, "a creator")
    with contextlib.closing(sqlite3.connect(copy)) as conn:
        conn.execute(
            "INSERT INTO medias (media_id, post_id, api_type, media_type,"
            " created_at, posted_at, model_id) VALUES (2, 101, 'timeline',"
            " 'Images', '2024-01-01', '2024-01-02', 5)"
        )
        conn.commit()
    asyncio.run(media_ops.create_media_table(model_id=6, username="new creator"))
    assert cache.get("scan_mark") == "real"  # the copy starts from the real cache
    cache.set("scan_mark", "dry run")

    # Then the scan saw a copy of the real rows and wrote to it
    assert copy != real and media_ids(copy) == [1, 2]
    # and after cleanup nothing in the library changed, not even a new folder
    dry_run.cleanup()
    assert snapshot(library.root) == before
    assert not copy.exists()

    # and a normal run afterwards uses the real files, as they were
    monkeypatch.setattr(library.settings, "dry_run", False)
    assert placeholder.databasePlaceholder().databasePathHelper(5, "a creator") == real
    assert media_ids(real) == [1]
    assert cache.get("scan_mark") == "real"


def test_copies_are_removed_after_an_error(library, monkeypatch):
    monkeypatch.setattr(library.settings, "dry_run", True)
    copy = placeholder.databasePlaceholder().databasePathHelper(5, "a creator")
    root = dry_run._root
    assert copy.exists() and root.exists()
    try:
        raise RuntimeError("scan failed")
    except RuntimeError:
        dry_run.cleanup()
    assert not root.exists()


def test_copy_includes_rows_still_in_the_write_ahead_log(library, monkeypatch):
    real = library.profile / ".data" / "a creator" / "user_data.db"
    with contextlib.closing(sqlite3.connect(real)) as keep_open:
        keep_open.execute("PRAGMA journal_mode=WAL")
        keep_open.execute("PRAGMA wal_autocheckpoint=0")
        keep_open.execute(
            "INSERT INTO medias (media_id, post_id, api_type, media_type,"
            " created_at, posted_at, model_id) VALUES (3, 102, 'timeline',"
            " 'Images', '2024-01-01', '2024-01-02', 5)"
        )
        keep_open.commit()
        assert (real.parent / "user_data.db-wal").stat().st_size > 0
        monkeypatch.setattr(library.settings, "dry_run", True)
        copy = placeholder.databasePlaceholder().databasePathHelper(5, "a creator")
        assert media_ids(copy) == [1, 3]


# --- the switch-off list ----------------------------------------------------


@pytest.fixture
def event_loop_for_app():
    """The app's run wrapper uses the current loop; give it one and close it."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


def test_download_is_counted_not_run(monkeypatch, library, event_loop_for_app):
    called = []

    async def process_dicts(*a, **k):
        called.append(a)
        return (1, 0, 0, 0, 0)

    monkeypatch.setattr(download, "process_dicts", process_dicts)
    monkeypatch.setattr(download, "remove_downloads_with_hashes", lambda *a: called.append("dupes"))
    monkeypatch.setattr(library.settings, "text_only", False, raising=False)
    monkeypatch.setattr(library.settings, "size_max", None, raising=False)
    monkeypatch.setattr(library.settings, "size_min", None, raising=False)
    monkeypatch.setattr(library.settings, "skip_download_script", None, raising=False)
    dry_run.reset()

    monkeypatch.setattr(library.settings, "dry_run", True)
    assert download.download_process("a creator", 5, [object(), object()], []) == (0, 0, 0, 0, 0)
    assert called == []
    assert dry_run.data()["to_download"] == 2

    monkeypatch.setattr(library.settings, "dry_run", False)
    download.download_process("a creator", 5, [object()], [])
    assert called and called[-1] == "dupes"
    dry_run.reset()


def test_report_says_size_limits_are_not_applied(monkeypatch, library):
    monkeypatch.setattr(library.settings, "size_max", 1000, raising=False)
    dry_run.reset()
    assert "aren't applied" in dry_run.report()
    assert dry_run.data()["limits_not_applied"] == ["size_max"]


# --- the report -------------------------------------------------------------


def media(area, kind, media_id, protected=False, url=None):
    return SimpleNamespace(
        id=media_id, responsetype=area, mediatype=kind, protected=protected,
        url=url, link=url,
    )


@pytest.fixture
def report_env(monkeypatch, library):
    """Expected paths from a stand-in Placeholders that records how it was built."""
    built = []

    class FakePlaceholders:
        def __init__(self, ele, ext):
            self.ele, self.ext = ele, ext

        async def init(self, create=True):
            built.append((self.ele.id, self.ext, create))
            self.filepath = f"lib/{self.ele.responsetype}/{self.ele.id}.{self.ext}"
            return self

    import ofscraper.classes.placeholder as placeholder_module

    monkeypatch.setattr(placeholder_module, "Placeholders", FakePlaceholders)
    for name in ("size_max", "size_min", "skip_download_script"):
        monkeypatch.setattr(library.settings, name, None, raising=False)
    dry_run.reset()
    yield built
    dry_run.reset()


def test_report_by_creator_area_and_type(report_env):
    asyncio.run(dry_run.record("b creator", [
        media("Timeline", "Images", 1, url="https://cdn.example/a/1.jpeg?sig=x"),
        media("Timeline", "Images", 2, url="https://cdn.example/a/2.jpg"),
        media("Timeline", "Videos", 3, url="https://cdn.example/a/3.mp4"),
        media("Messages", "Videos", 4, protected=True),
    ]))
    asyncio.run(dry_run.record("a creator", [media("Timeline", "Images", 5, url="https://x/5.png")]))

    d = dry_run.data()
    assert d["to_download"] == 5
    assert [c["username"] for c in d["creators"]] == ["a creator", "b creator"]
    b = d["creators"][1]
    assert b["areas"] == [
        {"area": "messages", "type": "videos", "count": 1, "protected": 1},
        {"area": "timeline", "type": "images", "count": 2, "protected": 0},
        {"area": "timeline", "type": "videos", "count": 1, "protected": 0},
    ]
    # one sample per area first; the link's extension, mp4 for protected video;
    # and the paths are worked out without making folders
    assert b["sample_paths"] == ["lib/Timeline/1.jpeg", "lib/Messages/4.mp4", "lib/Timeline/2.jpg"]
    assert all(create is False for _, _, create in report_env)

    text = dry_run.report()
    assert text.startswith("Dry run: nothing was downloaded and nothing was changed.")
    assert "  timeline        3 to download (2 images, 1 videos)" in text
    assert "  messages        1 to download (1 videos, 1 protected)" in text
    assert "    lib/Messages/4.mp4" in text
    assert "A download would attempt up to 5 files from 2 creators." in text
    assert "aren't applied" not in text


def test_samples_stop_at_three_per_creator(report_env):
    asyncio.run(dry_run.record("a creator", [media("Timeline", "Images", i, url=f"https://x/{i}.jpg") for i in range(10)]))
    asyncio.run(dry_run.record("a creator", [media("Messages", "Images", 99, url="https://x/99.jpg")]))
    assert len(dry_run.data()["creators"][0]["sample_paths"]) == 3
    assert dry_run.data()["to_download"] == 11


def test_json_report(report_env, tmp_path):
    import json

    asyncio.run(dry_run.record("a creator", [media("Timeline", "Images", 1, url="https://x/1.jpg")]))
    out = tmp_path / "report.json"
    dry_run.write_json(out)
    assert json.loads(out.read_text(encoding="utf-8")) == dry_run.data()


def test_dry_run_json_implies_dry_run():
    params = {"dry_run_json": "report.json", "actions": ["download"]}
    check_dry_run(params)
    assert params["dry_run"] is True
    with pytest.raises(UsageError, match="--daemon"):
        check_dry_run({"dry_run_json": "r.json", "daemon": 5.0, "actions": ["download"]})


@pytest.mark.parametrize("dry", [True, False])
def test_naming_script_runs_only_outside_a_dry_run(monkeypatch, library, tmp_path, dry):
    import ofscraper.scripts.naming_script as naming

    script = tmp_path / "name.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(library.settings, "naming_script", str(script), raising=False)
    monkeypatch.setattr(library.settings, "dry_run", dry)
    ran = []

    async def async_run(*a, **k):
        ran.append(a)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(naming, "async_run", async_run)
    try:
        ele = SimpleNamespace(
            username="a creator", model_id=5, value="free", modified_responsetype="Timeline",
            label_string="", id=7, post={}, date=None,
        )
        monkeypatch.setattr(naming, "Post", SimpleNamespace)  # a post, for the payload
        asyncio.run(naming.naming_script("dir", "file.jpg", ele))
    except Exception:
        pass  # only whether the script was started matters here
    assert bool(ran) is not dry


@pytest.mark.parametrize(
    "run_script",
    [
        lambda: after_download.after_download_action_script("a creator", []),
        lambda: after_like.after_like_action_script("a creator", []),
        lambda: final_script.final_script(),
    ],
    ids=["after_action_script (download)", "after_action_script (like)", "post_script"],
)
@pytest.mark.parametrize("dry", [True, False])
def test_scripts_run_only_outside_a_dry_run(monkeypatch, library, tmp_path, run_script, dry):
    script = tmp_path / "script.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(library.settings, "after_action_script", str(script), raising=False)
    monkeypatch.setattr(library.settings, "post_script", str(script), raising=False)
    monkeypatch.setattr(library.settings, "command", "scraper", raising=False)
    monkeypatch.setattr(library.settings, "dry_run", dry)
    models = SimpleNamespace(
        get_processed=lambda a: set(),
        get_unprocessed=lambda a: [],
        get_model=lambda name: {"username": name, "id": 5},
    )
    ran = []
    for module in (after_download, after_like, final_script):
        monkeypatch.setattr(module, "run", lambda *a, **k: ran.append(a))
        monkeypatch.setattr(
            module, "manager", SimpleNamespace(Manager=SimpleNamespace(current_model_manager=models))
        )
    run_script()
    assert bool(ran) is not dry


@pytest.mark.parametrize("dry", [True, False])
def test_leftover_part_files_are_kept_in_a_dry_run(monkeypatch, library, tmp_path, dry):
    part = tmp_path / "temp" / ".file.mp4.part"
    part.parent.mkdir()
    part.write_bytes(b"x")
    monkeypatch.setattr(paths.data, "get_TempDir", lambda *a, **k: str(part.parent))
    monkeypatch.setattr(library.settings, "auto_resume", False, raising=False)
    monkeypatch.setattr(library.settings, "dry_run", dry)
    real_getattr = paths.of_env.getattr
    monkeypatch.setattr(
        paths.of_env, "getattr",
        lambda key, *a, **k: True if key == "BATCH_TEMPFILE_CLEANUP" else real_getattr(key, *a, **k),
    )
    paths.temp_cleanup()
    assert part.exists() is dry


def test_fansly_scans_everything_first_in_a_dry_run(monkeypatch, library):
    monkeypatch.setattr(pipeline.sites, "is_fansly", lambda: True)
    for name, value in {
        "command": "scraper", "actions": ["download"], "users_first": False, "text_only": False,
    }.items():
        monkeypatch.setattr(library.settings, name, value, raising=False)
    monkeypatch.setattr(library.settings, "dry_run", False)
    assert pipeline.applies()
    monkeypatch.setattr(library.settings, "dry_run", True)
    assert not pipeline.applies()


def test_postgresql_is_refused(monkeypatch):
    import ofscraper.db.postgres as postgres

    monkeypatch.setattr(postgres, "enabled", lambda: True)
    assert "PostgreSQL" in dry_run.refuse_reason()
    monkeypatch.setattr(postgres, "enabled", lambda: False)
    assert dry_run.refuse_reason() is None


# --- refused combinations ---------------------------------------------------


@pytest.mark.parametrize(
    "params, message",
    [
        ({"daemon": 30.0, "actions": ["download"]}, "--daemon"),
        ({"live": True, "actions": ["download"]}, "--live"),
        ({"live_watch": True, "actions": ["download"]}, "--live"),
        ({"actions": ["like"]}, "--action download"),
        ({"actions": []}, "--action download"),
    ],
)
def test_refused_combinations(params, message):
    with pytest.raises(UsageError, match=message):
        check_dry_run({"dry_run": True, **params})


@pytest.mark.parametrize(
    "params",
    [
        {"actions": ["download"]},
        {"actions": ["like", "download"]},
        {"actions": [], "scrape_paid": True},
    ],
)
def test_accepted_combinations(params):
    check_dry_run({"dry_run": True, **params})
    check_dry_run({"dry_run": False, "daemon": 30.0, **params})


def test_option_parses():
    from ofscraper.utils.args.parse.commands.main import program

    ctx = program.make_context(
        "ofscraper", ["--dry-run", "--action", "download"], resilient_parsing=False
    )
    assert ctx.params["dry_run"] is True

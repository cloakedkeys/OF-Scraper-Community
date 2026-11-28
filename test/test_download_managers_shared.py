"""The normal and DRM downloaders share their part writer, their cache of
reply data and the reading of that cache. These pin what each one does, so
the shared code keeps both behaviours: the arguments each passes, the cache
key each uses and what each does with a saved reply."""

import asyncio
import contextvars
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.commands.scraper.actions.download.managers.alt_download as ad
import ofscraper.commands.scraper.actions.download.managers.downloadmanager as dm
import ofscraper.commands.scraper.actions.download.managers.main_download as md
import ofscraper.commands.scraper.actions.utils.globals as common_globals
import ofscraper.utils.cache.cache as cache
import ofscraper.utils.settings as settings


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda *a, **k: SimpleNamespace(
            auto_resume=True, download_limit=0, command="download"
        ),
    )
    monkeypatch.setattr(common_globals, "log", logging.getLogger("test"), raising=False)
    monkeypatch.setattr(common_globals, "thread", None, raising=False)
    for name in ("attempt", "attempt2"):
        monkeypatch.setattr(
            common_globals, name, contextvars.ContextVar(name, default=0), raising=False
        )
    monkeypatch.setattr(md.common_logs, "get_medialog", lambda ele: "")
    monkeypatch.setattr(md.common_logs, "path_to_file_logger", lambda *a, **k: None)
    monkeypatch.setattr(dm, "get_medialog", lambda ele: "")
    monkeypatch.setattr(ad, "get_medialog", lambda ele: "")
    monkeypatch.setattr(ad, "temp_file_logger", lambda *a, **k: None)


ELE = SimpleNamespace(id=1, post_id=2, username="u", filename="f", url="x", mediatype="Videos")


def _manager(drm):
    """A manager that records its progress task calls."""
    mgr = (ad.AltDownloadManager if drm else md.MainDownloadManager)()
    calls = {"add": [], "remove": []}

    async def add(*args, **kwargs):
        calls["add"].append((args, kwargs))
        return "task"

    async def remove(task, ele):
        calls["remove"].append(task)

    mgr._add_download_job_task = add
    mgr._remove_download_job_task = remove
    return mgr, calls


def _reply(chunks, error=None):
    async def gen():
        for chunk in chunks:
            yield chunk
        if error:
            raise error

    return SimpleNamespace(iter_chunked=lambda size: gen())


def _write(mgr, drm, reply, holder, total=10, resume_size=0, on_first_chunk=None):
    if drm:
        coro = mgr._download_fileobject_writer_streamer(
            ELE, total, reply, holder, resume_size, on_first_chunk
        )
    else:
        coro = mgr._download_fileobject_writer_streamer(
            reply, ELE, holder, holder, total, resume_size, on_first_chunk
        )
    return asyncio.run(coro)


DRM = pytest.mark.parametrize("drm", [False, True], ids=["normal", "drm"])


@DRM
@pytest.mark.parametrize("resume_size", [0, 3])
def test_writer_appends_on_resume_and_replaces_otherwise(
    tmp_path, monkeypatch, drm, resume_size
):
    part = tmp_path / "f.part"
    part.write_bytes(b"old")
    holder = SimpleNamespace(tempfilepath=part, trunicated_filepath=tmp_path / "f.mp4")
    events = []
    module = ad if drm else md
    monkeypatch.setattr(
        # the fourth argument is the manager's log
        module, "send_chunk_msg", lambda ele, total, h, log: events.append((ele, total, h))
    )

    async def first():
        events.append("first")

    mgr, calls = _manager(drm)
    written = _write(mgr, drm, _reply([b"ab", b"cd"]), holder, 10, resume_size, first)
    assert written == 4
    assert part.read_bytes() == (b"oldabcd" if resume_size else b"abcd")
    # on_first_chunk runs once, after the first chunk is written and before
    # its progress message
    assert events == ["first", (ELE, 10, holder), (ELE, 10, holder)]
    if drm:
        assert calls["add"] == [((ELE, 10, holder), {})]
    else:
        assert calls["add"] == [
            ((ELE,), {"total": 10, "tempholderObj": holder, "placeholderObj": holder})
        ]
    assert calls["remove"] == ["task"]


@DRM
def test_writer_with_no_data_leaves_the_part(tmp_path, drm):
    part = tmp_path / "f.part"
    part.write_bytes(b"old")
    holder = SimpleNamespace(tempfilepath=part, trunicated_filepath=tmp_path / "f.mp4")
    mgr, calls = _manager(drm)
    assert _write(mgr, drm, _reply([]), holder) == 0
    assert part.read_bytes() == b"old"
    assert calls["remove"] == ["task"]


@DRM
@pytest.mark.parametrize(
    "error", [asyncio.TimeoutError(), aiohttp.ServerTimeoutError()], ids=["asyncio", "aiohttp"]
)
def test_writer_turns_a_read_timeout_into_a_retryable_error(
    tmp_path, monkeypatch, drm, error
):
    monkeypatch.setattr(ad if drm else md, "send_chunk_msg", lambda *a: None)
    part = tmp_path / "f.part"
    holder = SimpleNamespace(tempfilepath=part, trunicated_filepath=tmp_path / "f.mp4")
    mgr, calls = _manager(drm)
    with pytest.raises(Exception) as raised:
        _write(mgr, drm, _reply([b"ab"], error), holder)
    assert type(raised.value) is Exception
    assert str(raised.value) == "Chunk download timed out"
    assert part.read_bytes() == b"ab"
    assert calls["remove"] == ["task"]


@DRM
def test_writer_reraises_other_errors(tmp_path, monkeypatch, drm):
    monkeypatch.setattr(ad if drm else md, "send_chunk_msg", lambda *a: None)
    holder = SimpleNamespace(
        tempfilepath=tmp_path / "f.part", trunicated_filepath=tmp_path / "f.mp4"
    )
    mgr, calls = _manager(drm)
    error = ValueError("boom")
    with pytest.raises(ValueError) as raised:
        _write(mgr, drm, _reply([b"ab"], error), holder)
    assert raised.value is error
    assert calls["remove"] == ["task"]


def test_progress_task_labels(tmp_path, monkeypatch):
    added = []
    monkeypatch.setattr(
        dm.progress_updater,
        "download",
        SimpleNamespace(add_job_task=lambda *a, **k: added.append((a, k)) or "t"),
    )
    temp = SimpleNamespace(tempfilepath=tmp_path / "f.part")
    final = SimpleNamespace(trunicated_filepath=tmp_path / "f.mp4")
    asyncio.run(
        dm.DownloadManager._add_download_job_task(
            md.MainDownloadManager(), ELE, total=5, placeholderObj=final, tempholderObj=temp
        )
    )
    asyncio.run(ad.AltDownloadManager()._add_download_job_task(ELE, 6, temp))
    # the normal file is labelled with its final name; a DRM track with its part
    assert added == [
        ((f"{final.trunicated_filepath}\n",), {"total": 5, "file": temp.tempfilepath}),
        ((f"{temp.tempfilepath}\n",), {"total": 6}),
    ]


@pytest.fixture
def fake_cache(monkeypatch):
    store = {}
    monkeypatch.setattr(cache, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(cache, "set", lambda key, value, *a, **k: store.__setitem__(key, value))
    return store


def test_cache_keys(fake_cache):
    item = {"name": "track"}

    async def go():
        main, alt = md.MainDownloadManager(), ad.AltDownloadManager()
        await main._set_data(ELE, {"m": 1})
        await alt._set_data(ELE, item, {"a": 1})
        return await main._get_data(ELE), await alt._get_data(ELE, item)

    assert asyncio.run(go()) == ({"m": 1}, {"a": 1})
    assert fake_cache == {"1_u_headers": {"m": 1}, "track_1_u_headers": {"a": 1}}


def _holder(tmp_path, size):
    part = tmp_path / "f.part"
    if size is not None:
        part.write_bytes(b"x" * size)
    return SimpleNamespace(tempfilepath=part)


def _resume(drm, data, holder, monkeypatch):
    mgr = (ad.AltDownloadManager if drm else md.MainDownloadManager)()

    async def no_skip(*a):
        return None

    mgr._check_forced_skip = no_skip
    if drm:
        item = {"name": "track", "type": "video"}
        return asyncio.run(mgr._resume_data_handler_alt(data, item, ELE, holder))

    class _Placeholders:
        def __init__(self, ele, content_type):
            self.content_type = content_type

        async def init(self):
            return self

    monkeypatch.setattr(md.placeholder, "Placeholders", _Placeholders)
    return asyncio.run(mgr._resume_data_handler_main(data, ELE, holder))


@DRM
def test_resume_from_a_finished_part(tmp_path, monkeypatch, fake_cache, drm):
    holder = _holder(tmp_path, 10)
    data = {"content-total": 10, "content-type": "video/mp4", "validator": "v"}
    result = _resume(drm, data, holder, monkeypatch)
    if drm:
        assert result == ({"name": "track", "type": "video", "total": 10, "validator": "v"}, True)
    else:
        total, placeholderObj, check = result
        assert (total, placeholderObj.content_type, check) == (10, "mp4", True)
    assert holder.tempfilepath.read_bytes() == b"x" * 10


@pytest.mark.parametrize(
    "content_type, ext",
    [("video/quicktime", "mov"), ("application/octet-stream", "mp4"), ("video/mp4", "mp4")],
)
def test_resume_names_the_file_by_its_format(tmp_path, monkeypatch, fake_cache, content_type, ext):
    holder = _holder(tmp_path, 10)
    data = {"content-total": 10, "content-type": content_type, "validator": "v"}
    _, placeholderObj, _ = _resume(False, data, holder, monkeypatch)
    assert placeholderObj.content_type == ext


@DRM
def test_resume_from_a_part_larger_than_the_file(tmp_path, monkeypatch, fake_cache, drm):
    holder = _holder(tmp_path, 12)
    data = {"content-total": 10, "content-type": "video/mp4", "validator": None}
    result = _resume(drm, data, holder, monkeypatch)
    assert not holder.tempfilepath.exists()
    if drm:
        assert result == (
            {"name": "track", "type": "video", "total": 10, "validator": None},
            False,
        )
    else:
        assert result[0] == 10 and result[2] is None


@DRM
def test_resume_with_no_saved_size(tmp_path, monkeypatch, fake_cache, drm):
    fake_cache["track_1_u_headers"] = fake_cache["1_u_headers"] = {"old": 1}
    holder = _holder(tmp_path, 4)
    data = {"content-total": None, "content-type": "video/mp4"}
    result = _resume(drm, data, holder, monkeypatch)
    # there's nothing to check the part against, so it's removed
    assert not holder.tempfilepath.exists()
    if drm:
        # the DRM downloader also clears the saved reply; the normal one keeps it
        assert result == (
            {"name": "track", "type": "video", "total": None, "validator": None},
            False,
        )
        assert fake_cache == {"track_1_u_headers": None, "1_u_headers": {"old": 1}}
    else:
        assert result[0] is None and result[2] is None
        assert fake_cache == {"track_1_u_headers": {"old": 1}, "1_u_headers": {"old": 1}}


# --- the integrity check and the finishing steps all three managers share


@pytest.mark.parametrize(
    "duration, fallback, call, ok",
    [
        (5, False, ("verify", "f.mp4", 5), True),
        (5, True, ("verify", "f.mp4", 5), True),
        (None, False, ("verify", "f.mp4", None), True),
        # Fansly's: with no expected duration, the file needs only a duration
        (None, True, ("duration", "f.mp4"), False),
    ],
)
def test_integrity_check_runs_off_the_event_loop(monkeypatch, duration, fallback, call, ok):
    calls = []

    def verify(path, expected):
        calls.append(("verify", path, expected, threading.get_ident()))
        return True

    def duration_of(path):
        calls.append(("duration", path, threading.get_ident()))
        return None

    monkeypatch.setattr(dm, "verify_media_integrity", verify)
    monkeypatch.setattr(dm, "get_media_duration", duration_of)

    async def go():
        result = await md.MainDownloadManager()._verify_integrity(
            "f.mp4", SimpleNamespace(duration=duration), duration_fallback=fallback
        )
        return threading.get_ident(), result

    loop_thread, result = asyncio.run(go())
    ((*made, thread),) = calls
    assert tuple(made) == call
    # ffprobe doesn't block the downloads running on the event loop
    assert thread != loop_thread
    assert result is ok


async def _name():
    return "f.mp4"


@pytest.mark.parametrize(
    "mediatype, verify_all, checked",
    [
        ("Videos", False, False),
        ("Videos", True, True),
        ("Audios", True, True),
        ("Images", True, False),
    ],
)
def test_normal_downloader_checks_integrity_only_when_asked(
    tmp_path, monkeypatch, mediatype, verify_all, checked
):
    monkeypatch.setattr(
        settings, "get_settings", lambda *a, **k: SimpleNamespace(verify_all_integrity=verify_all)
    )
    checks = []
    monkeypatch.setattr(
        dm, "verify_media_integrity", lambda path, expected: checks.append(path) or True
    )
    part = tmp_path / "f.part"
    part.write_bytes(b"x")
    holder = SimpleNamespace(trunicated_filepath=tmp_path / "f.mp4")
    ele = SimpleNamespace(mediatype=mediatype, duration=1, final_filename=_name())
    mgr = md.MainDownloadManager()
    mgr._size_checker = AsyncMock()
    mgr._finalize_download = AsyncMock()
    assert asyncio.run(mgr._handle_result_main((1, part, holder), ele, "u", 1)) == (mediatype, 1)
    assert checks == ([part] if checked else [])
    mgr._finalize_download.assert_awaited_once_with(part, holder.trunicated_filepath, ele, "u", 1)


def test_drm_merge_is_always_checked_and_skips_the_profile_cache(tmp_path, monkeypatch):
    # a merge can come out truncated: checked even without verify_all_integrity
    monkeypatch.setattr(
        settings, "get_settings", lambda *a, **k: SimpleNamespace(verify_all_integrity=False)
    )
    merged = tmp_path / "merged.mp4"
    checks = []
    monkeypatch.setattr(
        dm, "verify_media_integrity", lambda path, expected: checks.append(path) or True
    )
    monkeypatch.setattr(
        ad.placeholder,
        "tempFilePlaceholder",
        lambda *a: SimpleNamespace(init=AsyncMock(return_value=SimpleNamespace(tempfilepath=merged))),
    )
    monkeypatch.setattr(ad, "get_ffmpeg", lambda: "ffmpeg")

    async def merge(*a, **k):
        merged.write_bytes(b"merged")
        return SimpleNamespace(stderr=b"")

    monkeypatch.setattr(ad, "async_run", merge)
    holder = SimpleNamespace(trunicated_filepath=tmp_path / "f.mp4")
    mgr = ad.AltDownloadManager()
    mgr._finalize_download = AsyncMock()
    ele = SimpleNamespace(id=1, post_id=2, mediatype="Videos", duration=1)
    assert asyncio.run(mgr._handle_result_alt(holder, ele, None, None, "u", 1)) == ("Videos", 0)
    assert checks == [merged]
    mgr._finalize_download.assert_awaited_once_with(
        merged, holder.trunicated_filepath, ele, "u", 1, profile_cache=False
    )


@pytest.mark.parametrize("manager", ["main", "drm"])
def test_a_stop_during_the_integrity_check_cancels_before_the_move(
    tmp_path, monkeypatch, manager
):
    # ffprobe runs off the event loop, so a stop can arrive while it runs:
    # both downloaders check again before moving the file
    monkeypatch.setattr(
        settings, "get_settings", lambda *a, **k: SimpleNamespace(verify_all_integrity=True)
    )
    monkeypatch.setattr(
        dm, "verify_media_integrity", lambda path, expected: ad.stop.request() or True
    )
    merged = tmp_path / "merged.mp4"
    holder = SimpleNamespace(trunicated_filepath=tmp_path / "f.mp4")
    if manager == "main":
        part = tmp_path / "f.part"
        part.write_bytes(b"x")
        mgr = md.MainDownloadManager()
        mgr._size_checker = AsyncMock()
        ele = SimpleNamespace(mediatype="Videos", duration=1, final_filename=_name())
        run = lambda: mgr._handle_result_main((1, part, holder), ele, "u", 1)  # noqa: E731
    else:
        monkeypatch.setattr(
            ad.placeholder,
            "tempFilePlaceholder",
            lambda *a: SimpleNamespace(
                init=AsyncMock(return_value=SimpleNamespace(tempfilepath=merged))
            ),
        )
        monkeypatch.setattr(ad, "get_ffmpeg", lambda: "ffmpeg")

        async def merge(*a, **k):
            merged.write_bytes(b"merged")
            return SimpleNamespace(stderr=b"")

        monkeypatch.setattr(ad, "async_run", merge)
        mgr = ad.AltDownloadManager()
        ele = SimpleNamespace(id=1, post_id=2, mediatype="Videos", duration=1)
        run = lambda: mgr._handle_result_alt(holder, ele, None, None, "u", 1)  # noqa: E731
    mgr._finalize_download = AsyncMock()
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    finally:
        ad.stop.clear()
    mgr._finalize_download.assert_not_awaited()


@pytest.mark.parametrize("profile_cache", [True, False])
@pytest.mark.parametrize("own_move", [False, True], ids=["move", "own-move"])
def test_finalize_download_steps_in_order(tmp_path, monkeypatch, profile_cache, own_move):
    steps = []
    temp = tmp_path / "f.part"
    temp.write_bytes(b"data")
    target = tmp_path / "f.mp4"

    def move_helper(src, dest, ele, username=None, model_id=None):
        steps.append(("move", username, model_id))
        src.replace(dest)
        return dest

    async def own(src, dest):
        steps.append("own move")
        src.replace(dest)
        return dest

    async def get_hash(path, thread):
        return "hash"

    async def mark(ele, **kwargs):
        steps.append(("mark", kwargs["filepath"], kwargs["size"], kwargs["hashdata"]))

    async def profile(ele, thread):
        steps.append("profile")

    async def saved(path):
        steps.append(("saved", path))

    async def after(path, ele):
        steps.append(("after", path))

    monkeypatch.setattr(dm.common_paths, "moveHelper", move_helper)
    monkeypatch.setattr(dm.common_paths, "addGlobalDir", lambda folder, dirs: steps.append("dir"))
    monkeypatch.setattr(dm.common_paths, "set_time", lambda path, date: steps.append("time"))
    monkeypatch.setattr(dm.common, "get_hash", get_hash)
    monkeypatch.setattr(dm, "mark_media_as_downloaded", mark)
    monkeypatch.setattr(dm, "set_profile_cache", profile)
    mgr = md.MainDownloadManager()
    mgr._after_download_script = after
    ele = SimpleNamespace(
        id=1, postdate="2020-01-01T00:00:00", add_filepath=lambda p: steps.append(("filepath", p))
    )
    path = asyncio.run(
        mgr._finalize_download(
            temp, target, ele, "u", 1,
            move=own if own_move else None, profile_cache=profile_cache, on_saved=saved,
        )
    )
    assert path == target and target.read_bytes() == b"data"
    assert steps == [
        "own move" if own_move else ("move", "u", 1),
        "dir",
        "time",
        ("mark", target, 4, "hash"),
        *(["profile"] if profile_cache else []),
        ("saved", target),
        ("filepath", target),
        ("after", target),
    ]

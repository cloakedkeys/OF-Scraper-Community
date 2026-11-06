"""Fansly video: best delivery, HLS and DASH through ffmpeg, re-runs.

The account-media fixture is a sanitised capture. No master playlist or MPD
was captured, so the ones used here are written by the tests.
"""

import asyncio
import contextlib
import copy
import json
import logging
import pathlib
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

import ofscraper.commands.scraper.actions.download.managers.downloadmanager as dm
import ofscraper.commands.scraper.actions.download.managers.fansly_stream_download as stream_mod
import ofscraper.commands.scraper.actions.download.run as run_mod
import ofscraper.commands.scraper.actions.utils.globals as common_globals
import ofscraper.filters.media.filters as filters
import ofscraper.utils.settings as settings
from ofscraper.classes.of.media import Media
from ofscraper.classes.of.posts import Post
from ofscraper.managers.sessionmanager import ofsession
from ofscraper.sites.fansly import auth as fansly_auth
from ofscraper.sites.fansly import video

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "fansly"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/140.0"
TEST_SIGNED = {"Key-Pair-Id": "KP-TEST-1", "Signature": "SIG-TEST-2", "Policy": "POL-TEST-3"}
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not on PATH")

MASTER = (
    "#EXTM3U\n#EXT-X-VERSION:4\n#EXT-X-INDEPENDENT-SEGMENTS\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=400000,AVERAGE-BANDWIDTH=380000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2"\n'
    "r360/media.m3u8\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=9000000,AVERAGE-BANDWIDTH=8000000,RESOLUTION=3840x2160,CODECS="avc1.640033,mp4a.40.2"\n'
    "r2160/media.m3u8\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"\n'
    "r1080/media.m3u8\n"
)
EMPTY_MASTER = "#EXTM3U\n#EXT-X-VERSION:4\n#EXT-X-INDEPENDENT-SEGMENTS\n"
PROTECTED_MPD = (
    '<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">'
    "<Period><AdaptationSet mimeType=\"video/mp4\">"
    '<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc"/>'
    '<Representation id="1" width="3840" height="2160" bandwidth="9000000"/>'
    "</AdaptationSet></Period></MPD>\n"
)


# ------------------------------------------------------------------ helpers

def raw_media():
    data = json.loads((FIXTURES / "video_4k_account_media.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data["response"][0])


def variant(media, mime):
    return next(v for v in media["variants"] if v["mimetype"] == mime)


def drop_variant(raw, mime):
    raw["media"]["variants"] = [v for v in raw["media"]["variants"] if v["mimetype"] != mime]
    return raw


def make_ele(account_media, direct=True):
    raw = account_media["media"]
    direct_url = next(
        (loc["location"] for loc in raw.get("locations") or [] if str(loc["locationId"]) == "1"),
        None,
    ) if direct else None
    media = {
        "id": int(raw["id"]),
        "type": "video",
        "canView": True,
        "files": {"full": {"url": direct_url}},
        "fansly": {
            "accountMediaId": account_media["id"],
            "mediaId": raw["id"],
            "isPreview": False,
            "media": raw,
        },
    }
    post = Post(
        {"id": 100000000000000005, "postedAt": "2026-01-02T03:04:05+00:00", "text": ""},
        100000000000000002,
        "a-creator",
        responsetype="timeline",
    )
    return Media(media, 0, post)


def point_at(raw, mime, base):
    """Move a stream variant's location (and the direct file's) to the local test server."""
    for direct in raw.get("locations") or []:
        direct["location"] = base + urlparse(direct["location"]).path
    loc = variant(raw, mime)["locations"][0]
    loc["location"] = base + urlparse(loc["location"]).path
    loc["metadata"] = dict(TEST_SIGNED)
    return loc["location"]


class FakeResponse:
    def __init__(self, body, status=200):
        self.ok = status < 400
        self.status = status
        self._body = body

    async def text_(self):
        return self._body


class FakeSession:
    def __init__(self, bodies):
        self.bodies = bodies
        self.calls = []

    @contextlib.asynccontextmanager
    async def requests_async(self, url=None, **kwargs):
        self.calls.append((url, kwargs))
        yield FakeResponse(self.bodies.get(url, ""), 200 if url in self.bodies else 404)


@pytest.fixture
def env(monkeypatch, tmp_path):
    real = settings.get_settings()
    stub = SimpleNamespace(**{
        **dict(real), "site": "fansly", "verify_all_integrity": True, "size_max": 0,
        "size_min": 0, "force_all": False, "force_model_unique": False, "download_sems": 2,
        "ssl_verify": "default", "download_limit": 0, "proxy": None, "hash": "xxh128",
    })
    monkeypatch.setattr(settings, "get_settings", lambda: stub)
    # the download globals, without the shared logger (it writes to the user's log folder)
    common_globals.set_up_contexvars()
    pool = ThreadPoolExecutor(max_workers=4)
    for name, value in {
        "thread": pool, "sem": asyncio.Semaphore(2), "lock": asyncio.Lock(),
        "log": logging.getLogger("shared"), "dirSet": set(), "localDirSet": set(),
        "fileHashes": {}, "total_bytes": 0,
    }.items():
        monkeypatch.setattr(common_globals, name, value, raising=False)
    monkeypatch.setattr(fansly_auth, "read_auth", lambda *a, **k: {"user_agent": UA})

    out_dir, temp_dir = tmp_path / "out", tmp_path / "temp"
    out_dir.mkdir()
    temp_dir.mkdir()

    class FakePlaceholders:
        def __init__(self, ele, ext):
            self.filename = f"{ele.filename}.{ext}"
            self.filepath = self.trunicated_filepath = out_dir / self.filename

        async def init(self):
            return self

    class FakeTemp:
        def __init__(self, ele, name):
            self.tempfilepath = temp_dir / name

        async def init(self):
            return self

    monkeypatch.setattr(stream_mod, "placeholder",
                        SimpleNamespace(Placeholders=FakePlaceholders, tempFilePlaceholder=FakeTemp))
    marks = []

    async def mark(ele, **kw):
        marks.append((ele.id, kw))

    async def noop(*a, **k):
        return None

    async def no_skip(*a, **k):
        return False

    # never let a test fall through to the normal downloader and its real URL
    async def no_main_download(self, *a, **k):
        raise AssertionError("normal downloader used")

    monkeypatch.setattr(run_mod.MainDownloadManager, "main_download", no_main_download)
    monkeypatch.setattr(stream_mod, "mark_media_as_downloaded", mark)
    monkeypatch.setattr(stream_mod, "set_profile_cache", noop)
    monkeypatch.setattr(dm, "after_download_script", noop)
    monkeypatch.setattr(dm, "skip_download_script", no_skip)
    monkeypatch.setattr(dm.system, "check_free_size", lambda: True)
    ffmpeg_runs = []
    if FFMPEG:
        monkeypatch.setattr(stream_mod, "get_ffmpeg", lambda: FFMPEG)
    else:
        monkeypatch.setattr(stream_mod, "get_ffmpeg", lambda: "ffmpeg")
    real_run = subprocess.run

    def counting_run(cmd, *a, **k):
        ffmpeg_runs.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(stream_mod.subprocess, "run", counting_run)
    yield SimpleNamespace(out=out_dir, temp=temp_dir, marks=marks, stub=stub, ffmpeg_runs=ffmpeg_runs)
    pool.shutdown(wait=True)


def files_in(*dirs):
    return sorted(p.name for d in dirs for p in d.rglob("*") if p.is_file())


class Server:
    """Serves files from a dict and records every request's path and headers."""

    def __init__(self, files):
        self.files = files
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path
                outer.seen.append((path, {k.lower(): v for k, v in self.headers.items()}))
                body = outer.files.get(path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self):
        return f"http://localhost:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


def ffmpeg_make(args, cwd):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
           "-c:v", "mpeg4", "-g", "10", "-c:a", "aac", "-shortest", *args]
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type:format=duration,format_name",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def assert_cdn_request(headers):
    cookie = headers.get("cookie", "")
    for k, v in TEST_SIGNED.items():
        assert f"CloudFront-{k}={v}" in cookie
    assert headers.get("origin") == "https://fansly.com"
    assert headers.get("referer") == "https://fansly.com/"
    assert headers.get("user-agent") == UA
    assert "authorization" not in headers


async def run_download(c, ele):
    return await run_mod.download(c, ele, 100000000000000002, "a-creator")


# ------------------------------------------------------------------ DoD 1

def test_4k_stream_is_chosen_over_the_720p_direct_file():
    raw = raw_media()["media"]
    assert (raw["width"], raw["height"]) == (1280, 720)
    best = video.select_best(raw)
    assert best.kind == video.HLS
    assert (best.width, best.height) == (3840, 2160)
    assert best.url == variant(raw, video.HLS_MIME)["locations"][0]["location"]
    kinds = {c.kind: (c.width, c.height) for c in video.candidates(raw)}
    assert kinds == {video.DIRECT: (1280, 720), video.HLS: (3840, 2160), video.DASH: (3840, 2160)}


def test_ties_prefer_direct_then_hls_then_dash():
    raw = raw_media()["media"]
    for v in raw["variants"]:
        if v["mimetype"] in (video.HLS_MIME, video.DASH_MIME):
            v["width"], v["height"], v["metadata"] = 1280, 720, "{}"
    assert video.select_best(raw).kind == video.DIRECT
    del raw["locations"]
    assert video.select_best(raw).kind == video.HLS


def test_cloudfront_cookies_come_from_metadata_or_the_url_query():
    assert video.cloudfront_cookies({"location": "https://x/a.m3u8", "metadata": dict(TEST_SIGNED)}) == {
        "CloudFront-Policy": "POL-TEST-3", "CloudFront-Key-Pair-Id": "KP-TEST-1",
        "CloudFront-Signature": "SIG-TEST-2"}
    url = "https://x/a.m3u8?Policy=P1&Key-Pair-Id=K1&Signature=S1"
    assert video.cloudfront_cookies({"location": url}) == {
        "CloudFront-Policy": "P1", "CloudFront-Key-Pair-Id": "K1", "CloudFront-Signature": "S1"}


def test_only_fansly_items_on_a_fansly_run_take_the_stream_path(env):
    ele = make_ele(raw_media())
    assert video.uses_stream(ele) is True
    env.stub.site = "onlyfans"
    assert video.uses_stream(ele) is False
    env.stub.site = "fansly"
    assert video.uses_stream(make_ele(drop_variant(drop_variant(raw_media(), video.HLS_MIME),
                                                   video.DASH_MIME))) is False


def test_onlyfans_items_keep_the_normal_downloader(env, monkeypatch):
    env.stub.site = "onlyfans"
    calls = []

    async def main_download(self, c, ele, username, model_id):
        calls.append(ele.id)
        return "videos", 1

    async def stream_download(self, *a):
        raise AssertionError("stream path used for an OnlyFans item")

    monkeypatch.setattr(run_mod.MainDownloadManager, "main_download", main_download)
    monkeypatch.setattr(run_mod.FanslyStreamDownloadManager, "stream_download", stream_download)
    post = Post({"id": 1, "postedAt": "2026-01-02T03:04:05+00:00", "text": ""}, 2, "a-creator")
    of_ele = Media({"id": 7, "type": "video", "canView": True,
                    "files": {"full": {"url": "https://cdn.example/files/abc.mp4"}}}, 0, post)
    assert asyncio.run(run_download(None, of_ele)) == ("videos", 1)
    assert calls == [7]


def test_fansly_item_whose_best_is_a_stream_takes_the_stream_path(env, monkeypatch):
    calls = []

    async def main_download(self, *a):
        raise AssertionError("normal downloader used for a 4K stream")

    async def stream_download(self, c, ele, username, model_id):
        calls.append(ele.id)
        return "videos", 1

    monkeypatch.setattr(run_mod.MainDownloadManager, "main_download", main_download)
    monkeypatch.setattr(run_mod.FanslyStreamDownloadManager, "stream_download", stream_download)
    ele = make_ele(raw_media())
    assert asyncio.run(run_download(None, ele)) == ("videos", 1)
    assert calls == [ele.id]


# ------------------------------------------------------------------ DoD 2

def test_master_playlist_picks_the_largest_rendition():
    base = "https://cdn3.fansly.com/new/1/2/2.m3u8"
    assert video.best_rendition_url(MASTER, base) == "https://cdn3.fansly.com/new/1/2/r2160/media.m3u8"


def test_a_media_playlist_is_used_as_it_is():
    text = (FIXTURES / "media_playlist.m3u8").read_text(encoding="utf-8")
    assert video.best_rendition_url(text, "https://x/a.m3u8") == "https://x/a.m3u8"


def test_empty_master_fails_clearly_and_saves_nothing(env, caplog):
    raw = raw_media()
    hls_url = variant(raw["media"], video.HLS_MIME)["locations"][0]["location"]
    ele = make_ele(raw)
    session = FakeSession({hls_url: EMPTY_MASTER})
    with pytest.raises(video.EmptyMasterPlaylist):
        video.best_rendition_url(EMPTY_MASTER, hls_url)
    result = asyncio.run(run_download(session, ele))
    assert result == ("skipped", 0)
    assert files_in(env.out, env.temp) == []
    assert env.ffmpeg_runs == [] and env.marks == []
    # the master is fetched once, with the CloudFront cookies, never guessing other names
    assert [u for u, _ in session.calls] == [hls_url]
    kw = session.calls[0][1]
    assert set(kw["cookies"]) == {"CloudFront-Policy", "CloudFront-Key-Pair-Id", "CloudFront-Signature"}
    assert kw["actions"] == [stream_mod.HEADERS]


# ------------------------------------------------------------------ DoD 3

@needs_ffmpeg
def test_hls_stream_is_copied_into_one_playable_mp4(env, tmp_path):
    seg_dir = tmp_path / "segments"
    seg_dir.mkdir()
    # dump_extra repeats the video headers on every keyframe, as real HLS
    # segments do, so each segment can be decoded on its own
    ffmpeg_make(["-bsf:v", "dump_extra=freq=keyframe", "-f", "segment", "-segment_time", "1",
                 "-segment_format", "mpegts", "segment-%d.ts"], seg_dir)
    segments = sorted(p.name for p in seg_dir.glob("segment-*.ts"))
    assert segments == ["segment-0.ts", "segment-1.ts", "segment-2.ts"]

    raw = raw_media()
    with Server({}) as srv:
        url = point_at(raw["media"], video.HLS_MIME, srv.base)
        root = urlparse(url).path.rsplit("/", 1)[0]
        srv.files.update({
            urlparse(url).path: MASTER.encode(),
            f"{root}/r2160/media.m3u8": (FIXTURES / "media_playlist.m3u8").read_bytes(),
            **{f"{root}/r2160/{name}": (seg_dir / name).read_bytes() for name in segments},
        })
        ele = make_ele(raw)

        async def go():
            async with ofsession.download_session() as c:
                return await run_download(c, ele)

        result = asyncio.run(go())

    assert result[0] == "Videos" and result[1] > 0
    out = env.out / f"{ele.filename}.mp4"
    assert files_in(env.out, env.temp) == [out.name]
    info = probe(out)
    assert "mp4" in info["format"]["format_name"]
    assert sorted(s["codec_type"] for s in info["streams"]) == ["audio", "video"]
    assert 2.0 < float(info["format"]["duration"]) < 4.0

    requested = [p for p, _ in srv.seen]
    assert requested[0] == urlparse(url).path
    assert f"{root}/r2160/media.m3u8" in requested
    assert all(f"{root}/r2160/{n}" in requested for n in segments)
    assert not any("/r360/" in p or "/r1080/" in p for p in requested)
    assert not any(p.endswith(".mp4") for p in requested)  # the 720p direct file
    for _, headers in srv.seen:
        assert_cdn_request(headers)
    assert len(env.marks) == 1
    mark_id, kw = env.marks[0]
    assert mark_id == ele.id and kw["downloaded"] is True and kw["hashdata"]
    assert pathlib.Path(kw["filepath"]) == out and kw["size"] == out.stat().st_size


# ------------------------------------------------------------------ DoD 4

def test_dash_is_used_when_there_is_no_hls():
    raw = drop_variant(raw_media(), video.HLS_MIME)["media"]
    best = video.select_best(raw)
    assert best.kind == video.DASH and (best.width, best.height) == (3840, 2160)


def test_protected_dash_is_reported_and_writes_no_file(env, caplog):
    raw = drop_variant(raw_media(), video.HLS_MIME)
    dash_url = variant(raw["media"], video.DASH_MIME)["locations"][0]["location"]
    ele = make_ele(raw)
    session = FakeSession({dash_url: PROTECTED_MPD})
    with caplog.at_level("INFO"):
        result = asyncio.run(run_download(session, ele))
    assert result == ("forced_skipped", 0)
    assert files_in(env.out, env.temp) == []
    assert env.ffmpeg_runs == [] and env.marks == []
    assert video.dash_is_protected(PROTECTED_MPD)
    assert not video.dash_is_protected(PROTECTED_MPD.replace("ContentProtection", "SupplementalProperty"))


@needs_ffmpeg
def test_unprotected_dash_merges_video_and_audio(env, tmp_path):
    raw = drop_variant(raw_media(), video.HLS_MIME)
    with Server({}) as srv:
        url = point_at(raw["media"], video.DASH_MIME, srv.base)
        name = urlparse(url).path.rsplit("/", 1)[1]
        dash_dir = tmp_path / "dash"
        dash_dir.mkdir()
        ffmpeg_make(["-f", "dash", "-seg_duration", "1", name], dash_dir)
        root = urlparse(url).path.rsplit("/", 1)[0]
        srv.files.update({f"{root}/{p.name}": p.read_bytes() for p in dash_dir.iterdir()})
        ele = make_ele(raw)

        async def go():
            async with ofsession.download_session() as c:
                return await run_download(c, ele)

        result = asyncio.run(go())

    assert result[0] == "Videos" and result[1] > 0
    out = env.out / f"{ele.filename}.mp4"
    assert files_in(env.out, env.temp) == [out.name]
    assert sorted(s["codec_type"] for s in probe(out)["streams"]) == ["audio", "video"]
    assert len(srv.seen) > 2
    for _, headers in srv.seen:
        assert_cdn_request(headers)


# ------------------------------------------------------------------ DoD 5

def test_rerun_downloads_nothing(env, monkeypatch):
    raw = raw_media()
    hls_url = variant(raw["media"], video.HLS_MIME)["locations"][0]["location"]
    session = FakeSession({hls_url: (FIXTURES / "media_playlist.m3u8").read_text(encoding="utf-8")})
    stream_runs = []

    async def fake_ffmpeg(self, url, temp, cookies):
        stream_runs.append(url)
        temp.write_bytes(b"\0" * 2048)

    async def ok(*a, **k):
        return None

    monkeypatch.setattr(stream_mod.FanslyStreamDownloadManager, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(stream_mod.FanslyStreamDownloadManager, "_integrity_check", ok)
    downloaded = set()
    monkeypatch.setattr(filters, "get_media_ids_downloaded", lambda **kw: list(downloaded))
    monkeypatch.setattr(filters.separate, "seperate_avatars", lambda m: m)

    def one_run():
        todo = filters.previous_download_filter([make_ele(raw)], username="a-creator",
                                                model_id=100000000000000002)
        results = [asyncio.run(run_download(session, ele)) for ele in todo]
        downloaded.update(mark_id for mark_id, kw in env.marks if kw["downloaded"])
        return results

    assert one_run() == [("Videos", 2048)]
    assert len(stream_runs) == 1 and len(session.calls) == 1
    assert one_run() == []
    assert len(stream_runs) == 1 and len(session.calls) == 1
    assert files_in(env.out) == [f"{make_ele(raw).filename}.mp4"]

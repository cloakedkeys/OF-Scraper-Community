"""Fansly videos saved without sound because the CDN refused their audio are
checked again, ``SILENT_RECHECKS`` times, and downloaded again once the audio
is accepted.

No request is sent anywhere: ``account/media``, the audio check and the
download are faked, and the profile cache is a dict.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.utils.settings as settings
from ofscraper.commands.scraper.actions.download.utils import silent

MODEL = 100000000000000002


def make_ele(n):
    return SimpleNamespace(
        id=2000 + n,
        post_id=3000 + n,
        media={"fansly": {"accountMediaId": str(1000 + n), "media": {"id": str(2000 + n)}}},
        replace_silent=None,
    )


def answer(n):
    return {"id": str(1000 + n), "media": {"id": str(2000 + n), "variants": [{}]}}


@pytest.fixture
def run(monkeypatch):
    store = {}
    state = SimpleNamespace(
        store=store, asked=[], checks=[], downloads=[], verdict=silent.REFUSED,
        saved=True, api_error=None, answers=None, mediatypes=["Images", "Videos"],
    )

    def get(key, default=None):
        return store.get(key, default)

    def set_(key, value, **kw):
        store[key] = value

    @asynccontextmanager
    async def session(**kwargs):
        yield "api"

    async def account_media(c, ids):
        state.asked.append(list(ids))
        if state.api_error:
            raise state.api_error
        return [answer(int(i) - 1000) for i in ids] if state.answers is None else state.answers

    async def check_audio(c, raw):
        state.checks.append(raw["id"])
        verdict = state.verdict
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    async def download(c, ele):
        state.downloads.append((ele.id, ele.replace_silent))
        return state.saved

    real = settings.get_settings()
    monkeypatch.setattr(
        settings, "get_settings",
        lambda: SimpleNamespace(**{**dict(real), "mediatypes": state.mediatypes,
                                   "cached_disabled": False}),
    )
    monkeypatch.setattr(silent.cache, "get", get)
    monkeypatch.setattr(silent.cache, "set", set_)
    monkeypatch.setattr(silent.fansly_api, "account_media", account_media)
    monkeypatch.setattr(
        silent.manager, "Manager",
        SimpleNamespace(session=SimpleNamespace(aget_ofsession=session)),
    )

    def once(media=()):
        by_id = {str(m.id): m for m in media}
        asyncio.run(silent.recheck("dl", "a-creator", MODEL, by_id, check_audio, download))

    state.once = once
    return state


def test_a_refused_check_uses_one_check_and_downloads_nothing(run):
    ele = make_ele(0)
    silent.flag(MODEL, ele, "out/video.mp4")
    run.once([ele])
    assert run.asked == [["1000"]] and run.checks == ["2000"]
    assert run.downloads == []
    assert silent.flags(MODEL)["2000"]["checks"] == 1


def test_accepted_audio_downloads_again_in_replace_mode_and_drops_the_flag(run):
    ele = make_ele(0)
    silent.flag(MODEL, ele, "out/video.mp4")
    run.verdict = silent.ACCEPTED
    run.once([ele])
    assert run.downloads == [(2000, "out/video.mp4")]
    assert ele.replace_silent is None
    # the fresh links are what the download uses
    assert ele.media["fansly"]["media"] == answer(0)["media"]
    assert silent.flags(MODEL) == {}


def test_a_failed_download_again_keeps_the_flag_and_uses_a_check(run):
    ele = make_ele(0)
    silent.flag(MODEL, ele, "out/video.mp4")
    run.verdict, run.saved = silent.ACCEPTED, False
    run.once([ele])
    assert len(run.downloads) == 1
    assert silent.flags(MODEL)["2000"]["checks"] == 1


def test_after_the_last_refused_check_the_flag_goes_and_no_more_checks_run(run, caplog):
    ele = make_ele(0)
    silent.flag(MODEL, ele, "out/video.mp4")
    with caplog.at_level("INFO", logger="shared"):
        for _ in range(silent.limit()):
            run.once([ele])
    assert silent.flags(MODEL) == {}
    assert "keeping the video without sound" in caplog.text and "2000" in caplog.text
    run.once([ele])
    assert len(run.asked) == silent.limit() == 3


@pytest.mark.parametrize("failure", ["check", "api"])
def test_a_check_that_fails_for_another_reason_uses_nothing(run, failure):
    ele = make_ele(0)
    silent.flag(MODEL, ele, "out/video.mp4")
    if failure == "check":
        run.verdict = silent.FAILED
    else:
        run.api_error = RuntimeError("429")
    run.once([ele])
    run.verdict = ConnectionError("reset")
    run.api_error = None
    run.once([ele])
    assert silent.flags(MODEL)["2000"]["checks"] == 0


def test_gone_media_uses_a_check_so_the_flag_does_not_live_forever(run):
    ele = make_ele(0)
    silent.flag(MODEL, ele, "out/video.mp4")
    run.answers = []
    run.once([ele])
    assert run.checks == []
    assert silent.flags(MODEL)["2000"]["checks"] == 1


def test_a_run_without_videos_skips_the_checks(run):
    silent.flag(MODEL, make_ele(0), "out/video.mp4")
    run.mediatypes = ["Images"]
    run.once()
    assert run.asked == [] and silent.flags(MODEL)["2000"]["checks"] == 0


def test_one_call_fetches_links_for_every_flagged_video(run):
    eles = [make_ele(n) for n in range(3)]
    for ele in eles:
        silent.flag(MODEL, ele, f"out/{ele.id}.mp4")
    run.once(eles)
    assert run.asked == [["1000", "1001", "1002"]]
    assert sorted(run.checks) == ["2000", "2001", "2002"]


def test_accepted_audio_for_a_video_not_in_this_run_waits_without_using_a_check(run):
    silent.flag(MODEL, make_ele(0), "out/video.mp4")
    run.verdict = silent.ACCEPTED
    run.once([])
    assert run.downloads == []
    assert silent.flags(MODEL)["2000"]["checks"] == 0


def test_flagging_again_keeps_the_count_and_clear_removes_it(run):
    ele = make_ele(0)
    silent.flag(MODEL, ele, "out/old.mp4")
    run.once([ele])
    silent.flag(MODEL, ele, "out/new.mp4")
    record = silent.flags(MODEL)["2000"]
    assert record["checks"] == 1 and record["path"] == "out/new.mp4"
    silent.clear(MODEL, ele.id)
    assert silent.flags(MODEL) == {}


@pytest.mark.parametrize("site, stopped, expected", [
    ("fansly", False, True), ("onlyfans", False, False), ("fansly", True, False),
])
def test_the_checks_run_after_a_fansly_creator_s_downloads(monkeypatch, site, stopped, expected):
    import ofscraper.commands.scraper.actions.download.download as download_mod

    calls = []

    async def download_process(*a, **k):
        calls.append("download")
        return (0, 0, 0, 0, 0)

    async def recheck_silent(username, model_id, all_media):
        calls.append(("recheck", username, model_id, all_media))

    monkeypatch.setattr(download_mod, "download_process", download_process)
    monkeypatch.setattr(download_mod, "recheck_silent", recheck_silent)
    monkeypatch.setattr(download_mod.sites, "is_fansly", lambda: site == "fansly")
    if stopped:
        download_mod.stop.requested.set()
    try:
        asyncio.run(download_mod.downloader(
            username="a-creator", model_id=MODEL, posts=[], media=["new"],
            all_media=["new", "saved earlier"]))
    finally:
        download_mod.stop.requested.clear()
    assert calls[0] == "download"
    assert calls[1:] == ([("recheck", "a-creator", MODEL, ["new", "saved earlier"])]
                         if expected else [])


def test_no_checks_when_the_limit_is_zero(run, monkeypatch):
    monkeypatch.setenv("OFSC_SILENT_RECHECKS", "0")
    monkeypatch.setattr(silent, "limit", lambda: 0)
    silent.flag(MODEL, make_ele(0), "out/video.mp4")
    assert silent.flags(MODEL) == {}

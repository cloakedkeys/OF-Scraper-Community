"""Fansly videos saved without sound because the CDN refused their audio.

A silent save is final: the file counts as downloaded and no later run looks
at it again. If the refusal was temporary, the sound is lost for good. So a
video saved silent because its audio was refused is flagged, and its audio
is checked again: once at the end of the run that saved it, then once per
later download run of the creator, ``SILENT_RECHECKS`` times in all.

A check fetches fresh links for every flagged video of the creator in one
``account/media`` call, then asks for each video's audio playlist and first
audio file. Accepted: the video is downloaded again and the silent file is
replaced once the new copy passes its checks. Refused (or the media is gone):
one check is used. Anything else (network, rate limit): nothing is used.

The flags live in the profile cache, one entry per creator.
"""

import collections
import logging

import ofscraper.managers.manager as manager
import ofscraper.utils.cache.cache as cache
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.settings as settings
from ofscraper.sites.fansly import api as fansly_api

log = logging.getLogger("shared")

ACCEPTED, REFUSED, GONE, FAILED = "accepted", "refused", "gone", "failed"


def _key(model_id):
    return f"fansly_silent_v1_{model_id}"


def flags(model_id) -> dict:
    """media id -> {"post_id", "account_media_id", "path", "checks"}"""
    try:
        found = cache.get(_key(model_id), default=None)
    except Exception as E:
        log.debug(f"Fansly: could not read the silent-video flags: {E}")
        return {}
    return dict(found) if isinstance(found, dict) else {}


def _save(model_id, records):
    cache.set(_key(model_id), records)


def flag(model_id, ele, path):
    """``ele`` was saved at ``path`` without sound because its audio was refused."""
    account_media_id = ((ele.media or {}).get("fansly") or {}).get("accountMediaId")
    if not account_media_id or not limit():
        return
    records = flags(model_id)
    record = records.get(str(ele.id)) or {"checks": 0}
    record.update(
        post_id=str(ele.post_id), account_media_id=str(account_media_id), path=str(path)
    )
    records[str(ele.id)] = record
    _save(model_id, records)


def clear(model_id, media_id):
    """``media_id`` was saved with sound: no more checks."""
    records = flags(model_id)
    if records.pop(str(media_id), None) is not None:
        _save(model_id, records)


def limit() -> int:
    try:
        return max(0, int(of_env.getattr("SILENT_RECHECKS")))
    except (TypeError, ValueError):
        return 0


def _videos_wanted():
    types = settings.get_settings().mediatypes or []
    return any(str(t).lower() == "videos" for t in types)


async def recheck(c, username, model_id, media_by_id, check_audio, download):
    """Check the creator's flagged videos once (see the module docstring).

    ``media_by_id``: this run's Media by id, used for a download again.
    ``check_audio(c, raw)``: one of ACCEPTED, REFUSED, FAILED for a raw
    Fansly Media with fresh links. ``download(c, ele)``: True when ``ele``
    was saved again with sound.
    """
    records = flags(model_id)
    allowed = limit()
    if not records or not allowed or not _videos_wanted():
        return
    try:
        async with manager.Manager.session.aget_ofsession() as api:
            found = await fansly_api.account_media(
                api, [r["account_media_id"] for r in records.values()]
            )
    except Exception as E:
        log.debug(f"Fansly: checking the audio of silent videos failed: {E}")
        return
    answers = {str(x.get("id")): x for x in found or [] if isinstance(x, dict)}
    outcomes = collections.Counter()
    for media_id, record in list(records.items()):
        raw = (answers.get(record["account_media_id"]) or {}).get("media") or {}
        if str(raw.get("id")) != media_id or not raw.get("variants") and not raw.get("locations"):
            outcome = GONE
        else:
            try:
                outcome = await check_audio(c, raw)
            except Exception as E:
                log.debug(f"Media {media_id}: checking its audio failed: {type(E).__name__}")
                outcome = FAILED
        if outcome == ACCEPTED:
            ele = media_by_id.get(media_id)
            if ele is None:
                # not in this run (another area or date range): next time
                outcomes["accepted, not in this run"] += 1
                continue
            ele.media.setdefault("fansly", {})["media"] = raw
            ele.replace_silent = record.get("path")
            saved = False
            try:
                saved = await download(c, ele)
            finally:
                ele.replace_silent = None
            if saved:
                records.pop(media_id, None)
                outcomes["saved with sound"] += 1
                continue
            outcome = REFUSED
        outcomes[outcome] += 1
        if outcome in (REFUSED, GONE):
            record["checks"] = record.get("checks", 0) + 1
            if record["checks"] >= allowed:
                records.pop(media_id, None)
                log.info(
                    f"Media {media_id}: its audio was refused {record['checks']} "
                    f"{'time' if record['checks'] == 1 else 'times'}; keeping the "
                    "video without sound"
                )
    # the download may have cleared its own flag in the meantime
    current = flags(model_id)
    _save(model_id, {k: v for k, v in records.items() if k in current})
    log.debug(
        f"Fansly: checked the audio of {sum(outcomes.values())} silent "
        f"{'video' if sum(outcomes.values()) == 1 else 'videos'}: "
        + ", ".join(f"{n} {what}" for what, n in sorted(outcomes.items()))
    )

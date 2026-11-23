"""The run's totals (StatsManager.totals) and the end-of-run summary built on them."""

import logging
from types import SimpleNamespace

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
from ofscraper.managers.stats import StatsManager
from ofscraper.managers.utils.state import EActivity

DOWNLOAD = EActivity.ScrapeActivity.DOWNLOAD
PAID = EActivity.PaidActivity.SCRAPE_PAID_DOWNLOAD
METADATA = EActivity.ScrapeActivity.METADATA
TEXT = EActivity.ScrapeActivity.TEXT


def media(kind="Videos", size=0, attempted=True, succeeded=True, stopped=False):
    return SimpleNamespace(
        mediatype=kind, size=size, download_attempted=attempted,
        download_succeeded=succeeded, download_stopped=stopped,
        metadata_attempted=attempted, metadata_succeeded=succeeded,
    )


def post(attempted=True, succeeded=True):
    return SimpleNamespace(text_download_attempted=attempted, text_download_succeeded=succeeded)


def filled():
    stats = StatsManager()
    stats.update_stats("alice", DOWNLOAD, [
        media("Videos", 1000), media("Images", 10), media("Audios", 5),
        media(attempted=False), media(succeeded=False), media(stopped=True),
    ])
    stats.update_stats("alice", PAID, [media("Videos", 2000)])
    stats.update_stats("bob", DOWNLOAD, [media("Images", 7), media(succeeded=False)])
    stats.update_stats("bob", METADATA, [
        media("Videos"), media("Images"), media(succeeded=None), media(succeeded=False),
    ])
    stats.update_stats("bob", TEXT, [post(), post(attempted=False), post(succeeded=False)])
    # a creator with nothing done
    stats.update_stats("carol", DOWNLOAD, [])
    return stats


def test_totals_add_up_every_counter_across_creators():
    totals = filled().totals(per_creator=True)
    assert totals["downloads"] == {
        "videos": 2, "audios": 1, "photos": 2, "skipped": 1, "failed": 2, "stopped": 1, "bytes": 3022,
    }
    assert totals["metadata"] == {"videos": 1, "audios": 0, "photos": 1, "unchanged": 1, "failed": 1}
    assert totals["text"] == {"saved": 1, "skipped": 1, "failed": 1}
    assert totals["creators_active"] == 2
    assert totals["per_creator"] == [
        {"username": "alice", "new_files": 4, "bytes": 3015, "failed": 1},
        {"username": "bob", "new_files": 1, "bytes": 7, "failed": 1},
        {"username": "carol", "new_files": 0, "bytes": 0, "failed": 0},
    ]


def test_totals_of_an_empty_run():
    totals = StatsManager().totals(per_creator=True)
    assert totals["creators_active"] == 0 and totals["per_creator"] == []
    assert not any(totals["downloads"].values())
    assert "per_creator" not in StatsManager().totals()


def test_printed_summary_uses_the_totals(caplog):
    with caplog.at_level(logging.WARNING, logger="shared"):
        filled().print_global_summary()
    text = caplog.text
    assert " ➜ TOTAL ITEMS: 5" in text
    assert " ➜ FAILED:      2 overall" in text
    assert " ➜ TOTAL CHANGED: 2" in text
    assert " ➜ SAVED:   1" in text

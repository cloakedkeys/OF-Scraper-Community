"""The activity-group helpers in managers/utils/state.py and the Rich
summaries in managers/stats.py (#872)."""

import ofscraper.main.open.load  # noqa: F401  (import order: see AGENTS.md)
from ofscraper.managers.stats import (
    DownloadStats,
    LikeStats,
    MetadataStats,
    StatsManager,
    TextStats,
)
from ofscraper.managers.utils.state import EActivity, StateManager

PAID = EActivity.PaidActivity.SCRAPE_PAID_DOWNLOAD
PAID_META = EActivity.PaidActivity.SCRAPE_PAID_METADATA
DOWNLOAD = EActivity.ScrapeActivity.DOWNLOAD
LIKE = EActivity.ScrapeActivity.LIKE


def _state():
    state = StateManager()
    state.set_queue(DOWNLOAD, ["creator2", "creator1"])
    state.set_queue(LIKE, ["creator1", "creator3"])
    state.set_queue(PAID, ["creator4", "creator2"])
    state.set_queue(PAID_META, ["creator5"])
    for activity, name in [(DOWNLOAD, "creator2"), (LIKE, "creator3"), (PAID, "creator4")]:
        state.mark_as_processed(name, activity)
    return state


def test_queued_usernames_by_group_keep_first_appearance_order():
    state = _state()
    assert state.get_paid_queued_usernames() == ["creator4", "creator2", "creator5"]
    assert state.get_scrape_queued_usernames() == ["creator1", "creator3", "creator2"]
    all_names = state.get_all_queued_usernames()
    assert sorted(all_names) == ["creator1", "creator2", "creator3", "creator4", "creator5"]
    assert len(all_names) == len(set(all_names))


def test_reset_processed_touches_only_its_group():
    state = _state()
    state.reset_paid_processed_status()
    assert state.get_processed(PAID) == set()
    assert state.get_processed(DOWNLOAD) == {"creator2"}
    state.reset_scrape_processed_status()
    assert state.get_processed(DOWNLOAD) == set() and state.get_processed(LIKE) == set()
    assert state.get_queued_usernames(DOWNLOAD) == ["creator2", "creator1"]

    state = _state()
    state.reset_all_processed_status()
    assert all(state.get_processed(a) == set() for a in (PAID, DOWNLOAD, LIKE))


def test_clear_queues_touches_only_its_group():
    state = _state()
    state.clear_paid_queues()
    assert state.get_paid_queued_usernames() == []
    assert state.get_processed(PAID) == set()
    assert state.get_processed(DOWNLOAD) == {"creator2"}
    state.clear_scrape_queues()
    assert state.get_all_queued_usernames() == []

    state = _state()
    state.clear_all_queues()
    assert state.get_all_queued_usernames() == []
    assert state.get_processed(DOWNLOAD) == set()


def test_clear_stats_by_group():
    stats = StatsManager()
    for name in ("creator1", "creator2"):
        for activity in (PAID, DOWNLOAD, LIKE):
            stats._get_stat_obj(name, activity)
    stats.clear_paid_stats()
    assert all(set(per_user) == {DOWNLOAD, LIKE} for per_user in stats._stats.values())
    stats._get_stat_obj("creator1", PAID)
    stats.clear_scraper_activity_stats()
    assert stats._stats["creator1"] == {PAID: stats._stats["creator1"][PAID]}
    assert stats._stats["creator2"] == {}


def test_empty_summaries_are_plain():
    assert str(MetadataStats("m")) == (
        "(0 changed media item total [0 videos, 0 audios, 0 photos], "
        "0 items unchanged, 0 skipped, 0 failed)"
    )
    assert str(TextStats("t")) == r"\[0 text, 0 already saved, 0 failed]"
    assert str(DownloadStats("d")) == (
        r"(0.00 MB) (0 downloads total \[0 videos, 0 audios, 0 photos], "
        "0 skipped, 0 failed, 0 stopped)"
    )
    assert str(LikeStats("l")) == "[0 posts checked, (0 liked, 0 unliked), 0 unchanged, 0 failed]"


def test_non_zero_counts_are_styled():
    meta = MetadataStats("m")
    meta.changed_video, meta.unchanged_count, meta.failed_count = 2, 1, 3
    assert str(meta) == (
        "(2 changed media item total [[bold green]2 videos[/bold green], 0 audios, "
        "0 photos], [bold yellow]1 items unchanged[/bold yellow], 0 skipped, "
        "[bold red]3 failed[/bold red])"
    )

    text = TextStats("t")
    text.text_count, text.skipped_count = 1, 2
    # "already saved" is never styled
    assert str(text) == r"\[[bold green]1 text[/bold green], 2 already saved, 0 failed]"

    download = DownloadStats("d")
    download.video_count, download.skipped_count = 1, 1
    download.total_bytes = 2 * 1024**2
    assert str(download) == (
        r"([bold]2.00 MB[/bold]) ([bold]1 downloads total[/bold] "
        r"\[[bold green]1 videos[/bold green], 0 audios, 0 photos], "
        "[bold yellow]1 skipped[/bold yellow], 0 failed, 0 stopped)"
    )
    # a skip alone still bolds the total, which follows has_changes
    skipped_only = DownloadStats("d")
    skipped_only.skipped_count = 1
    assert "[bold]0 downloads total[/bold]" in str(skipped_only)

    likes = LikeStats("l")
    likes.posts_checked, likes.posts_liked, likes.posts_failed = 3, 1, 1
    assert str(likes) == (
        "[[bold]3 posts checked[/bold], ([bold green]1 liked[/bold green], "
        "0 unliked), [bold]1 unchanged[/bold], [bold red]1 failed[/bold red]]"
    )

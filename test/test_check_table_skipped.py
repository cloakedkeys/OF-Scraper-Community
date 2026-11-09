"""Check table: an item skipped from the table isn't marked downloaded (#353).

Since #326 the database doesn't record a size-limit or script skip as
downloaded, so the table's Downloaded column, its stats and its filter don't
either. These tests send rows through the real process_download_queue and
InputApp.update_cell_state with the download faked.
"""

import queue
from types import SimpleNamespace

import pytest
from textual.widgets import ContentSwitcher

import ofscraper.classes.table.app as table_app
import ofscraper.commands.check as check

# process_dicts returns (videos, audios, photos, forced_skipped, skipped);
# forced_skipped is a size-limit or script skip, skipped is a failure
DOWNLOADED = (0, 0, 1, 0, 0)
SIZE_SKIPPED = (0, 0, 0, 1, 0)
FAILED = (0, 0, 0, 0, 1)


class _StopLoop(BaseException):
    """Ends process_download_queue's endless loop; not caught by its handlers."""


class _Cell:
    def __init__(self):
        self.text = None

    def update(self, text):
        self.text = text


class _FakeTable:
    def __init__(self):
        self.cells = {}

    def update_cell_at_key(self, key, col, value):
        self.cells[(key, col)] = value


class _FakeApp:
    """Borrows InputApp's real methods, with no Textual screen behind them."""

    update_cell_state = table_app.InputApp.update_cell_state
    update_search_info = table_app.InputApp.update_search_info
    _filter_runner = table_app.InputApp._filter_runner
    action_select_all_undownloaded = table_app.InputApp.action_select_all_undownloaded

    def __init__(self, rows):
        self.table_data = rows
        self._filtered_rows = list(rows)
        self.table = _FakeTable()
        self._sortkey = "number"
        self._reverse = False
        self.main_page = 1
        self.is_sidebar_open = False
        self.nodes = {
            "#main_per_page_input": SimpleNamespace(value=50),
            "#db_info_bar": _Cell(),
            "#view_info_bar": _Cell(),
        }

    def query_one(self, selector):
        if selector is ContentSwitcher:
            return SimpleNamespace(current="table_page")
        return self.nodes[selector]

    def update_cart_info(self):
        pass

    def set_page(self):
        pass


def _row(index, media_id):
    return {
        "index": index,
        "number": index + 1,
        "media_id": media_id,
        "post_id": 100 + media_id,
        "username": "a_creator",
        "unlocked": True,
        "downloaded": False,
        "download_type": "normal",
        "download_cart": "[downloading]",
        "mediatype": "images",
    }


@pytest.fixture
def run_queue(monkeypatch):
    """Send rows through process_download_queue; each media id's download
    returns the result given for it."""

    def run(rows, results):
        app = _FakeApp(rows)
        row_queue = queue.Queue()
        for row in rows:
            row_queue.put((str(row["index"]), row))
        monkeypatch.setattr(table_app, "row_queue", row_queue)
        monkeypatch.setattr(table_app, "app", app, raising=False)

        media = {
            row["media_id"]: SimpleNamespace(
                id=row["media_id"],
                filename=f"file{row['media_id']}.jpg",
                post=SimpleNamespace(id=row["post_id"]),
            )
            for row in rows
        }
        collection = SimpleNamespace(find_media_item=lambda mid: media.get(mid))
        monkeypatch.setitem(check.check_user_dict, "1", {"collection": collection})

        def fake_get_data(row):
            item = media[int(row["media_id"])]
            return item, item.post, row["username"], 1

        def fake_process_dicts(username, model_id, medialist, posts):
            return results[medialist[0].id]

        done = []

        def fake_sleep(seconds):
            # The retry after a failure sleeps too; stop only once the batch
            # has finished
            if done:
                raise _StopLoop

        model_manager = SimpleNamespace(
            get_model=lambda username: SimpleNamespace(
                id=1, active=True, expired_string=None
            ),
            mark_as_processed=lambda *a, **k: None,
            clear_scrape_queues=lambda: None,
        )
        stats_manager = SimpleNamespace(
            update_and_print_stats=lambda *a, **k: None,
            clear_scraper_activity_stats=lambda: None,
        )
        monkeypatch.setattr(
            check.manager,
            "Manager",
            SimpleNamespace(
                current_model_manager=model_manager, stats_manager=stats_manager
            ),
        )
        monkeypatch.setattr(check, "_get_data_from_row", fake_get_data)
        monkeypatch.setattr(check, "process_dicts", fake_process_dicts)
        monkeypatch.setattr(check, "data_refill", lambda model_id: None)
        monkeypatch.setattr(check, "after_download_action_script", lambda *a: None)
        monkeypatch.setattr(check, "final_action", lambda: done.append(True))
        monkeypatch.setattr(check.operations, "table_init_create", lambda **k: None)
        monkeypatch.setattr(check.network, "check_cdm", lambda: True)
        monkeypatch.setattr(check.time, "sleep", fake_sleep)

        with pytest.raises(_StopLoop):
            check.process_download_queue()
        assert done, "the batch never finished"
        return app

    return run


def _cell(app, row):
    return app.table.cells[(str(row["index"]), "download_cart")].plain


def test_size_skipped_item_is_skipped_not_downloaded(run_queue):
    row = _row(0, 1)
    app = run_queue([row], {1: SIZE_SKIPPED})
    assert row["download_cart"] == "[skipped]"
    assert _cell(app, row) == "[skipped]"
    assert row["downloaded"] is False


def test_downloaded_item_is_marked_downloaded(run_queue):
    row = _row(0, 1)
    app = run_queue([row], {1: DOWNLOADED})
    assert row["download_cart"] == "[downloaded]"
    assert _cell(app, row) == "[downloaded]"
    assert row["downloaded"] is True


def test_failed_item_is_failed_not_downloaded(run_queue):
    row = _row(0, 1)
    app = run_queue([row], {1: FAILED})
    assert row["download_cart"] == "[failed]"
    assert _cell(app, row) == "[failed]"
    assert row["downloaded"] is False


def test_stats_count_a_skipped_item_as_missing(run_queue):
    rows = [_row(0, 1), _row(1, 2), _row(2, 3)]
    app = run_queue(rows, {1: SIZE_SKIPPED, 2: DOWNLOADED, 3: FAILED})
    text = app.nodes["#db_info_bar"].text
    assert "[DL'd: 1]" in text
    assert "[Missing: 2]" in text
    assert "[Unique Missing: 2]" in text


def test_downloaded_false_filter_keeps_a_skipped_item(run_queue):
    rows = [_row(0, 1), _row(1, 2)]
    app = run_queue(rows, {1: SIZE_SKIPPED, 2: DOWNLOADED})
    # Only "Downloaded False" ticked in the filter
    app.nodes["#downloaded"] = SimpleNamespace(compare=lambda value: value == "False")
    app._filter_runner()
    assert [r["media_id"] for r in app._filtered_rows] == [1]


def test_add_all_new_still_leaves_a_skipped_item_alone(run_queue):
    # Behaviour kept: "Add All New" adds only rows in [] or [failed], so a
    # skipped row isn't queued again just because it's no longer downloaded
    rows = [_row(0, 1), _row(1, 2)]
    app = run_queue(rows, {1: SIZE_SKIPPED, 2: FAILED})
    app.action_select_all_undownloaded()
    assert rows[0]["download_cart"] == "[skipped]"
    assert rows[1]["download_cart"] == "[added]"

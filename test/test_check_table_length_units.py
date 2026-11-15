"""Check table filter sidebar (Ctrl+S): which Length box is hours, minutes, seconds.

The Min/Max length boxes only said Hour/Min/Sec as placeholder text, which
disappears once a box has a value, and a saved or picked length filled all
three with "0". The unit names now sit above the boxes, and a zero part is
left empty so its placeholder shows. These tests drive the real InputApp.
"""

import asyncio
import queue
from types import SimpleNamespace

import pytest
from textual.widgets import Input, Label

import ofscraper.classes.table.app as table_app


class _Settings(SimpleNamespace):
    def __getattr__(self, name):
        return None


def _row(index, length):
    return {
        "index": index,
        "number": index,
        "download_cart": "[]",
        "username": "a_creator",
        "downloaded": False,
        "unlocked": True,
        "download_type": "normal",
        "other_posts_with_media": [],
        "post_media_count": 1,
        "mediatype": "Videos",
        "post_date": "2024-01-01",
        "media": 1,
        "length": length,
        "responsetype": "Timeline",
        "price": "Free",
        "post_id": 100 + index,
        "media_id": 1000 + index,
        "text": "t",
    }


@pytest.fixture
def start(monkeypatch, tmp_path, request):
    values = getattr(request, "param", {})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        table_app.settings,
        "get_settings",
        lambda *a, **k: _Settings(db_sort="number", desc=False, **values),
    )
    monkeypatch.setattr(
        table_app.settings,
        "get_args",
        lambda *a, **k: SimpleNamespace(text=False, text_only=False),
    )
    monkeypatch.setattr(table_app.settings, "update_args", lambda *a, **k: None)
    monkeypatch.setattr(table_app, "row_queue", queue.Queue())


async def _settle(pilot):
    for _ in range(5):
        await pilot.pause(0.02)


def _drive(steps):
    async def run():
        app = table_app.InputApp()
        app.table_data = [_row(1, "0:01:30")]
        app._sortkey = None
        app._reverse = False
        async with app.run_test(size=(245, 60)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await _settle(pilot)
            field = app.query_one("#length")
            app.query_one("#options_sidebar").scroll_to_widget(
                field, animate=False, top=True
            )
            await _settle(pilot)
            await steps(app, pilot, field)

    asyncio.run(run())


def _values(app, *ids):
    return [app.query_one(f"#{i}", Input).value for i in ids]


MIN = ("min_hour", "min_minute", "min_second")
MAX = ("max_hour", "max_minute", "max_second")


def test_unit_names_sit_above_their_boxes_and_stay_with_a_value(start):
    async def steps(app, pilot, field):
        for box in MIN + MAX:
            app.query_one(f"#{box}", Input).value = "5"
        await _settle(pilot)

        header = list(field.query_one("#unitHeader").query(".length_unit"))
        assert [str(label.render()) for label in header] == [
            "Hours",
            "Minutes",
            "Seconds",
        ]
        for label, lo, hi in zip(header, MIN, MAX):
            lo_box = app.query_one(f"#{lo}", Input).region
            hi_box = app.query_one(f"#{hi}", Input).region
            region = label.region
            assert label.display and region.height >= 1
            assert field.region.contains_region(region)
            # above both rows, in the same column
            assert region.bottom <= lo_box.y < hi_box.y
            assert lo_box.x <= region.x < lo_box.right
            assert not region.overlaps(lo_box)

    _drive(steps)


def test_rows_are_labelled(start):
    async def steps(app, pilot, field):
        for container, text in [("#minContainer", "Min length"), ("#maxContainer", "Max length")]:
            label = field.query_one(container).query_one(Label)
            assert str(label.render()) == text

    _drive(steps)


@pytest.mark.parametrize(
    "start", [{"length_min": 90, "length_max": 3600}], indirect=True
)
def test_saved_lengths_leave_zero_parts_empty(start):
    async def steps(app, pilot, field):
        assert _values(app, *MIN) == ["", "1", "30"]
        assert _values(app, *MAX) == ["1", "", ""]
        # an empty part is the placeholder, still telling you which box it is
        assert app.query_one("#max_minute", Input).placeholder == "Minutes"

    _drive(steps)


def test_a_picked_length_leaves_zero_parts_empty(start):
    async def steps(app, pilot, field):
        field.update_table_val("0:01:30")
        await _settle(pilot)
        assert _values(app, *MIN) == ["", "1", "30"]
        assert _values(app, *MAX) == ["", "1", "30"]
        # and the filter still matches that length
        assert field.compare("0:01:30")
        assert not field.compare("0:02:00")

    _drive(steps)

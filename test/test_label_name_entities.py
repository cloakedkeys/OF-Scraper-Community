"""OnlyFans sends label names HTML-escaped: "Nudes &amp; Sexy Videos".

The name was used as sent, so the progress line read "getting posts from
label -> Nudes &amp; Sexy Videos", --label "nudes & sexy videos" matched
nothing, and {label} and the database got "&amp;". get_labels_data now
decodes the name once.
"""

import asyncio
from types import SimpleNamespace

import pytest

import ofscraper.data.api.labels as labels

RAW = [
    {"id": 1, "name": "Nudes &amp; Sexy Videos"},
    {"id": 2, "name": "26 years old"},
    {"id": 3, "name": "Q&amp;A &lt;3 &quot;best&quot; &#39;s"},
    {"id": 4, "name": None},
]


@pytest.fixture
def api(monkeypatch):
    async def fake_tasks(generators):
        for gen in generators:
            await gen.aclose()
        return [dict(x) for x in RAW]

    monkeypatch.setattr(labels.sites, "is_fansly", lambda: False)
    monkeypatch.setattr(labels, "process_tasks_labels", fake_tasks)


def test_label_names_are_decoded(api):
    out = asyncio.run(labels.get_labels_data(1, c=None))
    assert [x["name"] for x in out] == [
        "Nudes & Sexy Videos",
        "26 years old",
        "Q&A <3 \"best\" 's",
        None,
    ]
    # nothing else about the label changes
    assert [x["id"] for x in out] == [1, 2, 3, 4]


def test_label_filter_matches_the_name_as_written(api, monkeypatch):
    seen = {}

    async def fake_known(labels_, *a, **k):
        seen["labels"] = [x["name"] for x in labels_]
        return {}

    async def fake_posts(labels_, *a, **k):
        return labels_

    monkeypatch.setattr(labels, "get_known_label_posts", fake_known)
    monkeypatch.setattr(labels, "get_posts_for_labels", fake_posts)
    monkeypatch.setattr(labels, "update_check", lambda *a, **k: None)
    monkeypatch.setattr(
        labels.settings,
        "get_settings",
        lambda: SimpleNamespace(label=["nudes & sexy videos"]),
    )

    async def run():
        return await labels.get_labels(1, c=None, username="a_creator")

    asyncio.run(run())
    assert seen["labels"] == ["Nudes & Sexy Videos"]

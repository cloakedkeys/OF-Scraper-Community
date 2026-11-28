"""Unit tests for the --filter/--neg-filter text filters and --before/--after."""

import re
from types import SimpleNamespace

import arrow
import pytest

import ofscraper.filters.media.filters as filters


class _Settings(SimpleNamespace):
    def __getattr__(self, name):
        return None


def _patch_settings(monkeypatch, **overrides):
    monkeypatch.setattr(
        filters.settings, "get_settings", lambda: _Settings(**overrides)
    )


POSTS = [
    SimpleNamespace(text="New SET today"),
    SimpleNamespace(text="new set tomorrow"),
    SimpleNamespace(text="something else"),
    SimpleNamespace(text=None),
]


def _texts(items):
    return [x.text for x in items]


@pytest.mark.parametrize(
    "patterns, kept",
    [
        (None, [p.text for p in POSTS]),
        ([], [p.text for p in POSTS]),
        # all lower-case: any case
        (["new set"], ["New SET today", "new set tomorrow"]),
        # an upper-case letter makes it case-sensitive
        (["New SET"], ["New SET today"]),
        # every pattern has to match
        (["set", "today"], ["New SET today"]),
    ],
)
def test_text_filter(monkeypatch, patterns, kept):
    _patch_settings(monkeypatch, filter=patterns)
    assert _texts(filters.post_text_filter(POSTS)) == kept


@pytest.mark.parametrize(
    "patterns, kept",
    [
        (None, [p.text for p in POSTS]),
        (["new set"], ["something else", None]),
        (["New SET"], ["new set tomorrow", "something else", None]),
        (["today", "tomorrow"], ["something else", None]),
    ],
)
def test_neg_text_filter(monkeypatch, patterns, kept):
    _patch_settings(monkeypatch, neg_filter=patterns)
    assert _texts(filters.post_neg_text_filter(POSTS)) == kept


@pytest.mark.parametrize(
    "func, key",
    [
        (filters.post_text_filter, "filter"),
        (filters.post_neg_text_filter, "neg_filter"),
    ],
)
def test_invalid_pattern_raises_only_with_posts(monkeypatch, func, key):
    _patch_settings(monkeypatch, **{key: ["("]})
    assert func([]) == []
    with pytest.raises(re.error):
        func(POSTS)


def _dated(attr):
    return [
        SimpleNamespace(name="old", **{attr: "2020-01-01T00:00:00+00:00"}),
        SimpleNamespace(name="mid", **{attr: "2022-06-01T12:00:00+00:00"}),
        SimpleNamespace(name="new", **{attr: "2024-01-01T00:00:00+00:00"}),
        SimpleNamespace(name="undated", **{attr: None}),
    ]


@pytest.mark.parametrize(
    "func, attr",
    [
        (filters.posts_date_filter, "date"),
        (filters.posts_date_filter_media, "postdate"),
    ],
)
@pytest.mark.parametrize(
    "before, after, kept",
    [
        (None, None, ["old", "mid", "new", "undated"]),
        ("2023-01-01", None, ["old", "mid", "undated"]),
        (None, "2021-01-01", ["mid", "new", "undated"]),
        ("2023-01-01", "2021-01-01", ["mid", "undated"]),
    ],
)
def test_date_filters(monkeypatch, func, attr, before, after, kept):
    _patch_settings(
        monkeypatch,
        before=arrow.get(before) if before else None,
        after=arrow.get(after) if after else None,
    )
    assert [x.name for x in func(_dated(attr))] == kept

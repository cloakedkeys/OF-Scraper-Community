"""Unit tests for the yes/no model filters (flags, subtype) and model sorting."""

from types import SimpleNamespace

import pytest

import ofscraper.filters.models.flags as flags
import ofscraper.filters.models.sort as sort
import ofscraper.filters.models.subtype as subtype
import ofscraper.utils.settings as settings


class _Settings(SimpleNamespace):
    def __getattr__(self, name):
        return None


def _patch_settings(monkeypatch, **overrides):
    monkeypatch.setattr(settings, "get_settings", lambda: _Settings(**overrides))


def _names(models):
    return [m.name for m in models]


@pytest.mark.parametrize(
    "setting, attr",
    [
        ("promo", "lowest_promo_claim"),
        ("all_promo", "lowest_promo_all"),
        ("last_seen", "last_seen"),
    ],
)
def test_presence_flags(monkeypatch, setting, attr):
    models = [
        SimpleNamespace(name="has", **{attr: 0}),
        SimpleNamespace(name="missing", **{attr: None}),
    ]
    _patch_settings(monkeypatch, **{setting: True})
    assert _names(flags.promoFilterHelper(models)) == ["has"]
    _patch_settings(monkeypatch, **{setting: False})
    assert _names(flags.promoFilterHelper(models)) == ["missing"]
    _patch_settings(monkeypatch, **{setting: None})
    assert _names(flags.promoFilterHelper(models)) == ["has", "missing"]


def _priced(name, current, promo, regular):
    return SimpleNamespace(
        name=name,
        lowest_promo_claim=None,
        lowest_promo_all=None,
        last_seen=None,
        final_current_price=current,
        final_promo_price=promo,
        final_regular_price=regular,
    )


def test_free_trial_flag(monkeypatch):
    models = [
        _priced("trial_current", 0, 5, 10),
        _priced("trial_promo", 5, 0, 10),
        _priced("paid", 5, 5, 10),
        _priced("free", 0, 0, 0),
    ]
    _patch_settings(monkeypatch, free_trial=True)
    assert _names(flags.promoFilterHelper(models)) == ["trial_current", "trial_promo"]
    _patch_settings(monkeypatch, free_trial=False)
    assert _names(flags.promoFilterHelper(models)) == ["paid", "free"]


@pytest.mark.parametrize(
    "setting, attr", [("renewal", "renewed"), ("sub_status", "active")]
)
def test_subtype_flags(monkeypatch, setting, attr):
    models = [
        SimpleNamespace(name="on", renewed="2024-01-01", active=True),
        SimpleNamespace(name="off", renewed=None, active=False),
    ]
    _patch_settings(monkeypatch, **{setting: True})
    assert _names(subtype.subType(models)) == ["on"]
    _patch_settings(monkeypatch, **{setting: False})
    assert _names(subtype.subType(models)) == ["off"]
    _patch_settings(monkeypatch, **{setting: None})
    assert _names(subtype.subType(models)) == ["on", "off"]


@pytest.mark.parametrize(
    "key, attr",
    [
        ("name", "name"),
        ("last-seen", "final_last_seen"),
        ("expired", "final_expired"),
        ("subscribed", "final_subscribed"),
        ("current-price", "final_current_price"),
        ("promo-price", "final_promo_price"),
        ("renewal-price", "final_renewal_price"),
        ("regular-price", "final_regular_price"),
    ],
)
def test_sort_by_each_key(monkeypatch, key, attr):
    def model(name, value):
        m = SimpleNamespace(name=name)
        if attr != "name":
            setattr(m, attr, value)
        return m

    # value order differs from name order, so a name sort can't pass by accident
    models = [model("a", 3), model("b", 1), model("c", 2)]
    expected = ["a", "b", "c"] if attr == "name" else ["b", "c", "a"]
    _patch_settings(monkeypatch, sort=key, desc=False)
    assert _names(sort.sort_models_helper(models)) == expected
    _patch_settings(monkeypatch, sort=key, desc=True)
    assert _names(sort.sort_models_helper(models)) == expected[::-1]


@pytest.mark.parametrize("key", [None, "unknown"])
def test_sort_falls_back_to_name(monkeypatch, key):
    models = [SimpleNamespace(name="b"), SimpleNamespace(name="a")]
    _patch_settings(monkeypatch, sort=key, desc=False)
    assert _names(sort.sort_models_helper(models)) == ["a", "b"]

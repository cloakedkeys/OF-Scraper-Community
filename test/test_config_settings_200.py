"""config.json settings that were ignored, rewritten or misread (#200).

Given/When/Then:
1. Given advanced_options.incremental_downloads is false, when the config is
   read and rebuilt for saving, then it stays false.
2. Given cache-mode "disabled" or "api_disabled", when settings are merged,
   then the cache (or only the API cache) is off and the value is kept.
3. Given responsetype.message is set, when a message post's folder name is
   built, then it uses that value.
4. Given download_sems is 0 or negative, when it is read, then the default
   (at least 1) is used.
5. Given list names or a dynamic-rule alias in config.json, when they are
   used, then list names match whatever their case, and every accepted alias
   picks the rule it names.
"""

from types import SimpleNamespace

import pytest
from diskcache import Disk

import ofscraper.utils.config.data as data
import ofscraper.utils.config.file as config_file
import ofscraper.utils.config.schema as schema


@pytest.fixture
def config(monkeypatch):
    cfg = {}
    monkeypatch.setattr(config_file, "open_config", lambda *a, **k: cfg)
    return cfg


# 1. incremental_downloads false


def test_incremental_downloads_false_is_kept():
    cfg = {"advanced_options": {"incremental_downloads": False}}
    assert data.get_incremental_downloads(config=cfg) is False
    new = schema.get_current_config_schema(cfg)
    assert new["advanced_options"]["incremental_downloads"] is False


def test_incremental_downloads_legacy_false_is_kept():
    cfg = {"advanced_options": {"enable_auto_after": False}}
    assert data.get_incremental_downloads(config=cfg) is False


# 2. cache-mode disabled / api_disabled


def test_cache_mode_api_disabled_is_kept_in_file():
    cfg = {"advanced_options": {"cache-mode": "api_disabled"}}
    assert data.cache_mode_helper(config=cfg) == "api_disabled"
    new = schema.get_current_config_schema(cfg)
    assert new["advanced_options"]["cache-mode"] == "api_disabled"
    # the general cache stays in sqlite storage, as before
    assert data.get_cache_mode(config=cfg) is Disk


def test_cache_mode_disabled_turns_cache_off(config):
    import ofscraper.utils.settings as settings

    config["advanced_options"] = {"cache-mode": "disabled"}
    merged = settings.merged_settings()
    assert merged.cached_disabled is True
    assert merged.api_cached_disabled is True
    assert merged.incremental_downloads is False


def test_cache_mode_api_disabled_turns_only_api_cache_off(config):
    import ofscraper.utils.settings as settings

    config["advanced_options"] = {"cache-mode": "api_disabled"}
    merged = settings.merged_settings()
    assert not merged.cached_disabled
    assert merged.api_cached_disabled is True


# 3. responsetype.message


def test_responsetype_message_renames_message_folder(config):
    import ofscraper.classes.of.posts as posts

    config["responsetype"] = {"message": "Chats"}
    post = posts.Post({"id": 1}, 1, "a_creator", responsetype="Messages")
    assert post.modified_responsetype == "Chats"


# 4. download_sems <= 0


@pytest.mark.parametrize("value", [0, -3, "0"])
def test_download_sems_below_one_falls_back_to_default(value):
    cfg = {"performance_options": {"download_sems": value}}
    sems = data.get_download_semaphores(config=cfg)
    assert sems == int(data.of_env.getattr("DOWNLOAD_SEM_DEFAULT"))
    assert sems >= 1


# 5. list name case and dynamic-rule aliases


def test_config_list_names_are_lowercased(config):
    import ofscraper.utils.settings as settings

    config["advanced_options"] = {
        "default_user_list": "Favourites, VIP",
        "default_black_list": ["Muted"],
    }
    merged = settings.merged_settings()
    assert merged.userlist == ["favourites", "vip"]
    assert merged.blacklist == ["muted"]
    # the file keeps what the user wrote
    new = schema.get_current_config_schema(config)
    assert new["advanced_options"]["default_user_list"] == "Favourites, VIP"


@pytest.mark.parametrize("alias", ["dv", "dev"])
def test_removed_deviint_aliases_are_rejected(alias):
    cfg = {"advanced_options": {"dynamic-mode-default": alias}}
    assert data.get_dynamic(config=cfg) == data.of_env.getattr("DYNAMIC_RULE_DEFAULT")


@pytest.mark.parametrize("alias", data.of_env.getattr("DYNAMIC_OPTIONS_ALL"))
def test_every_accepted_dynamic_alias_picks_its_rule(alias, monkeypatch):
    import ofscraper.utils.auth.request as request

    rules = {
        "datawhores": "datawhores",
        "xagler": "xagler",
        "rafa": "rafa",
        "digitalcriminals": "digitalcriminals",
        "dc": "digitalcriminals",
        "digital": "digitalcriminals",
        "digitals": "digitalcriminals",
        "manual": "manual",
        "generic": "generic",
    }
    assert alias in rules, f"{alias} is accepted but names no known rule"
    for name in {"datawhores", "xagler", "rafa", "digitalcriminals", "generic"}:
        monkeypatch.setattr(request, f"get_request_auth_{name}", lambda n=name: n)
    monkeypatch.setattr(request, "get_request_auth_dynamic_rule_manual", lambda: "manual")
    real = request.of_env.getattr
    monkeypatch.setattr(
        request.of_env,
        "getattr",
        lambda key, *a, **k: (
            "set"
            if key in {"DYNAMIC_RULE_MANUAL", "DYNAMIC_GENERIC_URL"}
            else real(key, *a, **k)
        ),
    )
    cfg = {"advanced_options": {"dynamic-mode-default": alias}}
    rule = data.get_dynamic(config=cfg)
    monkeypatch.setattr(
        request.settings, "get_settings", lambda: SimpleNamespace(dynamic_rules=rule)
    )
    monkeypatch.setattr(request.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(request, "curr_auth", None)
    monkeypatch.setattr(request, "last_check", None)
    assert request.get_request_auth() == rules[alias]

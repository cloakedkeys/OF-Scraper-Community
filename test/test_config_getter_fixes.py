"""Config getters on values a hand-edited config.json can hold.

auto_update_config rebuilds config.json from these getters and writes it back,
so a getter that raises stops the rewrite, and one that drops a saved value
changes the user's file.
"""

import pytest

import ofscraper.utils.config.data as data
import ofscraper.utils.config.schema as schema
import ofscraper.utils.of_env.of_env as of_env


@pytest.fixture(autouse=True)
def builtin_defaults():
    # the environment mustn't change the defaults these tests expect
    with of_env.builtin_defaults():
        yield


# --- sizes -----------------------------------------------------------------

SIZE_GETTERS = [
    (data.get_filesize_max, "content_filter_options", "file_size_max"),
    (data.get_filesize_min, "content_filter_options", "file_size_min"),
    (data.get_system_freesize, "download_options", "system_free_min"),
    (data.get_download_limit, "performance_options", "download_limit"),
]


@pytest.mark.parametrize("getter,section,key", SIZE_GETTERS)
@pytest.mark.parametrize("value", ["", "abc", "MB", "-1", True, ["1MB"]])
def test_unparsable_size_is_zero(getter, section, key, value):
    assert getter(config={section: {key: value}}) == 0


@pytest.mark.parametrize("getter,section,key", SIZE_GETTERS)
def test_parsable_size_is_still_parsed(getter, section, key):
    assert getter(config={section: {key: "2KB"}}) == 2000


def test_config_rewrite_survives_an_empty_size():
    new = schema.get_current_config_schema(
        {"content_filter_options": {"file_size_min": "", "file_size_max": "x"}}
    )
    assert new["content_filter_options"]["file_size_min"] == 0
    assert new["content_filter_options"]["file_size_max"] == 0


# --- saved falsy values ----------------------------------------------------


def test_top_level_false_is_kept_over_the_section():
    config = {"truncation_default": False, "file_options": {"truncation_default": True}}
    assert data.get_truncation(config=config) is False


def test_top_level_false_is_kept_when_nothing_else_is_set():
    assert data.get_truncation(config={"truncation_default": False}) is False


def test_empty_script_in_an_earlier_section_is_kept():
    # advanced_options.post_script is read before script_options.post_script
    config = {"advanced_options": {"post_script": ""}}
    assert data.get_post_script(config=config) == ""
    config["script_options"] = {"post_script": "later.sh"}
    assert data.get_post_script(config=config) == ""


def test_zero_size_in_an_earlier_section_is_kept():
    config = {
        "download_options": {"file_size_max": 0},
        "content_filter_options": {"file_size_max": "1MB"},
    }
    assert data.get_filesize_max(config=config) == 0


@pytest.mark.parametrize(
    "getter,key,section,falsy,truthy",
    [
        (data.get_min_length, "length_min", "content_filter_options", 0, 5),
        (data.get_max_post_count, "max_post_count", "download_options", 0, 9),
        (data.get_textlength, "textlength", "file_options", 0, 12),
        (data.get_block_ads, "block_ads", "content_filter_options", False, True),
        (data.get_hash, "remove_hash_match", "advanced_options", False, True),
        (
            data.get_InfiniteLoop,
            "infinite_loop_action_mode",
            "advanced_options",
            False,
            True,
        ),
        (data.get_filter, "filter", "download_options", [], ["images"]),
    ],
)
def test_top_level_falsy_value_is_kept(getter, key, section, falsy, truthy):
    assert getter(config={key: falsy, section: {key: truthy}}) == falsy


def test_top_level_auto_resume_false_is_kept():
    assert data.get_part_file_clean(config={"auto_resume": False}) is False


def test_top_level_empty_ffmpeg_and_temp_dir_are_kept():
    config = {"ffmpeg": "", "binary_options": {"ffmpeg": "/bin/ffmpeg"}}
    assert data.get_ffmpeg(config=config) == ""
    assert data.get_TempDir(config={"temp_dir": ""}) == ""


def test_the_first_set_path_still_wins_over_later_ones():
    config = {
        "advanced_options": {"post_script": "a.sh"},
        "script_options": {"post_script": "b.sh"},
    }
    assert data.get_post_script(config=config) == "a.sh"


@pytest.mark.parametrize(
    "getter,key,section,good",
    [
        (data.get_key_mode, "key-mode-default", "cdm_options", "manual"),
        (data.get_dynamic, "dynamic-mode-default", "advanced_options", "manual"),
        (data.get_textType, "text_type_default", "file_options", "word"),
        (data.cache_mode_helper, "cache-mode", "advanced_options", "json"),
        (data.get_download_semaphores, "download_sems", "performance_options", 4),
    ],
)
@pytest.mark.parametrize("falsy", ["", 0, False])
def test_falsy_value_doesnt_hide_a_later_one_where_none_is_valid(
    getter, key, section, good, falsy
):
    # these keys accept no falsy value, so one still counts as unset
    assert getter(config={key: falsy, section: {key: good}}) == good


def test_config_menu_answer_of_no_replaces_the_saved_yes():
    # the config menu puts its answers at the top level of the file it read
    # and rebuilds the config from that
    saved = {
        "file_options": {"truncation_default": True},
        "content_filter_options": {"block_ads": True},
        "script_options": {"post_script": "old.sh"},
    }
    answers = {"truncation_default": False, "block_ads": False, "post_script": ""}
    new = schema.get_current_config_schema({"config": {**saved, **answers}})
    assert new["file_options"]["truncation_default"] is False
    assert new["content_filter_options"]["block_ads"] is False
    assert new["script_options"]["post_script"] == ""


def test_written_config_keeps_a_top_level_false():
    new = schema.get_current_config_schema(
        {"truncation_default": False, "remove_hash_match": False}
    )
    assert new["file_options"]["truncation_default"] is False
    assert new["advanced_options"]["remove_hash_match"] is False

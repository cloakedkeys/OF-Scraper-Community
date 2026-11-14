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

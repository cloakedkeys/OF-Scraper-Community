import json
import os

import pytest

from ofscraper.utils.of_env.load import load_env_files

KEYS = (
    "OFSC_TEST_OBJECT",
    "OFSC_TEST_LIST",
    "OFSC_TEST_STRING",
    "OFSC_TEST_INT",
    "OFSC_TEST_FLOAT",
    "OFSC_TEST_BOOL",
    "OFSC_TEST_NULL",
)


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    # load_env_files also reads a .env from the working directory
    monkeypatch.chdir(tmp_path)
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


JSON_FILE = """{
  "OFSC_TEST_OBJECT": {"static_param": "abc", "checksum_indexes": [1, 2]},
  "OFSC_TEST_LIST": ["a", "b"],
  "OFSC_TEST_STRING": "plain",
  "OFSC_TEST_INT": 5,
  "OFSC_TEST_FLOAT": 1.5,
  "OFSC_TEST_BOOL": true,
  "OFSC_TEST_NULL": null
}
"""

YAML_FILE = """OFSC_TEST_OBJECT:
  static_param: abc
  checksum_indexes: [1, 2]
OFSC_TEST_LIST:
  - a
  - b
OFSC_TEST_STRING: plain
OFSC_TEST_INT: 5
OFSC_TEST_FLOAT: 1.5
OFSC_TEST_BOOL: true
OFSC_TEST_NULL: null
"""


@pytest.mark.parametrize("name,text", [("env.json", JSON_FILE), ("env.yaml", YAML_FILE)])
def test_objects_and_lists_are_stored_as_json(clean_env, name, text):
    path = clean_env / name
    path.write_text(text, encoding="utf-8")

    load_env_files([str(path)])

    assert json.loads(os.environ["OFSC_TEST_OBJECT"]) == {
        "static_param": "abc",
        "checksum_indexes": [1, 2],
    }
    assert json.loads(os.environ["OFSC_TEST_LIST"]) == ["a", "b"]


@pytest.mark.parametrize("name,text", [("env.json", JSON_FILE), ("env.yaml", YAML_FILE)])
def test_other_values_are_stored_as_before(clean_env, name, text):
    path = clean_env / name
    path.write_text(text, encoding="utf-8")

    load_env_files([str(path)])

    assert os.environ["OFSC_TEST_STRING"] == "plain"
    assert os.environ["OFSC_TEST_INT"] == "5"
    assert os.environ["OFSC_TEST_FLOAT"] == "1.5"
    assert os.environ["OFSC_TEST_BOOL"] == "True"
    assert os.environ["OFSC_TEST_NULL"] == "None"

"""file_options values from a hand-edited config.json are checked (#749).

text_type_default was matched case-sensitively and any other value became
letter without a word; a negative textlength dropped the last words; a
backslash in space_replacer crashed naming (it was a re.sub replacement)
and a slash made folders; a / or : from the date setting went into file
names; an unknown placeholder or broken template failed each file at
download time.
"""

import asyncio
import json
import logging
import pathlib
from types import SimpleNamespace

import pytest

import ofscraper.classes.of.posts as posts_
import ofscraper.classes.placeholder as placeholder
import ofscraper.main.open.load as load
import ofscraper.utils.config.config as config_
import ofscraper.utils.config.naming_check as naming_check

import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.paths.common as common_paths
import ofscraper.utils.settings as settings
from ofscraper.classes.of.base import base


class Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logged(monkeypatch):
    shared = logging.getLogger("shared")
    handler = Collect()
    monkeypatch.setattr(shared, "handlers", [handler])
    monkeypatch.setattr(shared, "level", logging.DEBUG)
    return handler.lines


@pytest.fixture
def config_json(monkeypatch, tmp_path):
    """Write file_options to a real config.json; settings are built from it."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(common_paths, "get_config_path", lambda: path)
    monkeypatch.setattr(config_, "config", None)  # read_config's cache
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    def write(cli=None, **file_options):
        """cli: command-line values that override file_options"""
        path.write_text(json.dumps({"file_options": file_options}), encoding="utf-8")
        args = settings.get_args(copy=True)
        args.text_type = args.space_replacer = args.textlength = None
        for key, value in (cli or {}).items():
            setattr(args, key, value)
        monkeypatch.setattr(settings, "get_args", lambda copy=False: args)
        monkeypatch.setattr(settings.read_args, "retriveArgs", lambda: args)
        with of_env.builtin_defaults():
            merged = settings.merged_settings()
        monkeypatch.setattr(settings, "get_settings", lambda: merged)
        return merged

    return write


def warnings(lines):
    return [line for line in lines if "file_options." in line]


# ---------------------------------------------------------------- text_type_default


@pytest.mark.parametrize("value,expected", [("Word", "word"), ("LETTER", "letter"), (" word ", "word")])
def test_text_type_is_matched_without_regard_to_case(config_json, logged, value, expected):
    assert config_json(text_type_default=value).text_type == expected
    load.checkFileOptions()
    assert warnings(logged) == []


def test_an_unknown_text_type_falls_back_to_letter(config_json):
    # the startup rewrite of config.json replaces it, and reports that
    assert config_json(text_type_default="words").text_type == "letter"


# ---------------------------------------------------------------- textlength


def test_a_negative_textlength_is_no_limit(config_json, logged):
    config_json(textlength=-2, text_type_default="word")
    assert base().text_trunicate("one two three four") == "one two three four"
    load.checkFileOptions()
    assert warnings(logged) == [
        "file_options.textlength is -2; a negative length is read as 0, no limit"
    ]


# ---------------------------------------------------------------- space_replacer


@pytest.mark.parametrize("value", ["\\", "\\1", "/", "a:b", "?"])
def test_a_space_replacer_that_cant_be_in_a_name_is_refused(config_json, logged, value):
    assert config_json(space_replacer=value).space_replacer == " "
    assert base().file_cleanup("good luck") == "good luck"
    load.checkFileOptions()
    assert warnings(logged) == [
        f"file_options.space_replacer is {json.dumps(value)}, which has characters"
        " a file name can't hold, so a space is used"
    ]


@pytest.mark.parametrize("value", [123, True, 1.5, ["_"], {"a": "_"}])
def test_a_space_replacer_that_isnt_text_is_refused(config_json, logged, value):
    # JSON that isn't a string can't replace a space: it crashed naming
    assert config_json(space_replacer=value).space_replacer == " "
    assert base().file_cleanup("good luck") == "good luck"
    load.checkFileOptions()
    assert warnings(logged) == [
        f"file_options.space_replacer is {json.dumps(value)}, which isn't text,"
        " so a space is used"
    ]


@pytest.mark.parametrize("value", ["_", "-", ".", "&", "__"])
def test_a_space_replacer_is_used_as_literal_text(config_json, logged, value):
    assert config_json(space_replacer=value).space_replacer == value
    assert base().file_cleanup("good luck") == f"good{value}luck"
    load.checkFileOptions()
    assert warnings(logged) == []


def test_a_space_replacer_with_spaces_is_put_in_once(config_json, names_env):
    # file_cleanup already puts it in: a second pass would add it again to
    # its own spaces, " - " becoming " - - - "
    config_json(space_replacer=" - ", file_format="{filename}.{ext}")
    item = evening_post(url="https://example.invalid/good luck.jpg").all_media[0]
    assert item.no_quality_final_filename == "good - luck"
    # an async cached property gives an awaitable, not a coroutine, and
    # asyncio.run() on Python 3.11 takes only a coroutine
    async def final_filename():
        return await item.final_filename

    assert asyncio.run(final_filename()) == "good - luck"
    assert asyncio.run(placeholder.Placeholders(item, "jpg").createfilename()) == "good - luck.jpg"


def test_a_profile_text_file_puts_in_a_space_replacer_once(config_json, names_env):
    config_json(space_replacer=" - ")
    post = evening_post(text="good luck")
    post._responsetype = "profile"
    assert asyncio.run(placeholder.Textholders(post, "txt").createfilename()) == "good - luck.txt"


def test_a_delete_character_is_not_put_in_a_name(config_json, logged):
    assert config_json(space_replacer="\x7f").space_replacer == " "
    assert naming_check.name_safe("a\x7fb") == "ab"


# ---------------------------------------------------------------- command line


def test_a_bad_space_replacer_on_the_command_line_names_the_option(config_json, logged):
    assert config_json(cli={"space_replacer": "a/b"}, space_replacer="_").space_replacer == " "
    load.checkFileOptions()
    assert [line for line in logged if "--space-replacer" in line] == [
        '--space-replacer is "a/b", which has characters a file name can\'t hold,'
        " so a space is used"
    ]


def test_a_good_space_replacer_on_the_command_line_wins(config_json, logged):
    assert config_json(cli={"space_replacer": "_"}, space_replacer="?").space_replacer == "_"
    load.checkFileOptions()
    assert logged == []


def test_a_negative_textlength_on_the_command_line_names_the_option(config_json, logged):
    config_json(cli={"textlength": -1}, textlength=5)
    load.checkFileOptions()
    assert logged == ["--textlength is -1; a negative length is read as 0, no limit"]


def test_a_text_type_on_the_command_line_wins(config_json):
    assert config_json(cli={"text_type": "word"}, text_type_default="letter").text_type == "word"


# ---------------------------------------------------------------- {date}


async def _none():
    return None


@pytest.fixture
def names_env(monkeypatch, tmp_path):
    monkeypatch.setattr(placeholder.log, "trace", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(placeholder.me, "parse_user", lambda: ("1", "me"))
    monkeypatch.setattr(placeholder.me, "get_id", lambda: 1)
    monkeypatch.setattr(placeholder.profile_data, "get_active_profile", lambda: "main_profile")
    monkeypatch.setattr(placeholder.common_paths, "get_config_home", lambda: tmp_path / "cfg")
    monkeypatch.setattr(
        placeholder.common_paths, "get_save_location", lambda *a, **k: str(tmp_path / "data")
    )
    monkeypatch.setattr(
        placeholder.manager,
        "Manager",
        SimpleNamespace(current_model_manager=SimpleNamespace(get_model=lambda _u: None)),
    )
    monkeypatch.setattr(placeholder, "naming_script", lambda *a, **k: _none())
    return tmp_path


def evening_post(url="https://example.invalid/a.jpg", text="hello"):
    raw = {
        "id": 2000,
        "postedAt": "2024-05-01T20:00:00+00:00",
        "text": text,
        "price": 0,
        "isOpened": True,
        "responseType": "post",
        "media": [
            {
                "id": 3000,
                "type": "photo",
                "canView": True,
                "files": {"full": {"url": url}},
            }
        ],
    }
    return posts_.Post(raw, 1000, "a_creator", "timeline")


@pytest.mark.parametrize(
    "date,folder,file_name",
    [
        ("YYYY/MM/DD", pathlib.Path("2024", "05", "01"), "20240501.jpg"),
        # ":" can't be in a folder name on Windows either
        ("YYYY-MM-DD HH:mm:ss", pathlib.Path("2024-05-01 200000"), "2024-05-01 200000.jpg"),
        ('YYYY/MM/DD "HH|mm?ss*"', pathlib.Path("2024", "05", "01 200000"), "20240501 200000.jpg"),
        ("MM-DD-YYYY", pathlib.Path("05-01-2024"), "05-01-2024.jpg"),
    ],
)
@pytest.mark.parametrize("cls", [placeholder.Placeholders, placeholder.Textholders])
def test_date_in_a_file_name_loses_what_a_name_cant_hold(
    config_json, names_env, monkeypatch, cls, date, folder, file_name
):
    config_json(date=date, dir_format="{date}", file_format="{date}.{ext}")
    item = evening_post()
    item = item.all_media[0] if cls is placeholder.Placeholders else item
    names = cls(item, "jpg")
    made = pathlib.Path(asyncio.run(names.getmediadir(create=False)))
    assert made.relative_to(names_env / "data") == folder
    assert asyncio.run(names.createfilename()) == file_name


# ---------------------------------------------------------------- templates


@pytest.mark.parametrize(
    "dir_format,file_format",
    [
        ("{model_username}/{responsetype}/{mediatype}/", "{filename}.{ext}"),
        ("{sitename}/{first_letter}/{username}/{year}/{month}", "{date} {day} {text}.{ext}"),
        ("{modelObj.name}/{config[file_options][date]}", "{post_id}_{media_id}{{x}}.{ext}"),
        ("{user_name}/{label}/{value}/{quality}", "{only_filename}-{current_price}.{ext}"),
    ],
)
def test_known_placeholders_are_not_reported(config_json, logged, dir_format, file_format):
    config_json(dir_format=dir_format, file_format=file_format)
    load.checkFileOptions()
    assert warnings(logged) == []


def test_an_unknown_placeholder_is_reported_once_naming_the_setting(config_json, logged):
    config_json(dir_format="{model_username}/{foo}", file_format="{bar}_{post_id}.{ext}")
    load.checkFileOptions()
    assert warnings(logged) == [
        'file_options.dir_format "{model_username}/{foo}" has an unknown placeholder:'
        " {foo}. Each file will fail to download until it's fixed",
        'file_options.file_format "{bar}_{post_id}.{ext}" has an unknown placeholder:'
        " {bar}. Each file will fail to download until it's fixed",
    ]


def test_two_unknown_placeholders_are_reported_together(config_json, logged):
    config_json(file_format="{foo}_{bar}.{ext}")
    load.checkFileOptions()
    assert warnings(logged) == [
        'file_options.file_format "{foo}_{bar}.{ext}" has unknown placeholders:'
        " {foo}, {bar}. Each file will fail to download until it's fixed"
    ]


def test_ext_is_only_known_in_file_names(config_json, logged):
    config_json(dir_format="{model_username}/{ext}")
    load.checkFileOptions()
    assert [w.split(" has ")[1] for w in warnings(logged)] == [
        "an unknown placeholder: {ext}. Each file will fail to download until it's fixed"
    ]


@pytest.mark.parametrize("template", ["{date} {text", "{date}} {text}", "{} {text}"])
def test_a_broken_template_is_reported(config_json, logged, template):
    config_json(file_format=template)
    load.checkFileOptions()
    (line,) = warnings(logged)
    assert line.startswith(f"file_options.file_format {json.dumps(template)} isn't a valid template")


def test_known_placeholders_match_what_names_are_built_from():
    """The check's list follows the placeholders: a new one must be added."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_reference",
        pathlib.Path(__file__).parents[1] / "scripts" / "reference" / "build_reference.py",
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    rows = build.placeholder_table()
    for kind, known in (("dir", naming_check.DIR_PLACEHOLDERS), ("file", naming_check.FILE_PLACEHOLDERS)):
        built = {k for k, kinds in rows.items() if kind in kinds}
        built |= {k.replace("_", "") for k in built}
        assert known == built, kind

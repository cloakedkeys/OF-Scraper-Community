"""A post's text file name doesn't repeat the caption (#736)."""

import asyncio

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.classes.of.posts as posts_
import ofscraper.classes.placeholder as placeholder
import ofscraper.utils.settings as settings
from ofscraper.utils.args.main import AutoDotDict
from ofscraper.utils.args.parse.commands.main import program
from test_undated_posts import names_env  # noqa: F401  (fixture)

CAPTION = "Look at this dildo disappear inside of me"


@pytest.fixture
def text_settings(monkeypatch, names_env):  # noqa: F811
    """Real settings with --text-type word, cut to 4 words as textlength 4 would."""
    params, _ = program.main(
        args=["--text-type", "word"], standalone_mode=False, prog_name="OF-Scraper"
    )
    args = AutoDotDict(params)
    args.command = "OF-Scraper"
    monkeypatch.setattr(settings.read_args, "retriveArgs", lambda *a, **k: args)
    merged = settings.merged_settings()
    merged.text_length = 4
    monkeypatch.setattr(settings, "get_settings", lambda *a, **k: merged)
    return merged


def text_name(monkeypatch, file_format):
    monkeypatch.setattr(placeholder.data, "get_fileformat", lambda *a, **k: file_format)
    raw = {"id": 8, "text": CAPTION, "price": 0, "isOpened": True,
           "responseType": "post", "postedAt": "2026-10-31T12:00:00+00:00"}
    holder = placeholder.Textholders(posts_.Post(raw, 1, "u", "timeline"), "txt")
    return asyncio.run(holder.createfilename())


@pytest.mark.parametrize("name", ["filename", "file_name", "only_filename",
                                  "only_file_name", "original_filename"])
def test_text_and_filename_together_name_the_caption_once(monkeypatch, text_settings, name):
    out = text_name(monkeypatch, "{date} {%s} {text}.{ext}" % name)
    # {filename} is the post id, as a media file's is its media id
    assert out == "2026-10-31 8 Look at this dildo.txt"


def test_filename_alone_is_still_the_caption(monkeypatch, text_settings):
    assert text_name(monkeypatch, "{date} {filename}.{ext}") == "2026-10-31 Look at this dildo.txt"


def test_text_alone_is_unchanged(monkeypatch, text_settings):
    assert text_name(monkeypatch, "{date} {text}.{ext}") == "2026-10-31 Look at this dildo.txt"

"""Filename extensions, placeholders, and text-file output."""

# ----------------------------------------------------------------------
# From test_extension_from_content_type.py
# A saved file's extension comes from the reply's Content-Type. The subtype
# is usually the extension, but not always: a QuickTime video was saved as
# ".quicktime", which players don't recognise.
# ----------------------------------------------------------------------

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
from ofscraper.commands.scraper.actions.utils.general import extension_for


@pytest.mark.parametrize(
    "content_type, ext",
    [
        ("video/mp4", "mp4"),
        ("image/jpeg", "jpeg"),  # unchanged: existing image names keep .jpeg
        ("audio/mp4", "mp4"),
        ("video/quicktime", "mov"),
        ("video/x-ms-wmv", "wmv"),
        ("video/x-msvideo", "avi"),
        ("video/x-matroska", "mkv"),
        ("video/mp2t", "ts"),
        ("Video/QuickTime", "mov"),
        ("video/mp4; charset=binary", "mp4"),
        ("wmv", "wmv"),  # a bare value some CDNs echo back from the link
    ],
)
def test_extension_for(content_type, ext):
    assert extension_for(content_type, "fallback") == ext


@pytest.mark.parametrize(
    "content_type", [None, "", "application/octet-stream", "binary/octet-stream"]
)
def test_missing_or_generic_type_uses_the_fallback(content_type):
    assert extension_for(content_type, "mp4") == "mp4"

# ----------------------------------------------------------------------
# From test_my_id_placeholder.py
# {my_id} holds the account's numeric id, not its display name (#206).
# ----------------------------------------------------------------------

from types import SimpleNamespace

import pytest

import ofscraper.classes.placeholder as placeholder


@pytest.fixture
def base(monkeypatch):
    monkeypatch.setattr(placeholder.profile_data, "get_my_info", lambda: {"id": 12345, "name": "Display Name", "username": "me_user"})
    monkeypatch.setattr(placeholder.profile_data, "get_active_profile", lambda: "main_profile")
    monkeypatch.setattr(placeholder.common_paths, "get_save_location", lambda *a, **k: "/data")
    monkeypatch.setattr(placeholder.common_paths, "get_config_home", lambda: "/cfg")
    monkeypatch.setattr(placeholder, "site_save_location", lambda: "/data")


def _variables(monkeypatch, anon):
    fake = SimpleNamespace(anon=anon)
    monkeypatch.setattr(placeholder.settings, "get_settings", lambda: fake)
    monkeypatch.setattr(placeholder.me.settings, "get_settings", lambda: fake)
    p = placeholder.basePlaceholder()
    p.create_variables_base()
    return p._variables


def test_my_id_is_the_account_id(base, monkeypatch):
    v = _variables(monkeypatch, anon=False)
    assert v["my_id"] == 12345
    assert v["my_username"] == "me_user"


def test_my_id_in_anon_mode(base, monkeypatch):
    v = _variables(monkeypatch, anon=True)
    assert v["my_id"] == "anon"
    assert v["my_username"] == "anon"

# ----------------------------------------------------------------------
# From test_paid_placeholder_models.py
# ----------------------------------------------------------------------

import ofscraper.managers.model as model_manager
import ofscraper.utils.of_env.of_env as of_env
from ofscraper.classes.of.models import Model


def _placeholder():
    return f"{of_env.getattr('DELETED_MODEL_PLACEHOLDER')}_123"


def test_placeholder_models_are_left_out_of_the_scrape_selection(monkeypatch):
    for module, name in [
        (model_manager.subtype, "subType"),
        (model_manager.price, "pricePaidFreeFilterHelper"),
        (model_manager.flags, "promoFilterHelper"),
        (model_manager.date_, "dateFilters"),
        (model_manager.other, "otherFilters"),
    ]:
        monkeypatch.setattr(module, name, lambda models: models)
    manager = model_manager.ModelManager()
    # what --scrape-paid adds for a deleted account, beside a real subscription
    manager._update_all_subs(
        [
            Model({"username": "model", "id": 1, "avatar": None}),
            Model({"username": _placeholder(), "id": _placeholder()}),
        ]
    )

    assert [m.name for m in manager._apply_filters()] == ["model"]
    assert manager.get_model(_placeholder()) is not None  # still there for the paid pass


def test_placeholder_model_has_no_avatar_or_header():
    placeholder = Model({"username": _placeholder(), "id": _placeholder()})
    assert placeholder.avatar is None and placeholder.header is None

# ----------------------------------------------------------------------
# From test_text_file.py
# ----------------------------------------------------------------------

import asyncio
from types import SimpleNamespace

import aiofiles

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.utils.text as text


class _Post:
    def __init__(self, caption):
        self.text = caption
        self.downloaded = None

    def mark_text_download_attempt(self):
        pass

    def mark_text_downloaded(self, ok):
        self.downloaded = ok


def _write(monkeypatch, tmp_path, caption):
    path = tmp_path / "caption.txt"

    class _Holder:
        def __init__(self, ele, ext):
            self.filepath = path

        async def init(self):
            return self

    async def _no_script(path):
        pass

    real_open = aiofiles.open

    def cp1252_default_open(*args, encoding=None, **kwargs):
        # what open() does on Windows before Python 3.15
        return real_open(*args, encoding=encoding or "cp1252", **kwargs)

    monkeypatch.setattr(text.placeholder, "Textholders", _Holder)
    monkeypatch.setattr(text, "after_download_script", _no_script)
    monkeypatch.setattr(text.aiofiles, "open", cp1252_default_open)
    monkeypatch.setattr(
        text.settings,
        "get_settings",
        lambda: SimpleNamespace(force_all=False, force_model_unique=False),
    )
    post = _Post(caption)
    asyncio.run(text.get_text_process("model", post))
    return post, path


def test_caption_with_emoji_is_written(monkeypatch, tmp_path):
    post, path = _write(monkeypatch, tmp_path, "new set out now \U0001f525✨")
    assert post.downloaded is True
    assert path.read_text(encoding="utf-8") == "new set out now \U0001f525✨"


def test_wrapped_caption_keeps_line_breaks(monkeypatch, tmp_path):
    caption = " ".join(["word"] * 30)  # 149 characters, wraps at 70
    post, path = _write(monkeypatch, tmp_path, caption)
    written = path.read_text(encoding="utf-8")
    assert written.replace("\n", " ") == caption
    assert "wordword" not in written

# ----------------------------------------------------------------------
# File names built from a post: real settings from command-line options,
# a real post, and the real placeholder classes.
# ----------------------------------------------------------------------

import ntpath
import re

import ofscraper.classes.of.posts as posts_
import ofscraper.utils.settings as settings
from ofscraper.utils.args.main import AutoDotDict
from ofscraper.utils.args.parse.commands.main import program
from test_undated_posts import names_env  # noqa: F401  (fixture)


def _post(caption, media=1, post_id=8):
    raw = {"id": post_id, "text": caption, "price": 0, "isOpened": True,
           "responseType": "post", "postedAt": "2026-10-31T12:00:00+00:00",
           "media": [{"id": 70 + n, "type": "photo", "canView": True,
                      "files": {"full": {"url": f"https://example.invalid/pic{n}.jpg"}}}
                     for n in range(media)]}
    return posts_.Post(raw, 1, "u", "timeline")


@pytest.fixture
def naming(monkeypatch, names_env):  # noqa: F811
    """Returns names(file_format, caption, *options, media=1): the text file's
    name and each media file's name for one post with that caption."""

    def names(file_format, caption, *options, media=1):
        params, _ = program.main(
            args=list(options), standalone_mode=False, prog_name="OF-Scraper"
        )
        args = AutoDotDict(params)
        args.command = "OF-Scraper"
        monkeypatch.setattr(settings.read_args, "retriveArgs", lambda *a, **k: args)
        merged = settings.merged_settings()
        monkeypatch.setattr(settings, "get_settings", lambda *a, **k: merged)
        monkeypatch.setattr(
            placeholder.data, "get_fileformat", lambda *a, **k: file_format
        )
        post = _post(caption, media)
        text = asyncio.run(placeholder.Textholders(post, "txt").createfilename())
        return text, [
            asyncio.run(placeholder.Placeholders(item, "jpg").createfilename())
            for item in post.all_media
        ]

    return names


# A caption can't change the folder a file is saved in (#739). A backslash is
# a folder separator on Windows, and a tab or a carriage return can't be in a
# Windows file name.

FOLDER = r"C:\data\a_creator\Posts"


@pytest.mark.parametrize(
    "caption",
    [r"me \ you", r"up\..\..\..\and out", "a tab\there", "a return\r\nhere"],
)
def test_a_caption_cannot_name_another_folder(naming, caption):
    text, (media,) = naming("{date} {filename} {text}.{ext}", caption)
    for name in (text, media):
        assert not re.search(r"[\\\x00-\x1f]", name)
        # as Windows reads it: still a file in the folder it was meant for
        assert ntpath.dirname(ntpath.normpath(ntpath.join(FOLDER, name))) == FOLDER


def test_a_text_file_named_by_its_caption_stays_in_its_folder(naming):
    # the default file_format: a text file's {filename} is its caption
    text, _ = naming("{filename}.{ext}", r"..\..\..\elsewhere")
    assert ntpath.dirname(ntpath.normpath(ntpath.join(FOLDER, text))) == FOLDER


# A text file is named with the same file_format as media, so it has every
# placeholder a media file has (#742).


def test_a_text_file_has_the_media_only_placeholders(naming):
    text, (media,) = naming(
        "{media_id} {mediaid} {download_type} {downloadtype} {text}.{ext}", "hello"
    )
    assert media == "70 70 Normal Normal hello.jpg"
    # no media: the post's id stands in for the media id
    assert text == "8 8 Normal Normal hello.txt"


def test_a_text_file_has_every_placeholder_a_media_file_has(naming, monkeypatch):
    seen = {}
    for cls in (placeholder.Placeholders, placeholder.Textholders):
        real = cls.createfilename

        async def recorded(self, real=real, cls=cls):
            out = await real(self)
            seen[cls.__name__] = set(self._variables)
            return out

        monkeypatch.setattr(cls, "createfilename", recorded)
    naming("{filename}.{ext}", "hello")
    assert seen["Placeholders"] - seen["Textholders"] == set()


# A post's media are told apart by a count before the extension when the
# file_format has nothing unique per media (#743).


def test_the_count_goes_before_the_extension_only(naming, monkeypatch):
    monkeypatch.setattr(placeholder.data, "get_date", lambda *a, **k: "DD.MM.YYYY")
    _, media = naming(
        "{date} {text}.{ext}", "Hello there. How are you... fine", media=2
    )
    assert media == [
        "31.10.2026 Hello there. How are you... fine_1.jpg",
        "31.10.2026 Hello there. How are you... fine_2.jpg",
    ]


def test_the_count_ends_a_name_with_no_extension(naming):
    _, media = naming("{post_id}", "hello", media=2)
    assert media == ["8_1", "8_2"]


@pytest.mark.parametrize(
    "name",
    ["filename", "file_name", "only_filename", "only_file_name", "onlyfilename",
     "original_filename", "originalfilename", "media_id", "mediaid"],
)
def test_no_count_when_the_name_is_already_unique(naming, name):
    # every spelling of a placeholder that differs per media, not only some
    _, media = naming("{%s}.{ext}" % name, "hello", media=2)
    assert not any(re.search(r"_[12]\.jpg$", item) for item in media), media
    assert len(set(media)) == 2


# A post's media and its text file get the same {text} (#744).


@pytest.mark.parametrize(
    "replacer, expected",
    [("-", "one-two-three-four"), ("_", "one_two_three_four"),
     (".", "one.two.three.four"), (" ", "one two three four")],
)
def test_media_and_text_files_cut_the_caption_alike(naming, replacer, expected):
    text, (media,) = naming(
        "{text}.{ext}", "one two three four five six",
        "--text-type", "word", "--textlength", "4", "--space-replacer", replacer,
    )
    assert (text, media) == (f"{expected}.txt", f"{expected}.jpg")


# --original and truncation_default: false leave a long path as the template
# gives it (#740).

LONG_CAPTION = " ".join(["word"] * 120)  # 599 characters: over every system's limit


def _paths(naming, *options):
    """The path of a long-captioned post's media file and text file."""
    naming("{text}.{ext}", LONG_CAPTION, *options)  # applies the settings
    post = _post(LONG_CAPTION)
    return [
        asyncio.run(holder.init(create=False)).filepath
        for holder in (
            placeholder.Placeholders(post.all_media[0], "jpg"),
            placeholder.Textholders(post, "txt"),
        )
    ]


def test_original_leaves_a_long_name_whole(naming):
    media, text = _paths(naming, "--original")
    assert (media.name, text.name) == (f"{LONG_CAPTION}.jpg", f"{LONG_CAPTION}.txt")


def test_a_long_name_is_shortened_by_default(naming):
    media, text = _paths(naming)
    assert len(media.name) < len(LONG_CAPTION) and media.suffix == ".jpg"
    assert len(text.name) < len(LONG_CAPTION) and text.suffix == ".txt"


# Two posts whose text files get the same name both keep their text (#746).


def _text_files(tmp_path):
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in (tmp_path / "data").rglob("*.txt")
    }


def _write_texts(naming, monkeypatch, captions, *options, collision="media_id"):
    async def no_script(path):
        pass

    naming("{filename}.{ext}", "x", "--text-type", "word", "--textlength", "2", *options)
    monkeypatch.setattr(text, "after_download_script", no_script)
    monkeypatch.setattr(
        text.config_data, "get_filename_collision", lambda *a, **k: collision
    )
    posts = [_post(caption, 0, post_id=n) for n, caption in enumerate(captions, 8)]
    asyncio.run(text.get_text("u", posts))


CAPTIONS = ["Good morning everyone", "Good morning to my fans"]


def test_a_taken_text_file_name_gets_the_post_id(naming, monkeypatch, tmp_path):
    _write_texts(naming, monkeypatch, CAPTIONS)
    both = {"Good morning.txt": CAPTIONS[0], "Good morning_9.txt": CAPTIONS[1]}
    assert _text_files(tmp_path) == both
    # a second run finds each post's own file, and writes nothing new
    _write_texts(naming, monkeypatch, CAPTIONS)
    assert _text_files(tmp_path) == both
    # --force-all rewrites each post's own file, not the other post's
    _write_texts(naming, monkeypatch, CAPTIONS, "--force-all")
    assert _text_files(tmp_path) == both


def test_the_same_caption_twice_is_one_text_file(naming, monkeypatch, tmp_path):
    _write_texts(naming, monkeypatch, ["Good morning", "Good morning"])
    assert _text_files(tmp_path) == {"Good morning.txt": "Good morning"}


def test_overwrite_mode_keeps_the_first_text_file(naming, monkeypatch, tmp_path):
    _write_texts(naming, monkeypatch, CAPTIONS, collision="overwrite")
    assert list(_text_files(tmp_path)) == ["Good morning.txt"]


# A caption in a file name reads as it does on the site (#748): a line break
# or a <br> separates words, and HTML entities are decoded, as label names
# are (#523).


@pytest.mark.parametrize(
    "caption, expected",
    [
        ("first line\nsecond line\n\n#tag1 #tag2", "first line second line #tag1 #tag2"),
        ("<p>first line<br />second line</p><p>third</p>", "first line second line third"),
        ("a tab\there, a return\r\nthere", "a tab here, a return there"),
        ("Tom &amp; Jerry don&#039;t", "Tom & Jerry don't"),
        ("non&nbsp;breaking space", "non breaking space"),
        # decoded, then what a name can't hold is removed, as for {label}
        ("Q&amp;A &lt;3 &quot;best&quot;", "Q&A 3 best"),
        ("<b>bold</b> and <i>italic</i>", "bold and italic"),
    ],
)
def test_a_caption_reads_in_a_name_as_on_the_site(naming, caption, expected):
    text, (media,) = naming("{text}.{ext}", caption)
    assert (text, media) == (f"{expected}.txt", f"{expected}.jpg")


def test_words_on_separate_lines_count_as_separate_words(naming):
    text, (media,) = naming(
        "{text}.{ext}", "one\ntwo three", "--text-type", "word", "--textlength", "2"
    )
    assert (text, media) == ("one two.txt", "one two.jpg")


def test_a_caption_with_nothing_for_a_name_is_the_post_id(naming):
    text, (media,) = naming("{text}.{ext}", "???")
    assert (text, media) == ("8.txt", "8.jpg")
    # a text file's {filename} is its caption too
    assert naming("{filename}.{ext}", "???")[0] == "8.txt"


# A text file reads as the caption does on the site (#751): no HTML, and the
# caption's own lines.


def _write_text(naming, monkeypatch, caption, *options, collision="media_id", before=None):
    """Write one post's text file (named caption.txt). `before` is what a
    file already at that name holds."""

    async def no_script(path):
        pass

    naming("caption.{ext}", "x", *options)
    monkeypatch.setattr(text, "after_download_script", no_script)
    monkeypatch.setattr(
        text.config_data, "get_filename_collision", lambda *a, **k: collision
    )
    post = _post(caption, 0)
    if before is not None:
        folder = asyncio.run(placeholder.Textholders(post, "txt").getmediadir())
        (folder / "caption.txt").write_text(before, encoding="utf-8")
    asyncio.run(text.get_text("u", [post]))


@pytest.mark.parametrize(
    "caption, written",
    [
        ("<p>Line one<br />Line two</p><p>Tom &amp; Jerry</p>",
         "Line one\nLine two\nTom & Jerry"),
        ("New set out now\n\n#tag1 #tag2", "New set out now\n\n#tag1 #tag2"),
        # each of the caption's lines is wrapped on its own
        (" ".join(["word"] * 16) + "\nlast line",
         " ".join(["word"] * 14) + "\nword word\nlast line"),
    ],
)
def test_a_text_file_reads_as_the_caption_does(
    naming, monkeypatch, tmp_path, caption, written
):
    _write_text(naming, monkeypatch, caption)
    assert _text_files(tmp_path) == {"caption.txt": written}


def test_a_fansly_caption_is_plain_text_already(naming, monkeypatch, tmp_path):
    # nothing to decode or remove: "<3 ... >" isn't a tag there
    caption = "I <3 you > me &amp; more"
    _write_text(naming, monkeypatch, caption, "--site", "fansly")
    assert list(_text_files(tmp_path).values()) == [caption]


OLD_FORM = "<p>Line one<br />Line two</p> <p>Tom &amp; Jerry</p>"


@pytest.mark.parametrize("collision", ["media_id", "overwrite"])
def test_an_older_version_s_text_file_is_corrected_in_place(
    naming, monkeypatch, tmp_path, collision
):
    # written by a version that kept the HTML: the same post's file, so it's
    # rewritten where it is, not left beside a caption_<post_id>.txt
    _write_text(
        naming, monkeypatch, "<p>Line one<br />Line two</p>\n<p>Tom &amp; Jerry</p>",
        collision=collision, before=OLD_FORM,
    )
    assert _text_files(tmp_path) == {"caption.txt": "Line one\nLine two\n\nTom & Jerry"}

"""Long paths are cut to fit: Windows' 259-character MAX_PATH, Linux's file-name limit."""

import pathlib

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.utils.paths.paths as paths

# a folder like a real Fansly one, under a Windows home
FOLDER = r"C:\Users\someone\Data\ofscraper\OnlyFans\Fansly\a_creator\Posts\2026-10-31"
CAPTION = "Look at this sunset fading behind the bay! 💀🪄 #fyp #beach #ocean #sky #sunsets " * 3


@pytest.fixture(autouse=True)
def windows_sizes(monkeypatch):
    # the defaults, measured as on Windows whatever runs the test
    monkeypatch.delenv("UTF", raising=False)


def cut(path):
    """The Windows helper's result, read as Windows reads it: CI runs on
    Linux, where a backslash isn't a folder separator."""
    return pathlib.PureWindowsPath(str(paths._windows_truncateHelper(path)))


def windows_chars(path):
    """MAX_PATH counts UTF-16 code units: an emoji takes two."""
    return len(str(path).encode("utf-16-le")) // 2


@pytest.mark.parametrize("ext", [".mp4", ".txt", "_1.jpg"])
def test_a_long_windows_path_fits_in_max_path(ext):
    out = cut(f"{FOLDER}\\2026-10-31 {CAPTION}{ext}")
    assert windows_chars(out) <= 259
    assert out.name.endswith(ext) and str(out.parent) == FOLDER


def test_the_cut_keeps_as_much_of_the_name_as_fits():
    out = cut(f"{FOLDER}\\{'a' * 300}.txt")
    # 259 characters exactly: one more letter would not fit
    assert windows_chars(out) == 259


def test_a_path_that_fits_is_left_alone():
    path = f"{FOLDER}\\2026-10-31 short.mp4"
    assert str(cut(path)) == path


@pytest.mark.parametrize("name, kept", [
    # the extension is removed from the end only, not wherever ".mp4" or
    # ".txt" read as a pattern matches ("-mp4", "ytxt")
    ("clip-mp4 " + "b" * 300 + ".mp4", "clip-mp4 "),
    ("wordytxt " + "b" * 300 + ".txt", "wordytxt "),
])
def test_only_the_real_extension_is_removed(name, kept):
    out = cut(f"{FOLDER}\\{name}")
    assert out.name.startswith(kept)


def test_a_folder_too_long_for_any_name_says_so():
    with pytest.raises(ValueError, match="folder"):
        cut(f"C:\\{'d' * 300}\\name.txt")


def test_a_long_linux_name_fits_in_the_name_limit():
    out = paths._linux_truncateHelper(f"/data/{'a' * 400}.txt")
    size = paths.get_string_byte_size_unix
    # measured as the app measures it; as much as fits, one more letter would not
    assert size(out.name) <= 254 < size("a" + out.name)


# ---------------------------------------------------------------- the caption is cut, not the end (#741)


@pytest.fixture
def name_env(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    import ofscraper.classes.placeholder as placeholder

    async def no_script(*a, **k):
        return None

    monkeypatch.setattr(placeholder.log, "trace", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(placeholder.me, "parse_user", lambda: ("1", "me"))
    monkeypatch.setattr(placeholder.me, "get_id", lambda: 1)
    monkeypatch.setattr(placeholder.profile_data, "get_active_profile", lambda: "main_profile")
    monkeypatch.setattr(placeholder.common_paths, "get_config_home", lambda: tmp_path / "cfg")
    monkeypatch.setattr(
        placeholder.common_paths, "get_save_location", lambda *a, **k: str(tmp_path / "data")
    )
    monkeypatch.setattr(placeholder.config_file, "open_config", lambda: {})
    monkeypatch.setattr(placeholder.settings, "get_args", lambda: SimpleNamespace())
    monkeypatch.setattr(
        placeholder.manager,
        "Manager",
        SimpleNamespace(current_model_manager=SimpleNamespace(get_model=lambda _u: None)),
    )
    monkeypatch.setattr(placeholder, "naming_script", no_script)
    monkeypatch.setattr(placeholder.data, "get_dirformat", lambda *a, **k: "{date}")
    monkeypatch.setattr(placeholder.data, "get_date", lambda *a, **k: "YYYY-MM-DD")
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")

    def names(file_format, text):
        import ofscraper.classes.of.posts as posts_

        monkeypatch.setattr(placeholder.data, "get_fileformat", lambda *a, **k: file_format)
        raw = {
            "id": 7, "text": text, "price": 0, "isOpened": True, "responseType": "post",
            "postedAt": "2024-05-01T20:00:00+00:00",
            "media": [
                {"id": 70 + i, "type": "photo", "canView": True,
                 "files": {"full": {"url": f"https://example.invalid/photo{i}.jpg"}}}
                for i in range(3)
            ],
        }
        items = posts_.Post(raw, 1, "u", "timeline").all_media
        return [
            asyncio.run(placeholder.Placeholders(item, "jpg").init(create=False)).filepath
            for item in items
        ]

    return names


@pytest.mark.parametrize("file_format, unique", [
    ("{date} {text} {filename}.{ext}", "photo{}"),
    ("{date} {text} {media_id}.{ext}", "7{}"),
])
def test_a_long_caption_is_cut_and_the_unique_part_kept(name_env, file_format, unique):
    out = name_env(file_format, "a long caption " * 40)
    for i, path in enumerate(out):
        assert path.name.endswith(f" {unique.format(i)}.jpg")
        assert path.name.startswith("2024-05-01 a long caption")
        assert paths.get_string_byte_size_unix(path.name) <= 254


def test_a_short_caption_is_left_alone(name_env):
    out = name_env("{date} {text} {filename}.{ext}", "short")
    assert [p.name for p in out] == [f"2024-05-01 short photo{i}.jpg" for i in range(3)]


@pytest.mark.parametrize("name", ["💀" * 200 + ".txt", "💀" * 200])
def test_a_folder_that_leaves_no_room_for_a_character_says_so(name):
    # room for 2 bytes after the folder: not one emoji (4), so nothing of the
    # name would be left, giving ".txt", or with no extension the folder itself
    folder = "C:\\" + "d" * (250 if name.endswith(".txt") else 254)
    with pytest.raises(ValueError, match="folder"):
        cut(f"{folder}\\{name}")


@pytest.mark.parametrize("end", [" ", ". ", " . "])
def test_a_cut_without_an_extension_drops_trailing_spaces_and_dots(end):
    # Windows silently drops them from a name; the cut must not end on one
    room = 259 - len(FOLDER) - 1
    kept = "a" * (room - len(end))
    out = cut(f"{FOLDER}\\{kept}{end}{'b' * 300}")
    assert out.name == kept and str(out.parent) == FOLDER


def test_a_cut_that_is_only_spaces_and_dots_says_so():
    room = 259 - len(FOLDER) - 1
    with pytest.raises(ValueError, match="folder"):
        cut(f"{FOLDER}\\{'. ' * room}{'b' * 300}")


def test_a_linux_name_with_no_room_for_a_character_says_so(monkeypatch):
    # room for 2 bytes beside ".txt": not one emoji (4)
    monkeypatch.setenv("LINUX_MAX_FILE_NAME_BYTES", "10")
    with pytest.raises(ValueError, match="no room"):
        paths._linux_truncateHelper(f"/data/{'💀' * 100}.txt")


@pytest.mark.parametrize("root", ["C:\\", "\\\\server\\share\\"])
def test_a_name_in_a_drive_or_share_root_gets_all_of_max_path(root):
    # the root already ends with its backslash: it isn't counted twice
    out = cut(f"{root}{'a' * 300}.txt")
    assert windows_chars(out) == 259 and str(out.parent) == root


def test_a_linux_extension_over_the_name_limit_says_so(monkeypatch):
    # nothing of the name to keep, and the extension alone is too long
    monkeypatch.setenv("LINUX_MAX_FILE_NAME_BYTES", "3")
    with pytest.raises(ValueError, match="no room"):
        paths._linux_truncateHelper("/data/.txt")

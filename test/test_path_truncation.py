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


@pytest.mark.parametrize(
    "folder", ["/Users/someone/d", "/Users/someone/" + "d" * 300], ids=["short", "long"]
)
@pytest.mark.parametrize(
    "name", ["日本語のテキスト" * 16, "💀" * 100, "a" * 300], ids=["cjk", "emoji", "ascii"]
)
def test_a_long_mac_name_fits_in_255_bytes(monkeypatch, folder, name):
    monkeypatch.setattr(paths.platform, "system", lambda: "Darwin")
    out = paths.truncate(f"{folder}/{name}.mp4")
    size = len(out.name.encode("utf-8"))
    # as much as fits: one more character would not
    assert size <= 255 < size + len(name[0].encode("utf-8"))
    assert out.name.endswith(".mp4") and str(out.parent) == folder


def test_a_short_mac_name_is_left_alone(monkeypatch):
    monkeypatch.setattr(paths.platform, "system", lambda: "Darwin")
    path = "/Users/someone/" + "d" * 300 + "/short.mp4"
    assert str(paths.truncate(path)) == path


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

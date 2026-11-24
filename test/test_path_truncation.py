"""Long paths are cut to fit: Windows' 259-character MAX_PATH, Linux's file-name limit."""

import pathlib

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.utils.paths.paths as paths

# a folder like a real Fansly one, under a Windows home
FOLDER = r"C:\Users\someone\Data\ofscraper\OnlyFans\Fansly\a_creator\Posts\2026-10-31"
CAPTION = "Look at this dildo disappear inside of me! 💀🪄 #fyp #pussy #dildo #ass #asshole " * 3


@pytest.fixture(autouse=True)
def windows_sizes(monkeypatch):
    # the defaults, measured as on Windows whatever runs the test
    monkeypatch.delenv("UTF", raising=False)


def windows_chars(path):
    """MAX_PATH counts UTF-16 code units: an emoji takes two."""
    return len(str(path).encode("utf-16-le")) // 2


@pytest.mark.parametrize("ext", [".mp4", ".txt", "_1.jpg"])
def test_a_long_windows_path_fits_in_max_path(ext):
    out = paths._windows_truncateHelper(f"{FOLDER}\\2026-10-31 {CAPTION}{ext}")
    assert windows_chars(out) <= 259
    assert out.name.endswith(ext) and str(out.parent) == FOLDER


def test_the_cut_keeps_as_much_of_the_name_as_fits():
    out = paths._windows_truncateHelper(f"{FOLDER}\\{'a' * 300}.txt")
    # 259 characters exactly: one more letter would not fit
    assert windows_chars(out) == 259


def test_a_path_that_fits_is_left_alone():
    path = f"{FOLDER}\\2026-10-31 short.mp4"
    assert str(paths._windows_truncateHelper(path)) == path


@pytest.mark.parametrize("name, kept", [
    # the extension is removed from the end only, not wherever ".mp4" or
    # ".txt" read as a pattern matches ("-mp4", "ytxt")
    ("clip-mp4 " + "b" * 300 + ".mp4", "clip-mp4 "),
    ("wordytxt " + "b" * 300 + ".txt", "wordytxt "),
])
def test_only_the_real_extension_is_removed(name, kept):
    out = paths._windows_truncateHelper(f"{FOLDER}\\{name}")
    assert out.name.startswith(kept)


def test_a_folder_too_long_for_any_name_says_so():
    with pytest.raises(ValueError, match="folder"):
        paths._windows_truncateHelper(f"C:\\{'d' * 300}\\name.txt")


def test_a_long_linux_name_fits_in_the_name_limit():
    out = paths._linux_truncateHelper(f"/data/{'a' * 400}.txt")
    size = paths.get_string_byte_size_unix
    # measured as the app measures it; as much as fits, one more letter would not
    assert size(out.name) <= 254 < size("a" + out.name)

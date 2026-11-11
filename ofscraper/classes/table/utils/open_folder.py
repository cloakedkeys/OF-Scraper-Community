"""Open a check table row's download folder (#424).

A downloaded row opens the folder that holds its file, with the file
selected where the file manager can do that. Any other row opens the
creator's download folder: the save location plus the part of dir_format up
to and including the username, built the way the downloader builds it.
"""

import logging
import os
import pathlib
import platform
import shutil
import string
import subprocess
import threading
from dataclasses import dataclass, replace

log = logging.getLogger("shared")

# the dir_format placeholders the downloader fills with the creator's username
USERNAME_FIELDS = {"model_username", "modelusername", "user_name", "username"}


class OpenError(Exception):
    """Nothing could open the folder; the message says why."""


@dataclass
class Target:
    path: pathlib.Path
    # True: path is a file, to select in its folder
    select: bool = False
    # set when the save location is opened in place of the creator's folder
    note: str | None = None

    @property
    def folder(self) -> pathlib.Path:
        return self.path.parent if self.select else self.path


def default_root():
    """The save location (with /Fansly on Fansly), as the downloader uses."""
    from ofscraper.classes.placeholder import default_media_root

    return default_media_root()


def get_dir_format():
    import ofscraper.utils.config.data as data

    return data.get_dirformat()


# what splits folders in the path the downloader builds (a backslash only on
# Windows)
_SEPARATORS = {"/", os.sep}


def _fill_username(field, spec, conv, username):
    """One username placeholder, filled as str.format fills it."""
    piece = "{0" + (f"!{conv}" if conv else "") + (f":{spec}" if spec else "") + "}"
    return piece.format(username)


def _first_separator(text):
    found = [text.index(sep) for sep in _SEPARATORS if sep in text]
    return min(found) if found else None


def creator_dir_prefix(dir_format, username):
    """dir_format up to the end of the path part holding the username
    placeholder, filled in: ``{model_username}_archive/{mediatype}`` gives
    ``<username>_archive``.

    None when another placeholder comes before it or shares its part, or
    there is none: those depend on the post, so the creator's folder isn't
    known.
    """
    if not username:
        return None
    try:
        parsed = list(string.Formatter().parse(dir_format or ""))
    except ValueError:
        return None
    out = []
    found = False
    try:
        for literal, field, spec, conv in parsed:
            if found:
                # the rest of the username's path part
                cut = _first_separator(literal)
                if cut is not None:
                    out.append(literal[:cut])
                    break
                out.append(literal)
            else:
                out.append(literal)
            if field is None:
                break
            if field not in USERNAME_FIELDS:
                return None
            out.append(_fill_username(field, spec, conv, username))
            found = True
    except (ValueError, KeyError, IndexError, AttributeError):
        return None
    return "".join(out) if found else None


def absolute(path) -> pathlib.Path:
    """The path made absolute against the working folder, as the downloader
    used it. Not resolve(): that can turn a mapped drive into a UNC path."""
    return pathlib.Path(os.path.abspath(path))


def creator_target(username, root=None, dir_format=None) -> Target:
    root = root if root is not None else default_root()
    if not username:
        return Target(
            absolute(str(root)),
            note="No creator picked, opened the save location",
        )
    dir_format = dir_format if dir_format is not None else get_dir_format()
    prefix = creator_dir_prefix(dir_format, username)
    if prefix is None:
        return Target(
            absolute(str(root)),
            note="dir_format doesn't start with the creator, opened the save location",
        )
    # joined as Placeholders.getmediadir joins it
    return Target(absolute(os.path.normpath(f"{root}/{prefix}")))


def stored_file(row):
    """The file the database recorded for a row, or None."""
    directory = (row or {}).get("directory")
    filename = (row or {}).get("filename")
    if not directory or not filename:
        return None
    return pathlib.Path(str(directory), str(filename))


def _is_file(path):
    try:
        return path.is_file()
    except OSError:
        return False


def row_target(row, root=None, dir_format=None) -> Target:
    """What `o` opens for a row: its file if downloaded and still on disk,
    else the creator's folder."""
    if row and row.get("downloaded"):
        file = stored_file(row)
        if file is not None and _is_file(file):
            return Target(absolute(file), select=True)
    return creator_target((row or {}).get("username"), root, dir_format)


def open_command(target: Target, system=None):
    system = system or platform.system()
    path = str(target.path)
    if system == "Windows":
        # a string, not a list: explorer wants /select,"<path>" and the list
        # form would quote the whole argument
        if target.select:
            return f'explorer /select,"{path}"'
        return f'explorer "{path}"'
    if system == "Darwin":
        return ["open", "-R", path] if target.select else ["open", path]
    # xdg-open can't select a file, so the folder
    return ["xdg-open", str(target.folder)]


def _has_desktop():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_target(target: Target, system=None, popen=None):
    """Start the file manager without waiting on it.

    Raises OpenError when the folder doesn't exist or nothing can open it.
    """
    system = system or platform.system()
    popen = popen or subprocess.Popen
    # a relative path such as "-downloads/a_creator" would reach the opener
    # as an option
    target = replace(target, path=absolute(target.path))
    folder = target.folder
    try:
        exists = folder.is_dir()
    except OSError:
        exists = False
    if not exists:
        raise OpenError("Folder doesn't exist yet")
    if system not in {"Windows", "Darwin"}:
        if not shutil.which("xdg-open"):
            raise OpenError("xdg-open isn't installed")
        if not _has_desktop():
            raise OpenError("No desktop to open it on")
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if system != "Windows":
        # keep it off the table's terminal
        kwargs["start_new_session"] = True
    try:
        proc = popen(open_command(target, system), **kwargs)
    except (OSError, ValueError) as E:
        raise OpenError(f"Couldn't start the file manager ({E})") from E
    # reaped in the background, so the table never waits on it
    threading.Thread(target=proc.wait, daemon=True).start()

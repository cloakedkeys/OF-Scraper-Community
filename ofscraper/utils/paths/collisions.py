"""
Keep different media from sharing one download path.

A filename template (e.g. one built on {text}) can render the same path for
different media; the later download then replaced the earlier one (#48).
When a path is already taken by a different media, the media's own id is
appended (name.mp4 -> name_<media_id>.mp4), and if that is taken too,
name_<media_id>_2.mp4, _3 and so on. Every candidate is checked the same
way, after truncation, so the media always ends up on a path no other media
holds. The candidates depend only on the media, so the same media resolves
to the same path on every run.

"Taken" means:
- recorded in the model's database as another media's download path, or
- claimed earlier in this run by another media (not in the database yet).
Paths are compared the way the target filesystem does: each name is
case-folded only if the directory holding it ignores case (most Windows
and macOS storage), so case-sensitive directories, including Windows ones
with per-directory case sensitivity, keep exact names. Each existing
directory's own setting is read (see _contents_case_insensitive()).

If several media are recorded at one path (left by the old overwrite
behaviour), the lowest media id keeps it. The file there may hold any of
those media, so the keeper's next download replaces it whatever its size
(see takes_over()). The same goes for a media whose own row at the path
isn't marked downloaded (e.g. after `metadata -md update`, see
shared_in_db()), since the file there isn't known to be its own.
"""

import logging
import os
import pathlib
import sys
import tempfile

import ofscraper.utils.config.data as config_data
import ofscraper.utils.paths.paths as paths

log = logging.getLogger("shared")

# model_id -> {path key: {media_id: downloaded}} from the database, loaded
# once per run
_index = {}
# path key -> media id that claimed it in this run
_claims = {}
# path keys whose current file may belong to another media
_take_over = set()

MAX_CANDIDATES = 1000


def reset():
    _index.clear()
    _claims.clear()
    _take_over.clear()
    _dir_case.clear()


PLATFORM_DEFAULT_INSENSITIVE = sys.platform in ("win32", "darwin")
# directory -> whether names inside it are compared case-insensitively
_dir_case = {}
SCAN_LIMIT = 200


def _windows_case_sensitive_flag(directory):
    """The directory's own per-directory case-sensitivity flag (Windows), or
    None where it can't be read (other systems, older Windows, some shares)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    class FILE_CASE_SENSITIVE_INFO(ctypes.Structure):
        _fields_ = [("Flags", wintypes.ULONG)]

    FILE_READ_ATTRIBUTES, OPEN_EXISTING = 0x80, 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # needed to open a directory
    FileCaseSensitiveInfo, FILE_CS_FLAG_CASE_SENSITIVE_DIR = 23, 0x1
    handle = kernel32.CreateFileW(
        directory, FILE_READ_ATTRIBUTES, 0x7, None, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS, None,
    )
    if handle in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        info = FILE_CASE_SENSITIVE_INFO()
        if not kernel32.GetFileInformationByHandleEx(
            handle, FileCaseSensitiveInfo, ctypes.byref(info), ctypes.sizeof(info)
        ):
            return None
        return bool(info.Flags & FILE_CS_FLAG_CASE_SENSITIVE_DIR)
    finally:
        kernel32.CloseHandle(handle)


def _probe_entries(directory):
    """Look up an existing entry's case-swapped name; None if there's none to test."""
    with os.scandir(directory) as entries:
        for n, entry in enumerate(entries):
            if n >= SCAN_LIMIT:
                break
            swapped = entry.name.swapcase()
            if swapped == entry.name:
                continue
            other = os.path.join(directory, swapped)
            return os.path.exists(other) and os.path.samefile(other, entry.path)
    return None


def _probe_with_file(directory):
    """Create a lowercase temp file and look it up in upper case; None if it can't."""
    try:
        fd, probe = tempfile.mkstemp(prefix="ofscraper-case-probe-", dir=directory)
    except OSError:
        return None
    os.close(fd)
    try:
        name = os.path.basename(probe)
        other = os.path.join(directory, name.upper())
        if name.upper() == name:
            return None
        return os.path.exists(other) and os.path.samefile(other, probe)
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass


def _contents_case_insensitive(directory):
    """
    Whether names inside directory are compared case-insensitively.

    Windows can enable case sensitivity per directory, so an existing
    directory is asked directly: its Windows flag where readable, else an
    existing entry's case-swapped name, else a short-lived probe file. Only
    a directory that doesn't exist yet takes its parent's answer (new NTFS
    and casefold directories inherit the setting; macOS decides per volume),
    and such guesses aren't cached, so the directory is checked for real
    once it exists.
    """
    directory = os.path.normpath(str(directory))
    if directory in _dir_case:
        return _dir_case[directory]
    result = None
    if os.path.isdir(directory):
        flag = _windows_case_sensitive_flag(directory)
        if flag is not None:
            result = not flag
        else:
            try:
                result = _probe_entries(directory)
            except OSError:
                result = None
            if result is None:
                result = _probe_with_file(directory)
    if result is not None:
        _dir_case[directory] = result
        return result
    parent = os.path.dirname(directory)
    if parent == directory:
        return PLATFORM_DEFAULT_INSENSITIVE
    return _contents_case_insensitive(parent)


def case_insensitive(path):
    """Whether path's name is compared case-insensitively in its directory."""
    return _contents_case_insensitive(os.path.dirname(os.path.normpath(str(path))))


def _key(path):
    # each component is folded only if the directory holding it ignores case
    norm = os.path.normpath(str(path))
    drive, rest = os.path.splitdrive(norm)
    anchor = drive + (os.sep if rest.startswith(os.sep) else "")
    parts = [p for p in rest.split(os.sep) if p]
    key = [anchor.casefold() if os.name == "nt" else anchor]
    current = anchor or os.curdir
    for part in parts:
        key.append(part.casefold() if _contents_case_insensitive(current) else part)
        current = os.path.join(current, part)
    return tuple(key)


async def _load(model_id, username):
    if model_id not in _index:
        from ofscraper.db.operations_.media import get_media_download_paths

        index = {}
        try:
            rows = await get_media_download_paths(model_id=model_id, username=username)
        except Exception as E:
            log.debug(f"could not read download paths from the database: {E}")
            rows = []
        for media_id, directory, filename, downloaded in rows:
            owners = index.setdefault(_key(pathlib.Path(directory, filename)), {})
            owners[media_id] = owners.get(media_id, False) or downloaded
        _index[model_id] = index
    return _index[model_id]


def with_media_id(path, media_id, n=1):
    path = pathlib.Path(path)
    tail = f"_{media_id}" if n == 1 else f"_{media_id}_{n}"
    return path.with_name(f"{path.stem}{tail}{path.suffix}")


def _candidates(path, media_id):
    yield path
    for n in range(1, MAX_CANDIDATES):
        yield pathlib.Path(paths.truncate(with_media_id(path, media_id, n)))


def _taken_by_other(key, media_id, owners):
    claimed = _claims.get(key)
    if claimed is not None and claimed != media_id:
        return True
    others = set(owners) - {media_id}
    if not others:
        return False
    # shared by several media in the database: lowest id keeps the path
    return media_id not in owners or media_id > min(others)


async def resolve(path, ele):
    """The path this media should use: path itself, or a _<media_id> variant."""
    if str(config_data.get_filename_collision()).lower() == "overwrite":
        return path
    path = pathlib.Path(path)
    media_id = int(ele.id)
    index = await _load(ele.model_id, ele.username)
    # no awaits from here on: each candidate is checked and claimed together
    for candidate in _candidates(path, media_id):
        key = _key(candidate)
        owners = index.get(key, {})
        if _taken_by_other(key, media_id, owners):
            continue
        _claims[key] = media_id
        if media_id in owners and (len(owners) > 1 or not owners[media_id]):
            _take_over.add(key)
        if candidate != path:
            log.debug(
                f"Media:{media_id} {path.name} is used by another media; saving as {candidate.name}"
            )
        return candidate
    raise RuntimeError(f"Media:{media_id} no free path found for {path}")


async def shared_in_db(path, model_id, username):
    """True if the model's database records several media at this path."""
    index = await _load(model_id, username)
    return len(index.get(_key(path), {})) > 1


def takes_over(path):
    """True once for a path whose existing file may be another media's."""
    key = _key(path)
    if key in _take_over:
        _take_over.discard(key)
        return True
    return False

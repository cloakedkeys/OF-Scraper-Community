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
Paths are compared the way the target filesystem does: case-insensitively
where a case-swapped name reaches the same directory (Windows, default
macOS), exactly elsewhere.

If several media are recorded at one path (left by the old overwrite
behaviour), the lowest media id keeps it. The file there may hold any of
those media, so the keeper's next download replaces it whatever its size
(see takes_over()). The same goes for a media whose own row at the path
isn't marked downloaded (e.g. after `metadata -md update`, see
shared_in_db()), since the file there isn't known to be its own.
"""

import functools
import logging
import os
import pathlib
import sys

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


@functools.lru_cache(maxsize=None)
def _case_insensitive_dir(existing_dir):
    """Whether the filesystem holding existing_dir ignores case."""
    d = pathlib.Path(existing_dir)
    for part in (d, *d.parents):
        name = part.name
        if name and name.swapcase() != name:
            other = part.with_name(name.swapcase())
            try:
                return other.exists() and os.path.samefile(other, part)
            except OSError:
                break
    return sys.platform in ("win32", "darwin")


def case_insensitive(path):
    d = pathlib.Path(os.path.normpath(str(path))).parent
    for existing in (d, *d.parents):
        if existing.exists():
            return _case_insensitive_dir(str(existing))
    return sys.platform in ("win32", "darwin")


def _key(path):
    norm = os.path.normcase(os.path.normpath(str(path)))
    return norm.casefold() if case_insensitive(path) else norm


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

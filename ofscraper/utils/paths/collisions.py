"""
Keep different media from sharing one download path.

A filename template (e.g. one built on {text}) can render the same path for
different media; the later download then replaced the earlier one (#48).
When a path is already taken by a different media, the media's own id is
appended (name.mp4 -> name_<media_id>.mp4). The suffix depends only on the
media, so the same media resolves to the same path on every run.

"Taken" means:
- recorded in the model's database as another media's download path, or
- claimed earlier in this run by another media (not in the database yet).
If several media are recorded at one path (left by the old overwrite
behaviour), the lowest media id keeps it. The file there may hold any of
those media, so the keeper's next download replaces it whatever its size
(see takes_over()). The same goes for a media whose own row at the path
isn't marked downloaded (e.g. after `metadata -md update`, see
shared_in_db()), since the file there isn't known to be its own.

The trailing _<digits> survives path truncation (paths.truncate keeps it with
the extension), so a truncated name stays unique.
"""

import logging
import os
import pathlib

import ofscraper.utils.config.data as config_data

log = logging.getLogger("shared")

# normalized path -> media id that claimed it in this run
_claims = {}
# normalized path -> {media_id: downloaded} the database records at that path
_db_owners = {}
# normalized paths whose current file may belong to another media
_take_over = set()


def _key(path):
    return os.path.normcase(str(path))


def reset():
    _claims.clear()
    _db_owners.clear()
    _take_over.clear()


def takes_over(path):
    """True once for a path whose existing file may be another media's."""
    key = _key(path)
    if key in _take_over:
        _take_over.discard(key)
        return True
    return False


def with_media_id(path, media_id):
    path = pathlib.Path(path)
    return path.with_name(f"{path.stem}_{media_id}{path.suffix}")


async def _owners_in_db(path, model_id, username):
    key = _key(path)
    if key not in _db_owners:
        from ofscraper.db.operations_.media import get_media_ids_at_path

        try:
            _db_owners[key] = dict(
                await get_media_ids_at_path(
                    path.parent, path.name, model_id=model_id, username=username
                )
            )
        except Exception as E:
            log.debug(f"could not read download paths from the database: {E}")
            _db_owners[key] = {}
    return _db_owners[key]


def shared_in_db(path):
    """True if the database records several media at this (already resolved) path."""
    return len(_db_owners.get(_key(path), {})) > 1


def _taken_by_other(key, media_id, db_owners):
    claimed = _claims.get(key)
    if claimed is not None and claimed != media_id:
        return True
    others = set(db_owners) - {media_id}
    if not others:
        return False
    # shared by several media in the database: lowest id keeps the path
    return media_id not in db_owners or media_id > min(others)


async def resolve(path, ele):
    """The path this media should use: path itself, or with _<media_id>."""
    if str(config_data.get_filename_collision()).lower() == "overwrite":
        return path
    path = pathlib.Path(path)
    media_id = int(ele.id)
    db_owners = await _owners_in_db(path, ele.model_id, ele.username)
    # no awaits from here on: checking and claiming happen together
    key = _key(path)
    if not _taken_by_other(key, media_id, db_owners):
        _claims[key] = media_id
        if media_id in db_owners and (len(db_owners) > 1 or not db_owners[media_id]):
            _take_over.add(key)
        return path
    unique = with_media_id(path, media_id)
    log.debug(
        f"Media:{media_id} {path.name} is used by another media; saving as {unique.name}"
    )
    _claims[_key(unique)] = media_id
    return unique

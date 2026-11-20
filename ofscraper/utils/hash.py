import io
import logging
import os
import pathlib

import xxhash

import ofscraper.classes.placeholder as placeholder
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.settings as settings
from ofscraper.db.operations_.media import get_dupe_media_copies, get_dupe_media_hashes

log = logging.getLogger("shared")


fileHashes = {}


def get_hash(
    file_data,
):
    global fileHashes
    hash = None
    if settings.get_settings().hash is None:
        return
    if isinstance(file_data, placeholder.Placeholders):
        file_data = file_data.trunicated_filepath
    if fileHashes.get(str(file_data)):
        hash = fileHashes.get(str(file_data))
    else:
        hash = _calc_hash(file_data)
    log.debug(f"{file_data} => hash: {hash}")
    return hash


def _calc_hash(file_data):
    hasher = xxhash.xxh128()
    BUF_SIZE = of_env.getattr("BUF_SIZE")
    with open(file_data, "rb") as f:
        buffered_f = io.BufferedReader(f, buffer_size=BUF_SIZE)
        for block in iter(lambda: buffered_f.read(BUF_SIZE), b""):
            hasher.update(block)
    fileHashes[str(file_data)] = hasher.hexdigest()
    return hasher.hexdigest()


def remove_dupes_hash(username, model_id, mediatype=None):
    """With remove_hash_match on, delete the creator's later copies of a file
    it already has: files of `mediatype` with the same recorded hash. The
    copy saved first is kept. Returns the paths deleted."""
    if not settings.get_settings().hash:
        return []
    removed = []
    hashes = get_dupe_media_hashes(
        username=username, model_id=model_id, mediatype=mediatype
    )
    for hash in hashes:
        files = get_dupe_media_copies(
            username=username, model_id=model_id, hash=hash, mediatype=mediatype
        )
        removed.extend(_remove_copies(files))
    return removed


def _remove_copies(files):
    """Keep the first file that exists and delete the others that are copies
    of it. `files` is (media id, folder, file name) rows. Returns the paths
    deleted.

    A file is only deleted when it is certainly a spare copy:
    - it is another file than the one kept. Several rows can name one file
      (a media in more than one post), and a hard link is the kept file
      under another name;
    - it belongs to another media than the one kept. With allow-dupes a
      media has a file per post on purpose;
    - its content is the kept file's now, whatever was recorded when the two
      were saved."""
    found = [
        (media_id, pathlib.Path(directory, filename))
        for media_id, directory, filename in files
    ]
    found = [(media_id, path) for media_id, path in found if path.is_file()]
    if len(found) < 2:
        return []
    (kept_media, kept), others = found[0], found[1:]
    removed = []
    kept_hash = None
    for media_id, path in others:
        if str(media_id) == str(kept_media):
            continue
        try:
            if os.path.samefile(kept, path):
                continue
            if os.path.getsize(kept) != os.path.getsize(path):
                continue
            kept_hash = kept_hash or _calc_hash(kept)
            if _calc_hash(path) != kept_hash:
                continue
            os.unlink(path)
        except OSError as E:
            log.warning(f"Could not remove the duplicate file {path}: {E}")
            continue
        fileHashes.pop(str(path), None)
        removed.append(path)
        log.info(f"Removed {path}: it is a copy of {kept}")
    return removed

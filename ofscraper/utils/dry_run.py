"""--dry-run: scan as usual, download nothing, change nothing.

The scan writes to the creator databases and the cache in many places, so a
dry run doesn't try to stop each write. It points the scan at throwaway
copies instead: each creator's database file is copied the first time it is
used, and the profile's cache folder is copied when the cache is first
opened. The copies are removed at the end of the run. The real files are
never opened.
"""

import atexit
import hashlib
import logging
import pathlib
import shutil
import tempfile
import threading

import ofscraper.utils.settings as settings

log = logging.getLogger("shared")

_lock = threading.Lock()
_root = None
_copies = {}
_counts = {}


def active():
    try:
        return bool(getattr(settings.get_settings(), "dry_run", False))
    except Exception:
        return False


def _workdir():
    global _root
    if _root is None:
        _root = pathlib.Path(tempfile.mkdtemp(prefix="ofscraper-dry-run-"))
        atexit.register(cleanup)
    return _root


def _copy_file(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dest)
        # a write-ahead log holds committed rows not yet in the file
        wal = src.with_name(src.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, dest.with_name(dest.name + "-wal"))


def _copy_dir(src, dest):
    if src.exists():
        shutil.copytree(src, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)


def redirect(real_path, folder=False):
    """The throwaway copy to use for real_path, made the first time it is
    asked for. folder=True copies a whole folder (the cache)."""
    real = pathlib.Path(real_path)
    key = str(real.resolve() if real.exists() else real.absolute())
    with _lock:
        if key in _copies:
            return _copies[key]
        name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        dest = _workdir() / name / real.name
        if folder:
            _copy_dir(real, dest)
        else:
            _copy_file(real, dest)
        _copies[key] = dest
        log.debug(f"dry run: using a copy of {real}")
        return dest


def cleanup():
    """Remove the copies. Safe to call more than once."""
    global _root
    # the cache may hold the copy open: close it, and forget it so a later
    # use opens the cache path afresh rather than the removed copy
    try:
        import ofscraper.utils.cache.cache as cache

        with cache.lock:
            if cache.cache is not None:
                cache.cache.close()
                cache.cache = None
    except Exception:
        pass
    with _lock:
        root, _root = _root, None
        _copies.clear()
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


def refuse_reason():
    """Why this run can't be a dry run, or None."""
    import ofscraper.db.postgres as postgres

    if postgres.enabled():
        return (
            "--dry-run doesn't work with PostgreSQL yet: it can't take a throwaway "
            "copy of the database. Run it without the PostgreSQL setting."
        )
    return None


def record(username, media):
    """Count what a download would have attempted for a creator."""
    with _lock:
        _counts[username] = _counts.get(username, 0) + len(media or [])


def reset():
    with _lock:
        _counts.clear()


def report():
    """What the dry run would have done, one line."""
    with _lock:
        total = sum(_counts.values())
        creators = len(_counts)
    noun = "file" if total == 1 else "files"
    who = "creator" if creators == 1 else "creators"
    text = (
        f"Dry run: nothing was downloaded and nothing was changed. "
        f"A download would attempt up to {total} {noun} from {creators} {who}."
    )
    s = settings.get_settings()
    if getattr(s, "size_max", None) or getattr(s, "size_min", None) or getattr(
        s, "skip_download_script", None
    ):
        text += " (Size limits and skip_download_script are decided during a download, so they aren't applied here.)"
    return text

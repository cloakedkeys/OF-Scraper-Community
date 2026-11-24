"""Install an optional extra the first time a feature needs it.

``--live`` on OnlyFans needs the ``live`` extra (Playwright) and a Chromium
browser; ``OFSC_DATABASE_URL`` needs psycopg, which is a dependency wherever it
has prebuilt wheels and the ``postgres`` extra elsewhere. Instead
of stopping with an install command, the first run installs them into the
Python running OF-Scraper, with pip, or with uv when that Python has no pip
(``uv tool install``). ``OFSC_INSTALL_EXTRAS=False`` turns this off.

The standalone download can't add packages, so it never tries.
"""

import functools
import importlib
import importlib.util
import logging
import shutil
import subprocess
import sys
import threading

log = logging.getLogger("shared")

# the same lists as [project.optional-dependencies] in pyproject.toml (test_extras checks it)
EXTRAS = {
    "postgres": ["psycopg[binary,pool]>=3.3.9,<3.4"],
    "live": ["playwright>=1.40,<2"],
}
# a slow connection and Chromium's ~150 MB download
INSTALL_TIMEOUT = 1800

_lock = threading.Lock()


def _once(func):
    """lru_cache, with callers at the same time (threads asking for the
    PostgreSQL driver together) waiting for the one install instead of each
    starting their own."""
    cached = functools.lru_cache(maxsize=None)(func)

    @functools.wraps(func)
    def wrapper(*args):
        with _lock:
            return cached(*args)

    wrapper.cache_clear = cached.cache_clear
    return wrapper


def enabled() -> bool:
    import ofscraper.utils.of_env.of_env as of_env

    return bool(of_env.getattr("INSTALL_EXTRAS"))


def pip_command(requirements) -> list | None:
    """The command that installs ``requirements`` into this Python, or None."""
    if importlib.util.find_spec("pip"):
        return [sys.executable, "-m", "pip", "install", *requirements]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *requirements]
    return None


def _run(cmd, what) -> bool:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=INSTALL_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(f"Couldn't install {what}: {e}")
        return False
    if r.returncode != 0:
        said = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-5:])
        log.warning(f"Couldn't install {what}:\n{said}")
        return False
    log.debug(f"Installed {what}:\n{r.stdout}")
    return True


@_once
def install(extra: str) -> bool:
    """Install the ``extra`` packages, once per run. True when they were installed.

    A failure isn't retried until the next run: ``--live-watch`` asks again
    every interval.
    """
    if getattr(sys, "frozen", False) or not enabled():
        return False
    cmd = pip_command(EXTRAS[extra])
    if cmd is None:
        log.warning(f"Can't install the optional {extra!r} packages: this Python has neither pip nor uv")
        return False
    log.info(f"Installing the optional {extra!r} packages; this happens once")
    ok = _run(cmd, f"the optional {extra!r} packages")
    # let this run import what was just installed
    importlib.invalidate_caches()
    if ok:
        log.info(f"Installed the optional {extra!r} packages")
    return ok


@_once
def install_chromium() -> bool:
    """Download Playwright's Chromium, once per run. True when it worked."""
    if getattr(sys, "frozen", False) or not enabled():
        return False
    log.info("Downloading the Chromium browser for livestream recording (about 150 MB); this happens once")
    ok = _run([sys.executable, "-m", "playwright", "install", "chromium"], "the Chromium browser")
    if ok:
        log.info("Chromium is installed")
    return ok

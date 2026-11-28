"""Runs the user's scripts (script_options) the same way: the path checks,
the subprocess, its output and the errors it can raise.

A failed script is logged, never raised, so it doesn't stop the run.
"""

import json
import logging
import os
import subprocess
import traceback

import ofscraper.utils.dry_run as dry_run
import ofscraper.utils.of_env.of_env as env
from ofscraper.utils.system.subprocess import async_run, run

log = logging.getLogger("shared")


def script_ready(script_path, name, skip_in_dry_run=True):
    """True when the script is set, exists and, if skip_in_dry_run, this
    isn't a dry run."""
    if not script_path:
        log.debug(f"{name.capitalize()} is disabled; skipping execution")
        return False
    if skip_in_dry_run and dry_run.active():
        log.debug(f"Dry run: not running the {name}")
        return False
    if not os.path.exists(script_path):
        log.info(
            f"{name.capitalize()} path is invalid: '{script_path}'. Aborting {name}."
        )
        return False
    return True


def run_user_script(
    script_path,
    make_input,
    *,
    name,
    level_key,
    subject=None,
    as_json=True,
    timeout=600,
):
    """Run the script with make_input()'s result on stdin, JSON-encoded when
    as_json. make_input is called here so an error building the payload is
    logged like any other. subject names what the script runs for in the log.

    Returns the finished process, or None if the script failed.
    """
    about = f" for {subject}" if subject else ""
    try:
        stdin = _stdin(make_input, as_json)
        log.info(f"Running {name}{about} with script: {script_path}")
        result = run([script_path], input=stdin, **_options(name, level_key, timeout))
    except Exception as e:
        _log_failure(e, script_path, name, about)
        return None
    # outside the try: the script succeeded, whatever happens logging it
    _log_success(result, name, about)
    return result


async def async_run_user_script(
    script_path,
    make_input,
    *,
    name,
    level_key,
    subject=None,
    as_json=True,
    timeout=600,
):
    """run_user_script, without blocking the event loop."""
    about = f" for {subject}" if subject else ""
    try:
        stdin = _stdin(make_input, as_json)
        log.info(f"Running {name}{about} with script: {script_path}")
        result = await async_run(
            [script_path], input=stdin, **_options(name, level_key, timeout)
        )
    except Exception as e:
        _log_failure(e, script_path, name, about)
        return None
    _log_success(result, name, about)
    return result


class _BadPayload(Exception):
    """The payload couldn't be encoded as JSON."""


def _stdin(make_input, as_json):
    data = make_input()
    if not as_json:
        return data
    try:
        return json.dumps(data, indent=None, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise _BadPayload(e) from e


def _options(name, level_key, timeout):
    return dict(
        capture_output=True,
        text=True,
        check=True,
        level=env.getattr(level_key),
        name=name,
        timeout=timeout,
    )


def output_level(value):
    """SCRIPT_OUTPUT_SUBPROCCESS_LEVEL as a logging level. It isn't a typed
    setting, so from the environment it's a string: a number ("20") or a
    level name ("INFO") is used, anything else is DEBUG."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
        return logging.getLevelNamesMapping().get(text.upper(), logging.DEBUG)
    return logging.DEBUG


def _log_success(result, name, about):
    if result is not None and env.getattr("SCRIPT_OUTPUT_SUBPROCCESS"):
        level = output_level(env.getattr("SCRIPT_OUTPUT_SUBPROCCESS_LEVEL"))
        for label, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            if text and text.strip():
                log.log(level, f"{name.capitalize()} {label}{about}:\n{text.strip()}")
    log.info(f"{name.capitalize()} ran successfully{about}.")


def _log_failure(e, script_path, name, about):
    """Log why the script failed; called from the except block."""
    if isinstance(e, _BadPayload):
        log.info(f"Failed to serialize payload to JSON for {name}{about}: {e}")
    elif isinstance(e, FileNotFoundError):
        log.info(
            f"{name.capitalize()} executable not found: '{script_path}'. Please "
            "ensure the path is correct and the script is executable."
        )
    elif isinstance(e, subprocess.CalledProcessError):
        log.info(
            f"{name.capitalize()} failed{about} with exit code {e.returncode}: "
            f"'{script_path}'"
        )
        for label, text in (("stdout", e.stdout), ("stderr", e.stderr)):
            if text and text.strip():
                log.debug(f"{name.capitalize()} {label}:\n{text.strip()}")
    else:
        log.info(
            f"An unexpected error occurred while running {name}{about} with "
            f"script '{script_path}': {e}"
        )
        log.traceback_(traceback.format_exc())

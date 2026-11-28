"""The shared runner for the user's scripts (#869): what each script gets on
stdin, its timeout, and how a failure is logged instead of raised."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

import ofscraper.__main__  # noqa: F401  (import order: avoids a circular import)
import ofscraper.scripts.after_download_script as after_download
import ofscraper.scripts.final_script as final
import ofscraper.scripts.runner as runner
import ofscraper.scripts.skip_download_script as skip
import ofscraper.utils.settings as settings


@pytest.fixture
def configured(monkeypatch, tmp_path):
    """Point every script setting at one Python script and return its path."""
    script = tmp_path / "script.py"

    def write(body):
        script.write_text(body, encoding="utf-8")
        return script

    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda *a, **k: SimpleNamespace(
            skip_download_script=str(script),
            after_download_script=str(script),
            post_script=str(script),
            command="scraper",
        ),
    )
    monkeypatch.setattr(runner.dry_run, "active", lambda: False)
    logger = logging.getLogger("shared")
    monkeypatch.setattr(logger, "traceback_", lambda *a, **k: None, raising=False)
    return write


def _ele(**media):
    return SimpleNamespace(
        media=media, post=SimpleNamespace(post={}), username="creator1",
        model_id=1, post_id=2, id=3,
    )


def test_after_download_script_gets_the_bare_path(configured, tmp_path):
    out = tmp_path / "stdin.txt"
    configured(f"import sys\nopen({str(out)!r}, 'w', encoding='utf-8').write(sys.stdin.read())\n")
    final_path = tmp_path / "file.mp4"
    asyncio.run(after_download.after_download_script(final_path))
    assert out.read_text(encoding="utf-8") == str(final_path)


def test_skip_script_gets_json_and_answers(configured):
    configured(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print('skip-permanent' if payload['media_id'] == 3 else 'true')\n"
    )
    assert asyncio.run(skip.skip_download_script(1000, _ele())) == skip.SKIP_PERMANENT


def test_output_logging_with_an_untyped_level_keeps_the_answer(
    configured, monkeypatch, caplog
):
    # SCRIPT_OUTPUT_SUBPROCCESS_LEVEL set from the environment is a string,
    # which log.log rejects: the script still succeeded, so its answer counts
    configured("print('skip-permanent')\n")
    real = runner.env.getattr
    overrides = {"SCRIPT_OUTPUT_SUBPROCCESS": True, "SCRIPT_OUTPUT_SUBPROCCESS_LEVEL": "INFO"}
    monkeypatch.setattr(
        runner.env, "getattr", lambda key, *a: overrides.get(key, real(key, *a))
    )
    with caplog.at_level(logging.DEBUG, logger="shared"):
        assert asyncio.run(skip.skip_download_script(1000, _ele())) == skip.SKIP_PERMANENT
    assert any("stdout for" in r.getMessage() for r in caplog.records)


def test_failing_script_is_logged_and_downloads(configured, caplog):
    configured("import sys\nprint('oops', file=sys.stderr)\nsys.exit(3)\n")
    with caplog.at_level(logging.DEBUG, logger="shared"):
        assert asyncio.run(skip.skip_download_script(1000, _ele())) is False
    messages = [r.getMessage() for r in caplog.records]
    assert any("failed for creator1" in m and "exit code 3" in m for m in messages)
    assert any("stderr:\noops" in m for m in messages)


def _circular():
    loop = {}
    loop["self"] = loop
    return loop


@pytest.mark.parametrize("bad", [object(), _circular()], ids=["TypeError", "ValueError"])
def test_payload_that_isnt_json_is_logged_and_the_script_not_run(
    configured, monkeypatch, caplog, bad
):
    # json.dumps raises TypeError or ValueError, never JSONDecodeError
    configured("")
    started = []

    async def fake_run(*a, **k):
        started.append(a)

    monkeypatch.setattr(runner, "async_run", fake_run)
    with caplog.at_level(logging.INFO, logger="shared"):
        assert asyncio.run(skip.skip_download_script(1000, _ele(bad=bad))) is False
    assert started == []
    assert any(
        "Failed to serialize payload to JSON for download skip script" in r.getMessage()
        for r in caplog.records
    )


def test_missing_executable_is_logged(configured, monkeypatch, caplog):
    def not_found(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(runner, "run", not_found)
    monkeypatch.setattr(final, "manager", SimpleNamespace(Manager=SimpleNamespace(
        current_model_manager=SimpleNamespace(get_processed=lambda a: [], get_unprocessed=lambda a: []))))
    configured("")
    with caplog.at_level(logging.INFO, logger="shared"):
        final.final_script()
    assert any("Final script executable not found" in r.getMessage() for r in caplog.records)


def test_timeouts(configured, monkeypatch):
    configured("")
    seen = {}

    def fake_run(cmd, **k):
        seen["final"] = k["timeout"]

    async def fake_async_run(cmd, **k):
        seen.setdefault("async", []).append(k["timeout"])
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner, "async_run", fake_async_run)
    monkeypatch.setattr(final, "manager", SimpleNamespace(Manager=SimpleNamespace(
        current_model_manager=SimpleNamespace(get_processed=lambda a: [], get_unprocessed=lambda a: []))))
    final.final_script()
    asyncio.run(after_download.after_download_script("file.mp4"))
    asyncio.run(skip.skip_download_script(1000, _ele()))
    assert seen == {"final": 1800, "async": [600, 600]}

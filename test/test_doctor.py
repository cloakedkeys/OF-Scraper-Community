"""ofscraper doctor: offline checks of the setup, in a scratch home."""

import json
import os
import sys

import pytest

import ofscraper.__main__ as entrypoint
import ofscraper.commands.doctor as doctor
import ofscraper.db.postgres as postgres
import ofscraper.utils.system.ffmpeg as ffmpeg

SECRET = "s3cret-value-that-must-not-show-0123456789"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) a test agent"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(postgres.URL_KEY, raising=False)
    monkeypatch.delenv("OFSC_FFMPEG_DEFAULT", raising=False)
    return home


def config_dir(home):
    return home / ".config" / "ofscraper"


def write_config(home, config):
    path = config_dir(home) / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config) if not isinstance(config, str) else config, encoding="utf-8")
    return path


def write_auth(home, **fields):
    auth = {"sess": SECRET, "auth_id": "12345", "user_agent": UA, "x-bc": SECRET, **fields}
    path = config_dir(home) / "main_profile" / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"auth": auth}), encoding="utf-8")
    return path


def result(report, check):
    (found,) = [r for r in report.results if r["check"] == check]
    return found["status"], found["detail"]


def files_under(path):
    return sorted(str(p) for p in path.rglob("*"))


def test_an_empty_home_gets_findings_and_nothing_is_created(home):
    report = doctor.run()
    assert files_under(home) == []
    assert result(report, "config file")[0] == "WARN"
    assert result(report, "OnlyFans auth") == ("FAIL", f"{config_dir(home) / 'main_profile' / 'auth.json'} isn't there")
    assert result(report, "Fansly auth")[0] == "SKIP"
    assert report.failed


def test_a_broken_config_is_a_finding_not_a_prompt(home, monkeypatch):
    write_config(home, '{\n  "main_profile": "main_profile",\n  oops\n}')
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("asked a question"))
    status, detail = result(doctor.run(), "config file")
    assert status == "FAIL" and "line 3" in detail


def test_auth_fields_are_named_never_shown(home, capsys):
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home, **{"x-bc": ""})
    status, detail = result(doctor.run(), "OnlyFans auth")
    assert status == "FAIL" and detail.endswith("blank in " + str(config_dir(home) / "main_profile" / "auth.json") + ": x-bc")
    write_auth(home)
    assert result(doctor.run(), "OnlyFans auth")[0] == "PASS"
    doctor.main([])
    doctor.main(["--json"])
    out = capsys.readouterr().out
    assert SECRET not in out and UA not in out


def test_a_fansly_profile_checks_the_fansly_auth(home):
    folder = config_dir(home) / "main_profile"
    folder.mkdir(parents=True)
    (folder / "site.json").write_text('{"site": "fansly"}', encoding="utf-8")
    report = doctor.run()
    assert result(report, "OnlyFans auth")[0] == "SKIP"
    status, detail = result(report, "Fansly auth")
    assert status == "FAIL" and "isn't there" in detail
    (folder / "fansly_auth.json").write_text(json.dumps({"authorization_token": "x" * 60, "user_agent": UA}), encoding="utf-8")
    assert result(doctor.run(), "Fansly auth")[0] == "PASS"
    (folder / "fansly_auth.json").write_text(json.dumps({"authorization_token": "short", "user_agent": UA}), encoding="utf-8")
    status, detail = result(doctor.run(), "Fansly auth")
    assert status == "FAIL" and "authorization_token is missing or too short" in detail


def test_an_old_configured_ffmpeg_fails(home, monkeypatch, tmp_path):
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"")
    write_config(home, {"binary_options": {"ffmpeg": str(fake)}})
    monkeypatch.setattr(doctor.shutil, "which", lambda p: p)
    monkeypatch.setattr(ffmpeg, "ffmpeg_version", lambda p: (True, "5.1.2", 5))
    assert result(doctor.run(), "ffmpeg") == ("FAIL", f"{fake} is version 5.1.2; protected videos need 6 or newer")


def test_ffmpeg_shows_its_version_and_where_it_was_found(home, monkeypatch):
    monkeypatch.setattr(ffmpeg, "find_ffmpeg", lambda configured: ("/usr/bin/ffmpeg", "PATH", "7.1"))
    assert result(doctor.run(), "ffmpeg") == ("PASS", "7.1 (from PATH): /usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg, "find_ffmpeg", lambda configured: (None, None, None))
    report = doctor.run()
    assert result(report, "ffmpeg")[0] == "FAIL" and result(report, "ffprobe")[0] == "SKIP"


def test_ffmpeg_version_reads_the_version(monkeypatch):
    class Done:
        stdout = "ffmpeg version 7.1.1-essentials Copyright (c) 2000-2025\n"
        stderr = ""

    monkeypatch.setattr(ffmpeg, "run", lambda *a, **k: Done)
    assert ffmpeg.ffmpeg_version("ffmpeg") == (True, "7.1.1-essentials", 7)
    Done.stdout = "ffmpeg version N-118000-gabc Copyright"
    assert ffmpeg.ffmpeg_version("ffmpeg") == (True, "N-118000-gabc", None)
    Done.stdout = "something else"
    assert ffmpeg.ffmpeg_version("ffmpeg") == (False, None, None)


def test_key_files(home, tmp_path):
    write_config(home, {"cdm_options": {"key-mode-default": "manual"}})
    assert result(doctor.run(), "key files") == ("FAIL", "client-id is not set; private-key is not set")
    client, key = tmp_path / "client_id.bin", tmp_path / "private_key.pem"
    write_config(home, {"cdm_options": {"key-mode-default": "manual", "client-id": str(client), "private-key": str(key)}})
    status, detail = result(doctor.run(), "key files")
    assert status == "FAIL" and "file not found" in detail
    client.write_bytes(b"not a client id")
    key.write_bytes(b"not a key")
    status, detail = result(doctor.run(), "key files")
    assert status == "FAIL" and detail.startswith("the key files can't be loaded")
    write_config(home, {"cdm_options": {"key-mode-default": "cdrm"}})
    assert "isn't contacted" in result(doctor.run(), "key files")[1]


def test_folders_and_the_free_space_limit(home, tmp_path, monkeypatch):
    save = tmp_path / "library"
    write_config(home, {"file_options": {"save_location": str(save)}, "download_options": {"system_free_min": 1000}})
    report = doctor.run()
    assert result(report, "save folder") == ("FAIL", f"{save} isn't there")
    save.mkdir()
    monkeypatch.setenv("OFSC_DISK_SPACE_CHECK_PATH", str(save))
    report = doctor.run()
    assert result(report, "save folder")[0] == "PASS"
    assert result(report, "free-space limit")[1].startswith("1000 bytes")
    real = doctor._device
    monkeypatch.setattr(doctor, "_device", lambda p: 1 if str(p) == str(save) else real(p) and 2)
    monkeypatch.setenv("OFSC_DISK_SPACE_CHECK_PATH", str(tmp_path))
    status, detail = result(doctor.run(), "free-space limit")
    assert status == "WARN" and "OFSC_DISK_SPACE_CHECK_PATH" in detail


def test_a_temp_folder_that_isnt_there(home, tmp_path):
    write_config(home, {"advanced_options": {"temp_dir": str(tmp_path / "nope")}})
    assert result(doctor.run(), "temp folder")[0] == "FAIL"


def test_database_setting_is_named_without_the_password(home, monkeypatch, capsys):
    monkeypatch.setenv(postgres.URL_KEY, f"postgresql://scraper:{SECRET}@db.example:5432/library")
    monkeypatch.setitem(sys.modules, "psycopg", None)
    status, detail = result(doctor.run(), "database")
    assert status == "FAIL" and "driver" in detail and SECRET not in detail
    doctor.main(["--json"])
    assert SECRET not in capsys.readouterr().out


def test_routed_with_exit_status_and_json(home, capsys):
    assert entrypoint.main(["doctor", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert {r["status"] for r in report["results"]} <= {"PASS", "WARN", "FAIL", "SKIP"}
    assert files_under(home) == []


def test_config_path_follows_the_runs_rule(home, tmp_path):
    folder = tmp_path / "cfg"
    folder.mkdir()
    assert doctor.config_path(str(folder)) == folder / "config.json"
    assert doctor.config_path("other.json") == config_dir(home) / "other.json"
    assert doctor.config_path(None) == config_dir(home) / "config.json"
    assert os.path.basename(doctor.config_path(str(tmp_path / "x" / "my.json"))) == "my.json"


# --- --online ---

def test_online_with_a_blank_auth_file_skips_the_login_and_never_prompts(home, monkeypatch):
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home, sess="")
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("asked a question"))
    monkeypatch.setattr(doctor, "start", lambda *a: pytest.fail("started"))
    status, detail = result(doctor.run(online=True), "Onlyfans login")
    assert status == "SKIP" and "auth file failed" in detail


def test_online_reports_postgresql_down_and_goes_on(home, monkeypatch):
    import ofscraper.data.api.init as init

    monkeypatch.setenv(postgres.URL_KEY, f"postgresql://scraper:{SECRET}@db.example:5432/library")
    monkeypatch.setitem(sys.modules, "psycopg", sys.modules.get("psycopg") or object())
    monkeypatch.setitem(sys.modules, "psycopg_pool", sys.modules.get("psycopg_pool") or object())

    def down():
        raise postgres.PostgresUnavailable("Can't use the PostgreSQL database at db.example:5432/library")

    monkeypatch.setattr(postgres.POSTGRES, "check", down)
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home)
    monkeypatch.setattr(doctor, "start", lambda *a: None)
    monkeypatch.setattr(init, "auth_status", lambda: ("UP", None))
    report = doctor.run(online=True)
    assert result(report, "PostgreSQL")[0] == "FAIL"
    assert result(report, "Onlyfans login") == ("PASS", "logged in")
    assert SECRET not in json.dumps(report.results)


@pytest.mark.parametrize("answer, status", [
    (("DOWN", None), "FAIL"),
    (("UNAVAILABLE", "couldn't reach the site"), "WARN"),
])
def test_online_tells_a_rejected_login_from_one_not_checked(home, monkeypatch, answer, status):
    import ofscraper.data.api.init as init

    write_config(home, {"main_profile": "main_profile"})
    write_auth(home)
    monkeypatch.setattr(doctor, "start", lambda *a: None)
    monkeypatch.setattr(init, "auth_status", lambda: answer)
    assert result(doctor.run(online=True), "Onlyfans login")[0] == status


def test_online_says_which_signing_rule_source_answered(home, monkeypatch):
    import ofscraper.data.api.init as init
    import ofscraper.utils.auth.request as request

    write_config(home, {"main_profile": "main_profile"})
    write_auth(home)
    monkeypatch.setattr(doctor, "start", lambda *a: None)
    monkeypatch.setattr(init, "auth_status", lambda: ("UP", None))
    monkeypatch.setattr(request, "last_attempt", {"used": "rafa", "failed": [("xagler", "couldn't reach the rule host (timed out)")]})
    status, detail = result(doctor.run(online=True), "signing rules")
    assert status == "WARN" and "from rafa" in detail and "xagler" in detail


def test_online_stash_unreachable(home, monkeypatch):
    monkeypatch.setenv("STASH_URL", "http://127.0.0.1:9/graphql")
    monkeypatch.setenv("STASH_API_KEY", SECRET)
    write_config(home, {"main_profile": "main_profile"})
    report = doctor.run(online=True)
    status, detail = result(report, "Stash")
    assert status == "FAIL" and SECRET not in detail


def test_offline_points_at_online(home):
    assert result(doctor.run(), "login")[0] == "SKIP"

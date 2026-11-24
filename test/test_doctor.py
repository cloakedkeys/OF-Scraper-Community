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


@pytest.mark.parametrize("fields, finding", [
    ({"sess": SECRET + ";"}, "sess has ;"),
    ({"auth_id": "123\u200b45"}, "auth_id has characters outside printable ASCII"),
    ({"x-bc": '"' + SECRET + '"'}, "x-bc has quotes"),
    ({"sess": "sess=" + SECRET}, "sess has ="),
    ({"user_agent": "Mozilla/5.0\u00a0(Windows NT 10.0)"}, "user_agent has characters outside printable ASCII"),
    ({"sess": "deleted"}, "sess is \"deleted\""),
])
def test_stray_characters_in_auth_fields_are_named_never_shown(home, fields, finding):
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home, **fields)
    report = doctor.run()
    assert result(report, "OnlyFans auth")[0] == "PASS"
    status, detail = result(report, "OnlyFans auth values")
    assert status == "WARN" and finding in detail
    assert SECRET not in json.dumps(report.results) and "\u200b" not in detail


def test_clean_auth_fields_and_a_user_agents_semicolons_pass(home):
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home, sess="  " + SECRET + "\n")
    report = doctor.run()
    assert [r for r in report.results if r["check"] in ("OnlyFans auth values", "auth in config")] == []


def test_auth_values_in_config_json(home):
    write_config(home, {"main_profile": "main_profile", "sess": SECRET, "general": {"x-bc": SECRET, "auth_id": ""}})
    write_auth(home)
    status, detail = result(doctor.run(), "auth in config")
    assert status == "WARN" and "sess, x-bc" in detail and "belong in auth.json" in detail
    assert SECRET not in detail


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


def manual_keys(home, client, key):
    write_config(home, {"cdm_options": {"key-mode-default": "manual", "client-id": str(client), "private-key": str(key)}})
    return result(doctor.run(), "key files")


def test_key_files_say_a_tilde_isnt_expanded(home, tmp_path):
    status, detail = manual_keys(home, "~/keys/client_id.bin", tmp_path / "private_key.pem")
    assert status == "FAIL" and "file not found" in detail
    assert "client-id starts with ~, which isn't expanded: use the full path" in detail


def test_key_files_name_a_dumped_file_beside_the_missing_one(home, tmp_path):
    (tmp_path / "device_client_id_blob").write_bytes(b"c")
    (tmp_path / "private_key").write_bytes(b"k")
    status, detail = manual_keys(home, tmp_path / "client_id.bin", tmp_path / "private_key.pem")
    assert status == "FAIL"
    assert "client-id: no file at that name, but device_client_id_blob is in that folder" in detail
    assert "private-key: no file at that name, but private_key is in that folder" in detail


def test_key_files_that_look_swapped_or_json(home, tmp_path):
    client, key = tmp_path / "client_id.bin", tmp_path / "private_key.pem"
    client.write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n")
    key.write_bytes(b"\x08\x01\x12\x10not a key")
    status, detail = manual_keys(home, client, key)
    assert status == "FAIL" and detail.startswith("the key files can't be loaded")
    assert "client-id looks like a private key: the two may be swapped" in detail
    assert "private-key looks like a client id: the two may be swapped" in detail
    client.write_text('{"client_id": "abc"}', encoding="utf-8")
    status, detail = manual_keys(home, client, key)
    assert "client-id looks like a JSON file, not a key file" in detail


def test_key_files_set_in_auth_json(home, tmp_path):
    write_config(home, {"cdm_options": {"key-mode-default": "manual"}})
    write_auth(home, **{"client-id": SECRET, "private_key": SECRET})
    status, detail = result(doctor.run(), "key files")
    assert status == "FAIL" and detail.startswith("client-id is not set; private-key is not set")
    assert "client-id and private-key are in auth.json: they belong in config.json" in detail
    assert SECRET not in detail


def test_key_mode_cdrm_warns_without_contacting_it(home, tmp_path, monkeypatch):
    from ofscraper.commands.scraper.actions.download.utils import keyhelpers

    monkeypatch.setattr(keyhelpers, "load_device", lambda *a: pytest.fail("loaded the keys"))
    write_config(home, {"cdm_options": {"key-mode-default": "cdrm"}})
    report = doctor.run()
    status, detail = result(report, "key files")
    assert status == "WARN" and "doesn't work" in detail and "key-mode-default to manual" in detail
    assert "then set client-id and private-key" in detail
    client, key = tmp_path / "client_id.bin", tmp_path / "private_key.pem"
    client.write_bytes(b"c")
    key.write_bytes(b"k")
    write_config(home, {"cdm_options": {"key-mode-default": "cdrm", "client-id": str(client), "private-key": str(key)}})
    status, detail = result(doctor.run(), "key files")
    assert status == "WARN" and "already set, so that switch is the whole fix" in detail


def test_folders_and_the_free_space_limit(home, tmp_path, monkeypatch):
    save = tmp_path / "library"
    write_config(home, {"file_options": {"save_location": str(save)}, "download_options": {"system_free_min": 1000}})
    report = doctor.run()
    assert result(report, "save folder") == ("FAIL", f"{save} isn't there")
    save.mkdir()
    # of_env reads OFSC_DISK_SPACE_CHECK_PATH once, so the value is given here
    check_path = {"value": str(save)}
    real_getattr = doctor.of_env.getattr
    monkeypatch.setattr(doctor.of_env, "getattr",
                        lambda key, *a, **k: check_path["value"] if key == "DISK_SPACE_CHECK_PATH" else real_getattr(key, *a, **k))
    report = doctor.run()
    assert result(report, "save folder")[0] == "PASS"
    assert result(report, "free-space limit")[1].startswith(f"1000 bytes, measured at {save}")
    real = doctor._device
    monkeypatch.setattr(doctor, "_device", lambda p: 1 if str(p) == str(save) else real(p) and 2)
    check_path["value"] = str(tmp_path)
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


# --- review follow-up ---

def test_online_postgres_check_parses_no_command_line(home, monkeypatch, capsys):
    """The PostgreSQL check runs before the start sets sys.argv: reading the
    settings there would parse `doctor --online` as a run's options."""
    import types

    import ofscraper.data.api.init as init
    import ofscraper.utils.args.globals as global_args
    import ofscraper.utils.settings as settings

    monkeypatch.setenv(postgres.URL_KEY, f"postgresql://scraper:{SECRET}@db.example:5432/library")
    monkeypatch.setitem(sys.modules, "psycopg", sys.modules.get("psycopg") or object())
    monkeypatch.setitem(sys.modules, "psycopg_pool", sys.modules.get("psycopg_pool") or object())
    monkeypatch.setattr(sys, "argv", ["ofscraper", "doctor", "--online", "--json"])
    monkeypatch.setattr(settings, "settings", {})
    monkeypatch.setattr(global_args, "args", None)
    schemas = []
    conn = types.SimpleNamespace(info=types.SimpleNamespace(server_version=160002))
    monkeypatch.setattr(postgres.POSTGRES, "connect", lambda where, ro, timeout: schemas.append(where) or conn)
    monkeypatch.setattr(postgres.POSTGRES, "close", lambda c: None)
    monkeypatch.setattr(doctor, "start", lambda *a: None)
    monkeypatch.setattr(init, "auth_status", lambda: ("UP", None))
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home)
    entrypoint.main(["doctor", "--online", "--json"])
    results = {r["check"]: r for r in json.loads(capsys.readouterr().out)["results"]}
    assert results["PostgreSQL"]["status"] == "PASS" and "16.2" in results["PostgreSQL"]["detail"]
    assert schemas == ["onlyfans"]
    assert settings.settings == {} and global_args.args is None


def test_an_empty_auth_file_is_a_finding(home, monkeypatch):
    import ofscraper.utils.settings as settings

    write_config(home, {"main_profile": "main_profile"})
    path = write_auth(home)
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "get_settings", lambda: pytest.fail("read the settings"))
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("asked a question"))
    assert result(doctor.run(), "OnlyFans auth") == ("FAIL", f"{path} is empty")


def test_an_empty_config_object_is_the_defaults(home, monkeypatch):
    import ofscraper.utils.config.file as config_file
    import ofscraper.utils.settings as settings

    write_config(home, {})
    monkeypatch.setattr(config_file, "open_config", lambda: pytest.fail("opened the config"))
    monkeypatch.setattr(settings, "get_args", lambda *a, **k: pytest.fail("read the arguments"))
    report = doctor.run()
    assert result(report, "config file")[0] == "PASS"
    assert result(report, "save folder")[0] in ("PASS", "FAIL")


def test_an_unset_check_path_measures_the_save_folders_disk(home, tmp_path, monkeypatch):
    """OFSC_DISK_SPACE_CHECK_PATH empty means the disk that holds
    save_location, as the free-space check measures it."""
    save = tmp_path / "library"
    save.mkdir()
    write_config(home, {"file_options": {"save_location": str(save / "not" / "made")},
                        "download_options": {"system_free_min": 1000}})
    real = doctor.of_env.getattr
    # an empty value is what the default becomes with #795
    monkeypatch.setattr(doctor.of_env, "getattr",
                        lambda key, *a, **k: "" if key == "DISK_SPACE_CHECK_PATH" else real(key, *a, **k))
    status, detail = result(doctor.run(), "free-space limit")
    assert status == "PASS" and detail.startswith(f"1000 bytes, measured at {save}")


def test_a_fansly_profile_needs_no_key_files(home):
    write_config(home, {"cdm_options": {"key-mode-default": "manual"}})
    folder = config_dir(home) / "main_profile"
    folder.mkdir(parents=True)
    (folder / "site.json").write_text('{"site": "fansly"}', encoding="utf-8")
    assert result(doctor.run(), "key files")[0] == "SKIP"


@pytest.mark.parametrize("fields, named", [
    ({"sess": "   "}, "blank in"),
    ({"auth_id": 12345}, "not text in"),
    ({"x-bc": None}, "blank in"),
])
def test_auth_values_a_run_cant_use_fail_offline(home, fields, named):
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home, **fields)
    status, detail = result(doctor.run(), "OnlyFans auth")
    assert status == "FAIL" and named in detail and detail.endswith(next(iter(fields)))


def test_an_ansi_config_and_auth_file_are_read(home, tmp_path, monkeypatch):
    import locale

    save = tmp_path / "Bibliothèque"
    save.mkdir()
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252")
    path = write_config(home, "")
    path.write_bytes(json.dumps({"file_options": {"save_location": str(save)}}, ensure_ascii=False).encode("cp1252"))
    auth = write_auth(home)
    auth.write_bytes(auth.read_bytes().replace(b"a test agent", "agent é".encode("cp1252")))
    report = doctor.run()
    assert result(report, "config file")[0] == "PASS"
    assert result(report, "save folder")[0] == "PASS"
    assert result(report, "OnlyFans auth")[0] == "PASS"


def test_a_working_configured_ffmpeg_doesnt_ask_pyffmpeg(monkeypatch):
    class Pyffmpeg:
        def FFmpeg(self):
            pytest.fail("asked pyffmpeg")

    monkeypatch.setitem(sys.modules, "pyffmpeg", Pyffmpeg())
    monkeypatch.setattr(ffmpeg, "_is_valid_ffmpeg", lambda p: p == "/opt/ffmpeg")
    monkeypatch.setattr(ffmpeg, "ffmpeg_version", lambda p: (True, "7.1", 7))
    assert ffmpeg.find_ffmpeg("/opt/ffmpeg") == ("/opt/ffmpeg", "config", "7.1")


def test_online_json_is_the_only_thing_on_stdout(home, monkeypatch, capsys):
    import ofscraper.data.api.init as init

    write_config(home, {"main_profile": "main_profile"})
    write_auth(home)
    monkeypatch.setattr(doctor, "start", lambda *a: print("a config was made"))
    monkeypatch.setattr(init, "auth_status", lambda: ("UP", None))
    doctor.main(["--online", "--json"])
    captured = capsys.readouterr()
    assert json.loads(captured.out)["results"]
    assert "a config was made" in captured.err


def test_key_mode_cdrm_with_a_server_of_your_own(home, monkeypatch):
    """OFSC_CDRM_URL set to a server other than the retired one: keys are
    asked from it, so the warning says that and not that cdrm can't work."""
    import ofscraper.utils.of_env.of_env as of_env

    url = f"http://user:{SECRET}@127.0.0.1:8080/api/decrypt"
    real = of_env.getattr
    monkeypatch.setattr(of_env, "getattr", lambda key, *a, **k: url if key == "CDRM" else real(key, *a, **k))
    write_config(home, {"cdm_options": {"key-mode-default": "cdrm"}})
    status, detail = result(doctor.run(), "key files")
    assert status == "WARN" and "doesn't work" not in detail
    assert "127.0.0.1" in detail and "cookies" in detail and SECRET not in detail


def test_key_files_that_name_a_folder(home, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "client_id.bin").write_bytes(b"c")
    (keys / "private_key.pem").write_bytes(b"k")
    status, detail = manual_keys(home, keys, keys / "private_key.pem")
    assert status == "FAIL"
    assert "client-id is a folder, not a file: client_id.bin is in it" in detail


def test_key_files_named_in_another_case(home, tmp_path):
    (tmp_path / "Client_ID.bin").write_bytes(b"c")
    (tmp_path / "DEVICE_PRIVATE_KEY").write_bytes(b"k")
    status, detail = manual_keys(home, tmp_path / "client_id.bin", tmp_path / "private_key.pem")
    assert status == "FAIL"
    assert "client-id: no file at that name, but Client_ID.bin is in that folder" in detail
    assert "private-key: no file at that name, but DEVICE_PRIVATE_KEY is in that folder" in detail


def test_a_json_key_file_with_a_bom(home, tmp_path):
    client, key = tmp_path / "client_id.bin", tmp_path / "private_key.pem"
    client.write_bytes(b"\xef\xbb\xbf  {\"client_id\": \"abc\"}")
    key.write_bytes(b"not a key")
    status, detail = manual_keys(home, client, key)
    assert status == "FAIL" and "client-id looks like a JSON file, not a key file" in detail


def test_spaces_inside_a_token_are_named(home):
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home, sess=SECRET[:10] + " " + SECRET[10:], auth_id="123 45")
    report = doctor.run()
    status, detail = result(report, "OnlyFans auth values")
    assert status == "WARN" and "sess has spaces" in detail and "auth_id has spaces" in detail
    assert SECRET[10:] not in json.dumps(report.results)


@pytest.mark.parametrize("value", [12345, True, ["x"]])
def test_an_auth_value_that_isnt_text_fails_and_isnt_cleaned(home, value):
    write_config(home, {"main_profile": "main_profile"})
    write_auth(home, auth_id=value)
    report = doctor.run()
    assert result(report, "OnlyFans auth")[0] == "FAIL"
    assert "not text" in result(report, "OnlyFans auth")[1]
    assert not [r for r in report.results if r["check"] == "OnlyFans auth values"]

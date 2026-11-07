"""--block-ads is accepted, --env-files is loaded, --length-max/min take seconds (#201)."""

from types import SimpleNamespace

import click
import pytest

import ofscraper.utils.settings as settings
from ofscraper.utils.args.parse.commands.main import program


def _parse(args):
    params, _ = program.main(args=args, standalone_mode=False, prog_name="OF-Scraper")
    return params


def test_block_ads_is_accepted():
    assert _parse(["--block-ads"])["block_ads"] is True
    assert _parse([])["block_ads"] is False


def test_length_filters_take_plain_seconds():
    params = _parse(["--length-max", "90", "--length-min", "1.5"])
    assert params["length_max"] == 90
    assert params["length_min"] == 1.5


def test_length_filters_reject_size_suffixes():
    with pytest.raises(click.BadParameter):
        _parse(["--length-max", "1m"])


@pytest.fixture
def loaded(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "load_env_files", lambda files: calls.append(files))
    monkeypatch.setattr(settings.of_env, "get_all_configs", lambda **k: None)
    monkeypatch.setattr(settings, "merged_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(settings, "settings", None)
    return calls


def test_env_files_from_the_command_line_are_loaded(monkeypatch, loaded):
    monkeypatch.setattr(settings, "get_args", lambda *a, **k: SimpleNamespace(env_files=("cli.env",)))
    monkeypatch.setattr(settings.config_data, "get_env_files", lambda *a, **k: ["config.env"])
    settings.setup_settings()
    assert loaded == [["cli.env"]]


def test_config_env_files_are_loaded_without_the_option(monkeypatch, loaded):
    monkeypatch.setattr(settings, "get_args", lambda *a, **k: SimpleNamespace(env_files=()))
    monkeypatch.setattr(settings.config_data, "get_env_files", lambda *a, **k: "a.env, b.env")
    settings.setup_settings()
    assert loaded == [["a.env", "b.env"]]

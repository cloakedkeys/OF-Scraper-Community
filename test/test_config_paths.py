import pytest

import ofscraper.utils.config.data as data


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"C:\\keys\\private_key.pem"', "C:\\keys\\private_key.pem"),
        ("'/keys/private_key.pem'", "/keys/private_key.pem"),
        ('  "/keys/a b.pem"  ', "/keys/a b.pem"),
        ("/keys/private_key.pem", "/keys/private_key.pem"),
        ('"', '"'),
        (None, None),
    ],
)
def test_strip_path_quotes(raw, expected):
    assert data.strip_path_quotes(raw) == expected


def test_cdm_paths_are_unquoted():
    config = {
        "cdm_options": {
            "private-key": '"C:\\keys\\private_key.pem"',
            "client-id": '"C:\\keys\\client_id.bin"',
        }
    }
    assert data.get_private_key(config=config) == "C:\\keys\\private_key.pem"
    assert data.get_client_id(config=config) == "C:\\keys\\client_id.bin"


def test_ffmpeg_path_is_unquoted():
    config = {"binary_options": {"ffmpeg": '"C:\\ffmpeg\\bin\\ffmpeg.exe"'}}
    assert data.get_ffmpeg(config=config) == "C:\\ffmpeg\\bin\\ffmpeg.exe"


def test_empty_ffmpeg_falls_back_to_env_default(monkeypatch):
    monkeypatch.setattr(
        data.of_env,
        "getattr",
        lambda key, *a: "/opt/ffmpeg" if key == "FFMPEG_DEFAULT" else None,
    )
    assert data.get_ffmpeg(config={"binary_options": {"ffmpeg": ""}}) == "/opt/ffmpeg"

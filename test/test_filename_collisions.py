import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

import ofscraper.commands.scraper.actions.utils.paths as action_paths
import ofscraper.utils.paths.paths as path_utils


def _media(media_id):
    return SimpleNamespace(id=media_id, post_id=1, model_id=10, username="model")


@pytest.fixture(autouse=True)
def collision_mode(monkeypatch):
    mode = {"value": "media_id"}
    monkeypatch.setattr(
        action_paths.config_data, "get_filename_collision", lambda: mode["value"]
    )
    return mode


def _download(tmp_path, data, name="dl.part"):
    temp = tmp_path / name
    temp.write_bytes(data)
    return temp


def _move(tmp_path, target, media_id, data):
    return action_paths.moveHelper(_download(tmp_path, data), target, _media(media_id))


def test_free_path_is_used(tmp_path):
    target = tmp_path / "caption.jpg"
    assert _move(tmp_path, target, 1, b"media 1") == target
    assert target.read_bytes() == b"media 1"


def test_different_file_keeps_its_name(tmp_path):
    target = tmp_path / "caption.jpg"
    _move(tmp_path, target, 1, b"media 1")
    got = _move(tmp_path, target, 2, b"media 2, larger than media 1")
    assert got == tmp_path / "caption_2.jpg"
    assert target.read_bytes() == b"media 1"
    assert got.read_bytes() == b"media 2, larger than media 1"


def test_identical_file_is_this_download(tmp_path):
    # a re-download (e.g. --force-all) of the same content
    target = tmp_path / "caption.jpg"
    _move(tmp_path, target, 1, b"media 1")
    temp = _download(tmp_path, b"media 1")
    assert action_paths.moveHelper(temp, target, _media(1)) == target
    assert not temp.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["caption.jpg"]


def test_stable_across_runs(tmp_path):
    target = tmp_path / "caption.jpg"
    first = [_move(tmp_path, target, 1, b"media 1"), _move(tmp_path, target, 2, b"media 2")]
    again = [_move(tmp_path, target, 2, b"media 2"), _move(tmp_path, target, 1, b"media 1")]
    assert again == first[::-1]
    assert len(list(tmp_path.iterdir())) == 2


def test_fallback_holding_another_file_is_skipped(tmp_path):
    # media 3's own file sits at caption_2.jpg, media 2's first fallback
    (tmp_path / "caption.jpg").write_bytes(b"media 1")
    (tmp_path / "caption_2.jpg").write_bytes(b"media 3")
    got = _move(tmp_path, tmp_path / "caption.jpg", 2, b"media 2, larger than media 3")
    assert got == tmp_path / "caption_2_2.jpg"
    assert (tmp_path / "caption_2.jpg").read_bytes() == b"media 3"


def test_old_collision_is_recovered_by_redownloading(tmp_path):
    # the old behaviour left media 9's file where media 5 was also recorded
    target = tmp_path / "caption.jpg"
    target.write_bytes(b"media 9, the larger image")
    assert _move(tmp_path, target, 9, b"media 9, the larger image") == target
    got = _move(tmp_path, target, 5, b"media 5")
    assert got == tmp_path / "caption_5.jpg" and got.read_bytes() == b"media 5"


def test_concurrent_moves_to_one_name_keep_every_file(tmp_path):
    target = tmp_path / "caption.jpg"
    temps = [_download(tmp_path, f"media {i}".encode(), f"{i}.part") for i in range(1, 9)]
    results = [None] * 8

    def move(i):
        results[i] = action_paths.moveHelper(temps[i], target, _media(i + 1))

    threads = [threading.Thread(target=move, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 8
    assert sorted(p.read_bytes() for p in results) == sorted(f"media {i}".encode() for i in range(1, 9))


def test_overwrite_setting_keeps_old_behaviour(tmp_path, collision_mode):
    collision_mode["value"] = "overwrite"
    target = tmp_path / "caption.jpg"
    _move(tmp_path, target, 1, b"media 1")
    assert _move(tmp_path, target, 2, b"media 2, larger") == target
    assert target.read_bytes() == b"media 2, larger"
    # and the smaller download is dropped, as before
    assert _move(tmp_path, target, 3, b"tiny") == target
    assert target.read_bytes() == b"media 2, larger"


def test_suffix_survives_truncation(tmp_path):
    long = tmp_path / ("x" * 400 + ".mp4")
    out = path_utils.truncate(action_paths.with_media_id(long, 3784819492))
    assert out.name.endswith("_3784819492.mp4")


def test_case_variant_follows_the_filesystem(tmp_path):
    existing = tmp_path / "CaseName.JPG"
    existing.write_bytes(b"media 1")
    same_file = (tmp_path / "casename.jpg").exists()
    got = _move(tmp_path, tmp_path / "casename.jpg", 2, b"media 2")
    assert got == (tmp_path / "casename_2.jpg" if same_file else tmp_path / "casename.jpg")
    assert existing.read_bytes() == b"media 1"


@pytest.fixture
def case_sensitive_dir(tmp_path):
    """A Windows directory with per-directory case sensitivity (as WSL uses)."""
    if sys.platform != "win32":
        pytest.skip("per-directory case sensitivity is a Windows feature")
    d = tmp_path / "cs"
    d.mkdir()
    r = subprocess.run(
        ["fsutil", "file", "setCaseSensitiveInfo", str(d), "enable"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.skip(f"can't enable case sensitivity here: {r.stdout or r.stderr}")
    return d


@pytest.mark.parametrize("populated", [False, True])
def test_case_sensitive_windows_dir(case_sensitive_dir, tmp_path, populated):
    # reviews on #116: distinct names in a case-sensitive directory, empty or not
    if populated:
        (case_sensitive_dir / "other.txt").write_bytes(b"x")
    upper, lower = case_sensitive_dir / "A.jpg", case_sensitive_dir / "a.jpg"
    assert _move(tmp_path, upper, 1, b"media 1") == upper
    assert _move(tmp_path, lower, 2, b"media 2") == lower
    assert not upper.samefile(lower)
    # next run: same names
    assert _move(tmp_path, lower, 2, b"media 2") == lower
    assert _move(tmp_path, upper, 1, b"media 1") == upper
    assert not (case_sensitive_dir / "a_2.jpg").exists()


def test_metadata_update_keeps_a_recorded_file_under_another_name(tmp_path, monkeypatch):
    # media 2 was saved as caption_2.jpg because caption.jpg held media 1;
    # update mode must not re-point it at caption.jpg
    import ofscraper.commands.metadata.manager as metadata_manager

    (tmp_path / "caption.jpg").write_bytes(b"media 1")
    (tmp_path / "caption_2.jpg").write_bytes(b"media 2")
    prev = {"directory": str(tmp_path), "filename": "caption_2.jpg"}
    manager = metadata_manager.MetaDataManager()
    for mode, expected in [("update", tmp_path / "caption_2.jpg"), ("check", None)]:
        monkeypatch.setattr(metadata_manager.settings, "get_settings", lambda: SimpleNamespace(metadata=mode))
        assert manager._metadata_recorded_file(prev) == expected
    monkeypatch.setattr(metadata_manager.settings, "get_settings", lambda: SimpleNamespace(metadata="update"))
    assert manager._metadata_recorded_file({"directory": str(tmp_path), "filename": "gone.jpg"}) is None


_MOVE_IN_PROCESS = """
import os, sys
from types import SimpleNamespace
temp, target, media_id, gate = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
sys.argv = sys.argv[:1]
import ofscraper.main.open.load  # import order, as in conftest
import ofscraper.commands.scraper.actions.utils.paths as ap
ap.config_data.get_filename_collision = lambda: "media_id"
open(gate + f".ready{media_id}", "w").close()
while not os.path.exists(gate):  # released together
    pass
media = SimpleNamespace(id=media_id, post_id=1, model_id=10, username="model")
print(ap.moveHelper(temp, target, media))
"""


def test_concurrent_processes_reserve_distinct_names(tmp_path):
    # reservation has to hold across processes, not just threads
    import os
    import pathlib
    import time

    target = tmp_path / "caption.jpg"
    temps = [_download(tmp_path, f"media {i}".encode(), f"{i}.part") for i in range(1, 7)]
    repo = pathlib.Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo)}
    gate = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _MOVE_IN_PROCESS, str(t), str(target), str(i + 1), str(gate)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=repo,
        )
        for i, t in enumerate(temps)
    ]
    deadline = time.time() + 90
    while len(list(tmp_path.glob("go.ready*"))) < len(procs):
        assert time.time() < deadline and all(p.poll() is None for p in procs), "a worker failed to start"
        time.sleep(0.05)
    gate.write_bytes(b"")
    results = []
    for p in procs:
        out, err = p.communicate(timeout=90)
        assert p.returncode == 0, err[-2000:]
        results.append(out.strip().splitlines()[-1])
    assert len(set(results)) == 6
    contents = sorted(open(r, "rb").read() for r in results)
    assert contents == sorted(f"media {i}".encode() for i in range(1, 7))


def test_another_downloads_reservation_is_left_alone(tmp_path):
    target = tmp_path / "caption.jpg"
    target.write_bytes(b"")  # reserved by another download, not filled yet
    got = _move(tmp_path, target, 2, b"media 2")
    assert got == tmp_path / "caption_2.jpg"
    assert target.read_bytes() == b""


def test_temp_on_another_drive_is_copied(tmp_path, monkeypatch):
    import errno
    import os

    def cross_device(src, dst):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "replace", cross_device)
    temp = _download(tmp_path, b"media 1")
    got = action_paths.moveHelper(temp, tmp_path / "caption.jpg", _media(1))
    assert got.read_bytes() == b"media 1" and not temp.exists()


def test_failed_move_removes_its_reservation(tmp_path, monkeypatch):
    import os

    def denied(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        _move(tmp_path, tmp_path / "caption.jpg", 1, b"media 1")
    assert not (tmp_path / "caption.jpg").exists()


def test_failed_move_leaves_other_files_alone(tmp_path, monkeypatch):
    # the planned name holds another file; only the fallback was reserved
    import os

    (tmp_path / "caption.jpg").write_bytes(b"media 1")

    def denied(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        _move(tmp_path, tmp_path / "caption.jpg", 2, b"media 2")
    assert (tmp_path / "caption.jpg").read_bytes() == b"media 1"
    assert not (tmp_path / "caption_2.jpg").exists()

"""Save folder guard: copied before a run, put back after; nothing is deleted."""

import json

from app.games.save_guard import PENDING_FILE, SaveGuard, recover_saves


def make_saves(tmp_path):
    saves = tmp_path / "Saves"
    (saves / "slot").mkdir(parents=True)
    (saves / "slot" / "save1.sav").write_bytes(b"mine")
    (saves / "profile.cfg").write_bytes(b"cfg")
    return saves


def test_the_run_changes_are_put_back_and_kept(tmp_path):
    saves = make_saves(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    guard = SaveGuard(123, saves, tmp_path / "run", data)
    guard.begin()
    assert json.loads((data / PENDING_FILE).read_text())["123"]["folder"] == str(saves)
    (saves / "slot" / "save1.sav").write_bytes(b"benchmark autosave")       # the game writes during the run
    (saves / "slot" / "auto2.sav").write_bytes(b"new")
    (saves / "profile.cfg").unlink()
    note = guard.finish()
    assert (saves / "slot" / "save1.sav").read_bytes() == b"mine" and (saves / "profile.cfg").read_bytes() == b"cfg"
    assert (tmp_path / "run" / "game_saves_after_run" / "slot" / "save1.sav").read_bytes() == b"benchmark autosave"
    assert (saves / "slot" / "auto2.sav").read_bytes() == b"new"            # left, and named
    assert "2 file(s) put back" in note and "slot/auto2.sav" in note
    assert not (data / PENDING_FILE).exists()


def test_an_untouched_folder_says_so(tmp_path):
    saves = make_saves(tmp_path)
    guard = SaveGuard(1, saves, tmp_path / "run", tmp_path)
    guard.begin()
    assert guard.finish() == "saves: unchanged by the run"


def test_a_missing_or_huge_folder_stops_the_run(tmp_path, monkeypatch):
    import pytest
    import app.games.save_guard as sg
    with pytest.raises(ValueError, match="not there"):
        SaveGuard(1, tmp_path / "nope", tmp_path / "run", tmp_path).begin()
    monkeypatch.setattr(sg, "LIMIT_BYTES", 3)
    with pytest.raises(ValueError, match="saves only"):
        SaveGuard(1, make_saves(tmp_path), tmp_path / "run", tmp_path).begin()
    assert not (tmp_path / PENDING_FILE).exists() and not (tmp_path / "run").exists()


def test_a_cut_off_run_is_put_back_at_the_next_start_unless_the_game_is_open(tmp_path):
    saves = make_saves(tmp_path)
    SaveGuard(5, saves, tmp_path / "run", tmp_path).begin()               # the app closed before finish()
    (saves / "slot" / "save1.sav").write_bytes(b"benchmark")
    assert recover_saves(tmp_path, running=lambda app_id: True) == []
    assert (saves / "slot" / "save1.sav").read_bytes() == b"benchmark"
    lines = recover_saves(tmp_path)
    assert "a run was cut off" in lines[0] and (saves / "slot" / "save1.sav").read_bytes() == b"mine"
    assert (tmp_path / "run" / "game_saves_before_recovery" / "slot" / "save1.sav").read_bytes() == b"benchmark"
    assert recover_saves(tmp_path) == []

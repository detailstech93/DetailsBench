"""A forced game resolution the screen does not offer is refused before the launch (owner, 2026-10-23: Cyberpunk
2077 asked 2560x1440 on a 1920x1080 screen ran the whole benchmark at 1024x768 and was stored as invalid)."""

import json

from app.games import screen_modes
from app.games.settings_file import ForcedSettings
from app.simulation.script import settings_problem
from tests.test_game_settings_file import APP, env, option  # noqa: F401  (env is a fixture)

SCREEN = {(1280, 720), (1920, 1080), (1600, 1024)}


def test_refusal_names_the_screen_s_largest_mode():
    assert screen_modes.parse("2560x1440") == (2560, 1440) and screen_modes.parse(" 1920 × 1080 ") == (1920, 1080)
    assert screen_modes.parse("Native") is None
    why = screen_modes.refusal("2560x1440", SCREEN)
    assert "does not offer 2560x1440" in why and "largest mode is 1920x1080" in why
    assert screen_modes.refusal("1920x1080", SCREEN) is None
    assert screen_modes.refusal("2560x1440", None) is None          # unknown: the check after the run is left
    assert screen_modes.refusal("Native", SCREEN) is None


def test_the_game_does_not_start_with_a_resolution_the_screen_lacks(env, monkeypatch):
    e, path, data_dir, run_dir = env
    before = path.read_bytes()
    monkeypatch.setattr(screen_modes, "resolutions", lambda: SCREEN)
    rec = ForcedSettings(APP, {"resolution": "2560x1440", "dlss_frame_gen": False}, run_dir, data_dir, env=e).apply()
    assert "does not offer 2560x1440" in rec.refused["resolution"] and rec.changed == ["dlss_frame_gen"]
    assert settings_problem(rec).startswith("settings not set: resolution: the screen does not offer")
    assert option(path, "Resolution")["value"] == "2560x1440" and path.read_bytes() != before   # only frame gen written


def test_an_offered_resolution_goes_on(env, monkeypatch):
    e, path, data_dir, run_dir = env
    monkeypatch.setattr(screen_modes, "resolutions", lambda: SCREEN | {(2560, 1440)})
    rec = ForcedSettings(APP, {"resolution": "2560x1440"}, run_dir, data_dir, env=e).apply()
    assert rec.refused == {} and settings_problem(rec) is None
    assert json.loads(path.read_text(encoding="utf-8"))


def test_this_pc_s_modes_are_read_only_on_windows(monkeypatch):
    monkeypatch.setattr(screen_modes.sys, "platform", "linux")
    assert screen_modes.resolutions() is None and screen_modes.refresh_rates() is None

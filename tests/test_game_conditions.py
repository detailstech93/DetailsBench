"""Far Cry 6 settings, the test conditions a game run keeps, and the richer game report (owner, 2026-10-23)."""

from types import SimpleNamespace

from app.automation.workload import WorkloadResult
from app.games import far_cry6 as fc6
from app.games.presets import GamePreset, plan_preset
from app.games.settings_file import game_conditions
from app.report import render_session_report

PROFILE = ('<GamerProfile>\n<RenderProfile GPU="0" VideoModeProfile="1" Quality="high" ResolutionScale="1" '
           'DxrReflections="0" DxrShadows="0" VSyncPreset="off">\n'
           '<VideoModeProfileFullscreen adapterIndex="0" windowMode="0" width="1920" height="1080" />\n'
           '<VideoModeProfileWindow windowMode="3" width="1920" height="1080" />\n</RenderProfile>\n'
           '<CustomQuality>\n<quality ShadowQuality="high" id="custom" />\n</CustomQuality>\n'
           '<ResolutionConfig FidelityFxSuperResolutionEnabled="0" FidelityFxSuperResolutionMode="0" />\n'
           '</GamerProfile>\n')


def test_far_cry_6_reads_and_changes_only_its_values():
    assert fc6.read_settings(PROFILE) == {
        "resolution": "1920x1080", "window_mode": "1", "preset": "High", "dxr_reflections": False,
        "dxr_shadows": False, "upscaler": "Off", "resolution_scale": "1", "vsync": "off"}
    wanted = GamePreset(ray_tracing="on", quality="ultra", upscaler="fsr", frame_gen="x2")
    # the game's Ultra is "ultrahigh" (seen 2026-10-23; a guessed "ultra" made it fall back to Low)
    requested, skipped = plan_preset(2369390, wanted, fc6.choices(PROFILE, {}))
    assert requested == {"dxr_reflections": True, "dxr_shadows": True, "preset": "Ultra", "upscaler": "FSR 1.0"}
    assert skipped == {"frame_gen": "the game has none"}
    dlss = plan_preset(2369390, GamePreset(upscaler="dlss"), fc6.choices(PROFILE, {}))[1]
    assert dlss == {"upscaler": "the game has no such value; it offers: FSR 1.0, Off"}
    known = {}
    changes, refused = fc6.plan(PROFILE, requested, known)
    new = fc6.apply(PROFILE, changes)
    assert refused == {} and fc6.verify(new, requested) == {}
    assert new == (PROFILE.replace('Quality="high" ', 'Quality="ultrahigh" ', 1).replace('Reflections="0"', 'Reflections="1"')
                   .replace('DxrShadows="0"', 'DxrShadows="1"').replace('SuperResolutionEnabled="0"',
                                                                         'SuperResolutionEnabled="1"'))
    assert 'ShadowQuality="high"' in new                # the custom quality's own "Quality" names are not touched


def test_far_cry_6_resolution_only_in_full_screen():
    windowed = PROFILE.replace('VideoModeProfile="1"', 'VideoModeProfile="0"')
    known = {"resolution": {"2560x1440": 0}}
    assert fc6.plan(PROFILE, {"resolution": "2560x1440"}, known)[1] == {}
    changes, refused = fc6.plan(windowed, {"resolution": "2560x1440"}, known)
    assert changes == [] and "not in full screen" in refused["resolution"]


def test_the_conditions_come_from_the_games_file_and_the_screen(tmp_path):
    folder = tmp_path / "My Games" / "Far Cry 6"
    folder.mkdir(parents=True)
    (folder / "gamerprofile.xml").write_text(PROFILE.replace('DxrShadows="0"', 'DxrShadows="1"'), encoding="utf-8")
    items = game_conditions(2369390, "1920x1080 at 60 Hz", env={"PCBM_DOCUMENTS": str(tmp_path)})
    assert items[:4] == ["Screen mode during the benchmark: 1920x1080 at 60 Hz", "Resolution: 1920x1080",
                         "Window mode: Full screen", "Quality preset: High"]
    assert "Ray traced shadows: On" in items and "Upscaler: Off" in items
    assert "Frame generation: the game has none" in items
    unknown = game_conditions(286690, None, env={})            # Metro 2033 Redux: its benchmark has its own window
    assert unknown[0].startswith("Resolution: not read") and len(unknown) == 4


def test_the_conditions_go_on_completed_runs_only():
    from app.ui.frame_capture_panel import add_conditions
    done = SimpleNamespace(result=WorkloadResult("completed", score=60.0, note="Proof screenshot: x.png"))
    bad = SimpleNamespace(result=WorkloadResult("invalid", error="no"))
    add_conditions(SimpleNamespace(windows=[done, bad]), ["Resolution: 1920x1080"], {"preset": "ultra"})
    assert done.result.note == "Proof screenshot: x.png; Settings used: Resolution: 1920x1080; Forced: preset ultra"
    assert bad.result.note is None


def test_the_game_report_shows_conditions_the_results_screen_and_charts(tmp_path):
    shot = tmp_path / "benchmark_result.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    run = {"benchmark_name": "fc6", "benchmark_type": "game", "status": "completed", "score": 71.0, "unit": "FPS",
           "run_index": 1, "is_warmup": False, "is_test": False,
           "note": f"Proof screenshot: {shot}; Settings used: Screen mode during the benchmark: 1920x1080 at 60 Hz, "
                   "Ray tracing: not in the game's file",
           "series": {"fps": {"unit": "FPS", "points": [[1, 70], [2, 72], [3, 71]]},
                      "tel_gpu0_temperature": {"unit": "C", "points": [[0, 60], [1, 65]]},
                      "tel_cpu_load": {"unit": "%", "points": [[0, 40], [1, 45]]}}}
    export = {"session": {"id": 1, "name": "s"}, "runs": [run],
              "summaries": {"fc6": {"included": 1, "stats": {"mean": 71.0}}}, "metric_summaries": {},
              "manifest": {"benchmarks": [{"name": "fc6", "display_name": "Far Cry 6"}]}}
    page = render_session_report(export)
    game = page[page.index("Game results"):]
    assert "<h4>Test conditions</h4>" in game
    assert "<tr><th>Screen mode during the benchmark</th><td>1920x1080 at 60 Hz</td></tr>" in game
    assert "The game&#x27;s own result" in game or "The game's own result" in game
    assert "Frames per second" in game and "Temperature" in game and "Load" in game


GAME_REPORT = ('<div class = "option"> <div class = "option_name">Average FPS:</div>\n'
               '<div class = "option_value avg">129</div></div>\n'
               '<div class = "option_name">Window Mode:</div> <div class = "option_value">Fullscreen</div>\n'
               '<div class = "option_name">Display Resolution:</div> <div class = "option_value">1920 x 1080</div>\n'
               '<div class = "option_name">Graphics Quality:</div> <div class = "option_value">Low</div>\n')


def _game_folder(tmp_path, report=GAME_REPORT, profile=PROFILE):
    folder = tmp_path / "My Games" / "Far Cry 6"
    (folder / "benchmarks" / "benchmark 2026-10-23_13-16-08").mkdir(parents=True)
    (folder / "gamerprofile.xml").write_text(profile, encoding="utf-8")
    if report:
        (folder / "benchmarks" / "benchmark 2026-10-23_13-16-08" / "result.html").write_text(report, encoding="utf-8")
    return folder


def test_the_games_own_report_is_read_and_compared(tmp_path):
    folder = _game_folder(tmp_path)
    report = fc6.game_report(folder / "gamerprofile.xml")
    assert report == {"Average FPS": "129", "Window Mode": "Fullscreen", "Display Resolution": "1920 x 1080",
                      "Graphics Quality": "Low"}
    assert fc6.game_report(folder / "gamerprofile.xml", since=4102444800) is None     # older than the run
    assert fc6.report_mismatches(report, {"preset": "Ultra", "resolution": "1920x1080", "window_mode": "1",
                                          "dxr_shadows": True}) == {"preset": ("Ultra", "Low")}


def test_a_setting_the_game_ignored_fails_the_check_after_the_run(tmp_path):
    """The game ran at Low while its file still held what was written: the file alone passed (2026-10-23)."""
    _game_folder(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    from app.games.settings_file import ForcedSettings
    forced = ForcedSettings(2369390, {"preset": "Ultra"}, tmp_path / "run", data,
                            env={"PCBM_DOCUMENTS": str(tmp_path)})
    forced.apply()
    forced.started = 0                                   # the report above counts as this run's
    rec = forced.finish()
    assert rec.mismatch_after_run == {"preset": ["Ultra", "Low"]} and not rec.verified and rec.restored


def test_the_conditions_come_from_the_games_own_report(tmp_path):
    _game_folder(tmp_path)
    items = game_conditions(2369390, None, env={"PCBM_DOCUMENTS": str(tmp_path)}, since=0)
    assert "Graphics Quality (the game's report): Low" in items
    assert "Display Resolution (the game's report): 1920 x 1080" in items
    assert not any(i.startswith("Quality preset:") for i in items)       # the file's value is not what it used


def test_the_games_own_numbers_are_read_from_its_report(tmp_path):
    from app.games.settings_file import game_own_result
    _game_folder(tmp_path, report=GAME_REPORT.replace("Average FPS:</div>", "Average FPS:</div>") + (
        '<div class = "option_name">Minimum FPS:</div> <div class = "option_value min">109</div>\n'
        '<div class = "option_name">Maximum FPS:</div> <div class = "option_value">175</div>\n'))
    assert game_own_result(2369390, 0, env={"PCBM_DOCUMENTS": str(tmp_path)}) == {
        "avg": 129.0, "min": 109.0, "max": 175.0}
    assert game_own_result(2369390, 4102444800, env={"PCBM_DOCUMENTS": str(tmp_path)}) is None
    assert game_own_result(750920, 0, env={}) is None               # a game whose report the app cannot read

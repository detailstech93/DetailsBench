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
    requested, skipped = plan_preset(2369390, GamePreset(ray_tracing="on", quality="ultra", upscaler="fsr",
                                                         frame_gen="x2"), fc6.choices(PROFILE, {}))
    assert requested == {"dxr_reflections": True, "dxr_shadows": True, "preset": "Ultra", "upscaler": "FSR 1.0"}
    assert "frame_gen" in skipped
    changes, refused = fc6.plan(PROFILE, requested, {})
    new = fc6.apply(PROFILE, changes)
    assert refused == {} and fc6.verify(new, requested) == {}
    assert new == (PROFILE.replace('Quality="high" ', 'Quality="ultra" ', 1).replace('Reflections="0"', 'Reflections="1"')
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
    unknown = game_conditions(750920, None, env={})
    assert unknown[0].startswith("Resolution: not read") and len(unknown) == 4


def test_the_conditions_go_on_completed_runs_only():
    from app.ui.frame_capture_panel import add_conditions
    done = SimpleNamespace(result=WorkloadResult("completed", score=60.0, note="Proof screenshot: x.png"))
    bad = SimpleNamespace(result=WorkloadResult("invalid", error="no"))
    add_conditions(SimpleNamespace(windows=[done, bad]), ["Resolution: 1920x1080"], {"preset": "Ultra"})
    assert done.result.note == "Proof screenshot: x.png; Settings used: Resolution: 1920x1080; Forced: preset Ultra"
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

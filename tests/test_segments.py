import pytest

from app.frames.segments import (
    first_frame_offset,
    game_seconds,
    pick_game_process,
    slice_samples,
    split_frames,
    window_bounds,
)


def test_window_bounds_example():
    assert window_bounds(10, 30, 3) == [(10, 40), (40, 70), (70, 100)]


def test_window_bounds_single_window():
    assert window_bounds(0, 1, 1) == [(0, 1)]


def test_window_bounds_float_values():
    assert window_bounds(2.5, 0.5, 2) == [(2.5, 3.0), (3.0, 3.5)]


def test_window_bounds_rejects_negative_settle():
    with pytest.raises(ValueError):
        window_bounds(-1, 10, 1)


def test_window_bounds_rejects_zero_segment():
    with pytest.raises(ValueError):
        window_bounds(0, 0, 1)


def test_window_bounds_rejects_count_zero():
    with pytest.raises(ValueError):
        window_bounds(0, 10, 0)


def test_game_seconds_empty():
    assert game_seconds([]) == 0.0


def test_game_seconds_sum():
    assert game_seconds([1000, 250, 0.5]) == 1.2505


def test_split_frames_example():
    frametimes = [500] * 10
    windows = [(1, 3), (3, 5), (5, 7)]
    expected = [[500, 500, 500, 500], [500, 500, 500, 500], None]
    assert split_frames(frametimes, windows) == expected


def test_split_frames_none_when_window_end_not_reached():
    frametimes = [1000, 1000]
    windows = [(0, 1), (1, 2), (2, 3)]
    assert split_frames(frametimes, windows) == [[1000], [1000], None]


def test_split_frames_empty_window_when_reached_but_no_frame_ends_inside():
    assert split_frames([3000], [(1, 2)]) == [[]]


def test_split_frames_drops_frames_before_and_after_window():
    frametimes = [500, 500, 500, 500, 500]
    assert split_frames(frametimes, [(1, 2)]) == [[500, 500]]


def test_slice_samples_example():
    samples = [
        (0.0, {"x": 1}),
        (5.0, {"x": 2}),
        (6.5, {"x": 3}),
        (9.0, {"x": 4}),
    ]
    windows = [(3, 5), (5, 8)]
    expected = [
        [(0.0, {"x": 2}), (1.5, {"x": 3})],
        [(2.0, {"x": 4})],
    ]
    assert slice_samples(samples, 2.0, windows) == expected


def test_slice_samples_empty_window():
    samples = [(1.0, {"a": 1})]
    assert slice_samples(samples, 0.0, [(2, 3)]) == [[]]


def test_slice_samples_keeps_same_readings_object():
    readings = {"v": 1}
    samples = [(10.0, readings)]
    result = slice_samples(samples, 5.0, [(5, 6)])
    assert result[0][0][1] is readings


def test_slice_samples_boundary_inclusive_start_exclusive_end():
    samples = [(5.0, {"a": 1}), (6.0, {"b": 2})]
    assert slice_samples(samples, 0.0, [(5, 6)]) == [[(0.0, {"a": 1})]]


def test_pick_game_process_case_insensitive_most_rows():
    summary = [
        {"application": "launcher.exe", "process_id": 1, "rows": 10},
        {"application": "GAME.EXE", "process_id": 2, "rows": 100},
        {"application": "game.exe", "process_id": 3, "rows": 50},
    ]
    assert pick_game_process(summary, {"Game.exe"}) == ("GAME.EXE", 2)


def test_pick_game_process_tie_prefers_first():
    summary = [
        {"application": "game.exe", "process_id": 1, "rows": 100},
        {"application": "GAME.EXE", "process_id": 2, "rows": 100},
    ]
    assert pick_game_process(summary, ["Game.exe"]) == ("game.exe", 1)


def test_pick_game_process_no_match():
    summary = [{"application": "other.exe", "process_id": 1, "rows": 10}]
    assert pick_game_process(summary, {"game.exe"}) is None


def test_pick_game_process_skips_bad_items():
    summary = [
        "not a dict",
        {"application": 123, "process_id": 1, "rows": 100},
        {"application": "game.exe", "process_id": 2, "rows": "100"},
        {"application": "game.exe", "process_id": 3, "rows": 10},
    ]
    assert pick_game_process(summary, {"Game.exe"}) == ("game.exe", 3)


def test_pick_game_process_missing_process_id():
    summary = [{"application": "game.exe", "rows": 10}]
    assert pick_game_process(summary, {"Game.exe"}) == ("game.exe", None)


def test_first_frame_offset_example():
    assert first_frame_offset(20.0, 12.5) == 7.5


def test_first_frame_offset_negative():
    assert first_frame_offset(10.0, 12.5) == -2.5


def test_the_start_click_measures_from_the_load_after_it_to_the_next_one():
    from app.frames.segments import loading_bounds
    from data.game_start_keys import starts_after_loading
    # menu with its own load, the Start click at 5 s, the benchmark's load (two frames, as BL3's 223 and 244 ms)
    # at 10 s, 100 s of benchmark, the load back to the menu
    frames = [250.0] + [10.0] * 1000 + [300.0, 10.0, 240.0] + [5.0] * 20000 + [220.0] + [3.0] * 1000
    start, end = loading_bounds(frames, after_seconds=5.0)
    assert start == pytest.approx(10.8)
    assert end == pytest.approx(10.8 + 100.0 - 3.0)
    assert loading_bounds(frames, after_seconds=5.0, within_seconds=4.0) == (None, None)    # no load: no window
    assert loading_bounds([5.0] * 100, 0.3) == (None, None)
    # NucBox K10, 2026-11-13: a lone stutter before 95 % of the benchmark is not its end; the fade after it is
    stutter = frames[:1004] + [5.0] * 18000 + [288.0] + [5.0] * 2000 + [220.0] + [3.0] * 1000
    assert loading_bounds(stutter, 5.0)[1] == pytest.approx(10.8 + 90.0 - 3.0)
    assert loading_bounds(stutter, 5.0, min_seconds=95.0)[1] == pytest.approx(10.8 + 100.288 - 3.0)
    assert starts_after_loading(397540) and not starts_after_loading(1174180)


def test_the_benchmark_is_its_scenes_between_fades_up_to_the_cut_to_the_results():
    """RDR2 on a NucBox K10, 2026-11-14 (session 18): a fade, then five scenes each after a 250 FPS fade; the
    last ends with a few 3-4 ms frames (the cut to the results screen). Its own average is the last scene only."""
    from app.frames.segments import faded_scenes
    menu, fade, results = [20.0] * 500, [4.0] * 1250, [40.0] * 750          # 10 s at 50 FPS, 5 s, 30 s
    split_fade = [4.0] * 500 + [40.0] * 50 + [4.0] * 750                     # 2 s slower frames inside a fade
    ramp = [4.0] * 5                                                         # a fade's last fast frames
    scenes = [[40.0] * 500, [32.0] * 625, [40.0] * 500, [32.0] * 625, [40.0] * 1000]   # 20 s each, last 40 s
    frames = menu + fade + scenes[0] + split_fade + ramp + scenes[1]
    for one in scenes[2:]:
        frames += fade + one
    frames += [4.0, 3.5] + results
    found, start, end = faded_scenes(frames, 0.0, 5)
    assert [len(s) for s in found] == [500, 625, 500, 625, 1000] and set(found[1]) == {32.0}
    assert start == pytest.approx(10.0) and end == pytest.approx(10 + 5 + 20 + 7 + 0.02 + 20 + 25 + 25 + 45)
    all_fps = 1000 * sum(map(len, found)) / sum(map(sum, found))
    assert all_fps == pytest.approx(3250 / 120, rel=1e-6)                   # the fades and the menu left out
    for after in (5.0, 9.9):                                                 # where the window happens to begin
        assert faded_scenes(frames, after, 5)[1:] == (start, end)
    # session 22: 'stop' read 45 s after the first fade; 30 s back finds 4 scenes, 60 s back all 5
    assert faded_scenes(frames, 55.0 - 30, 5) is None and faded_scenes(frames, 55.0 - 60, 5)[1:] == (start, end)
    assert faded_scenes(frames, 0.0, 6) is None                              # a scene the benchmark lacks
    assert faded_scenes(frames[: len(frames) - 760], 0.0, 5) is None         # the recording ended in the last scene
    assert faded_scenes([20.0] * 5000 + frames, 0.0, 5) is None              # no fade near the mark


def test_rdr2_says_its_own_average_is_its_last_scene():
    from data.game_start_keys import scene_fades
    assert scene_fades(1174180) == {"scenes": 5, "own_is_last_scene": True} and scene_fades(397540) is None


def test_the_last_scene_is_checked_against_the_games_own_average():
    from app.automation.workload import WorkloadResult
    from app.frames.game_benchmark import GameBenchmark
    r = WorkloadResult(status="completed", score=27.1)
    text = GameBenchmark._last_scene_check(r, [38.0] * 1000, {"avg": 26.34})         # 26.32 FPS
    assert "26.32 FPS, the game 26.34 (-0.1%: they match)" in text and r.metrics["last_scene_fps"] == (26.32, "FPS")
    assert "DO NOT MATCH" in GameBenchmark._last_scene_check(r, [30.0] * 1000, {"avg": 26.34})
    assert GameBenchmark._last_scene_check(r, [38.0] * 1000, None).endswith("26.32 FPS")   # its number not read


def test_borderlands3_own_result_is_read_from_its_benchmark_report(tmp_path):
    """NucBox K10, session 19 (2026-11-14): BenchmarkData_<time>.txt says 31.13, its .csv lists every frame."""
    import os
    from app.games import borderlands3 as bl3
    settings = tmp_path / "Saved" / "Config" / "WindowsNoEditor" / "GameUserSettings.ini"
    data = tmp_path / "Saved" / "BenchmarkData"
    data.mkdir(parents=True)
    old = data / "BenchmarkData_2026-11-13_16-22-21.txt"
    old.write_text("BenchmarkResults  x\n - FramesPerSecondAvg:   99.00\n", encoding="utf-8")
    os.utime(old, (1000, 1000))
    new = data / "BenchmarkData_2026-11-14_15-09-07.txt"
    new.write_text("BenchmarkResults                    2026-11-14_15-09-07\n - FramesPerSecondAvg:              31.13\n"
                   " - FrameTimeMsAvg:                  32.12\nGPU:                                Intel(R) UHD Graphics\n",
                   encoding="utf-8")
    rows = ["TimeInSeconds,FrameTimeMs,FPS"] + [f"{i * 0.032:.5f},32.00,31.25" for i in range(100)]
    new.with_suffix(".csv").write_text("\n".join(rows), encoding="utf-8")       # 3.2 s: 32, 31, 31 and a partial
    report = bl3.game_report(settings, since=2000)
    assert report["GPU"] == "Intel(R) UHD Graphics"
    assert bl3.own_numbers(report) == {"avg": 31.13, "min": 31.0, "max": 32.0}
    assert bl3.game_report(settings, since=time_far_future()) is None and bl3.report_conditions(report) == []
    assert bl3.own_numbers({"FramesPerSecondAvg": "31.13"}) == {"avg": 31.13}        # no frame list


def time_far_future():
    import time
    return time.time() + 3600


def test_sottr_own_result_is_its_average_benchmark_statistics(tmp_path):
    """NucBox K10, 2026-11-13: SOTTR_<PC>_<date>.txt says Average FPS 32.7; its Min FPS 0.0 is not taken."""
    import os
    from app.games import shadow_tomb_raider as sottr
    folder = tmp_path / "Shadow of the Tomb Raider"
    folder.mkdir()
    (folder / "SOTTR_PC_2026-11-13_22.35.45.txt").write_text(
        "Raw Benchmark Statistics for 1 run:\n\n\tBenchmark Statistics (Run No. 1):\n\t\tMin FPS: 0.0\n"
        "\t\tMax FPS: 98.7\n\t\tAverage FPS: 33.1\n\t\tNum Frames: 5145\n\n"
        "Average Benchmark Statistics for 1 runs with 0 extremes excluded:\n\tMin FPS: 0.0\n\tMax FPS: 98.7\n"
        "\tAverage FPS: 32.7\n\n\nSettings:\n\nFullscreen=true\n")
    (folder / "SOTTR_PC_2026-11-13_22.35.45_frametimes_1.txt").write_bytes(b"\x00\x01")
    env = {"PCBM_DOCUMENTS": str(tmp_path)}
    report = sottr.game_report(None, None, env)
    assert sottr.own_numbers(report) == {"avg": 32.7, "frames": 5145}
    assert sottr.game_report(None, time_far_future(), env) is None
    assert sottr.report_conditions(report) == []

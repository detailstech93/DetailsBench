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


def test_a_late_mark_measures_from_the_loading_frame_to_the_next_one():
    from app.frames.segments import loading_bounds
    from data.game_start_keys import starts_after_loading
    # menu with its own load, the benchmark's load at 10.3 s, 100 s of benchmark, the load back to the menu
    frames = [250.0] + [10.0] * 1000 + [300.0] + [5.0] * 20000 + [220.0] + [3.0] * 1000
    start, end = loading_bounds(frames, before_seconds=50.0)     # its "fps" was read 40 s late
    assert start == pytest.approx(10.55)
    assert end == pytest.approx(10.55 + 100.0 + 0.22 - 3.0)
    assert loading_bounds([5.0] * 100, 0.3) == (None, None)
    assert starts_after_loading(397540) and not starts_after_loading(1174180)

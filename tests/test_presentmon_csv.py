import pytest

from app.frames.presentmon_csv import (
    CaptureParse,
    parse_presentmon_csv,
    summarize_processes,
)


def test_parse_basic_single_process():
    text = (
        "Application,ProcessID,Runtime,Dropped,MsBetweenPresents\n"
        "game.exe,1234,DXGI,0,10.0\n"
        "game.exe,1234,DXGI,1,20.0\n"
    )

    result = parse_presentmon_csv(text)

    assert isinstance(result, CaptureParse)
    assert result.error is None
    assert result.frametimes_ms == [10.0, 20.0]
    assert result.frame_time_column == "MsBetweenPresents"
    assert result.application == "game.exe"
    assert result.process_id == 1234
    assert result.runtimes == ["DXGI"]
    assert result.rows_total == 2
    assert result.rows_used == 2
    assert result.rows_skipped == 0
    assert result.dropped == 1


def test_parse_case_insensitive_headers_and_values():
    text = (
        "application,processid,msbetweenpresents\n"
        "Game.EXE,1234,16.6\n"
    )

    result = parse_presentmon_csv(text, process_name="game.exe")

    assert result.error is None
    assert result.frame_time_column == "msbetweenpresents"
    assert result.application == "Game.EXE"
    assert result.process_id == 1234
    assert result.frametimes_ms == [16.6]


def test_parse_bom_is_removed():
    text = (
        "\ufeffApplication,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,10.0\n"
    )

    result = parse_presentmon_csv(text)

    assert result.error is None
    assert result.frame_time_column == "MsBetweenPresents"
    assert result.frametimes_ms == [10.0]


def test_parse_two_processes_without_filter_lists_both():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,10.0\n"
        "other.exe,5678,20.0\n"
    )

    result = parse_presentmon_csv(text)

    assert result.frametimes_ms == []
    assert result.rows_total == 2
    assert result.error == (
        "several processes in capture: game.exe (1234), other.exe (5678); select one"
    )


def test_parse_filter_by_name():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,10.0\n"
        "other.exe,5678,20.0\n"
    )

    result = parse_presentmon_csv(text, process_name="game.exe")

    assert result.error is None
    assert result.application == "game.exe"
    assert result.process_id == 1234
    assert result.frametimes_ms == [10.0]
    assert result.rows_total == 2
    assert result.rows_used == 1


def test_parse_filter_by_pid():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,10.0\n"
        "other.exe,5678,20.0\n"
    )

    result = parse_presentmon_csv(text, process_id=5678)

    assert result.error is None
    assert result.application == "other.exe"
    assert result.process_id == 5678
    assert result.frametimes_ms == [20.0]
    assert result.rows_total == 2
    assert result.rows_used == 1


def test_parse_filter_by_name_and_pid():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,10.0\n"
        "game.exe,4321,20.0\n"
    )

    result = parse_presentmon_csv(text, process_name="game.exe", process_id=4321)

    assert result.error is None
    assert result.application == "game.exe"
    assert result.process_id == 4321
    assert result.frametimes_ms == [20.0]
    assert result.rows_total == 2
    assert result.rows_used == 1


def test_parse_non_numeric_frame_time_is_skipped():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,10.0\n"
        "game.exe,1234,not-a-number\n"
        "game.exe,1234,\n"
    )

    result = parse_presentmon_csv(text)

    assert result.error is None
    assert result.frametimes_ms == [10.0]
    assert result.rows_used == 1
    assert result.rows_skipped == 2
    assert result.rows_total == 3


def test_parse_dropped_counting():
    text = (
        "Application,ProcessID,Dropped,MsBetweenPresents\n"
        "game.exe,1234,1,10.0\n"
        "game.exe,1234,0,20.0\n"
        "game.exe,1234,1,30.0\n"
    )

    result = parse_presentmon_csv(text)

    assert result.error is None
    assert result.dropped == 2
    assert result.rows_used == 3


def test_parse_missing_frame_time_column_error():
    text = (
        "Application,ProcessID\n"
        "game.exe,1234\n"
    )

    result = parse_presentmon_csv(text)

    assert result.frametimes_ms == []
    assert result.rows_total == 1
    assert result.error == "no frame time column (MsBetweenPresents or FrameTime)"


def test_parse_missing_application_and_process_id_error():
    text = (
        "MsBetweenPresents\n"
        "10.0\n"
    )

    result = parse_presentmon_csv(text)

    assert result.frametimes_ms == []
    assert result.rows_total == 1
    assert result.error == "no Application or ProcessID column"


def test_parse_empty_file_error():
    result = parse_presentmon_csv("")

    assert result.frametimes_ms == []
    assert result.rows_total == 0
    assert result.error == "empty file"


def test_parse_no_frames_for_selected_process():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,10.0\n"
    )

    result = parse_presentmon_csv(text, process_name="missing.exe")

    assert result.frametimes_ms == []
    assert result.rows_total == 1
    assert result.error == "no frames for the selected process"


def test_parse_no_frames_in_file_without_filter():
    text = "Application,ProcessID,MsBetweenPresents\n"

    result = parse_presentmon_csv(text)

    assert result.frametimes_ms == []
    assert result.rows_total == 0
    assert result.error == "no frames in file"


def test_parse_no_usable_frame_times():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,\n"
        "game.exe,1234,abc\n"
    )

    result = parse_presentmon_csv(text)

    assert result.frametimes_ms == []
    assert result.rows_used == 0
    assert result.rows_skipped == 2
    assert result.error == "no usable frame times"


def test_parse_frame_time_column_preference():
    text = (
        "Application,ProcessID,FrameTime,MsBetweenPresents\n"
        "game.exe,1234,99.0,10.0\n"
    )

    result = parse_presentmon_csv(text)

    assert result.error is None
    assert result.frame_time_column == "MsBetweenPresents"
    assert result.frametimes_ms == [10.0]


def test_parse_runtime_column_preference_and_sorted_runtimes():
    text = (
        "Application,ProcessID,Runtime,PresentRuntime,MsBetweenPresents\n"
        "game.exe,1234,RTX,DXGI,10.0\n"
        "game.exe,1234,DXGI,RTX,20.0\n"
    )

    result = parse_presentmon_csv(text)

    assert result.error is None
    assert result.runtimes == ["DXGI", "RTX"]


def test_parse_rows_shorter_than_header_treat_missing_cells_as_empty():
    text = (
        "Application,ProcessID,Runtime,MsBetweenPresents\n"
        "game.exe,1234\n"
        "game.exe,1234,DXGI,10.0\n"
    )

    result = parse_presentmon_csv(text)

    assert result.error is None
    assert result.frametimes_ms == [10.0]
    assert result.rows_used == 1
    assert result.rows_skipped == 1
    assert result.runtimes == ["DXGI"]


def test_parse_zero_and_negative_frame_times_are_kept():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "game.exe,1234,0\n"
        "game.exe,1234,-1.5\n"
    )

    result = parse_presentmon_csv(text)

    assert result.error is None
    assert result.frametimes_ms == [0.0, -1.5]
    assert result.rows_used == 2
    assert result.rows_skipped == 0


def test_summarize_processes_ordering():
    text = (
        "Application,ProcessID,MsBetweenPresents\n"
        "b.exe,2,10.0\n"
        "a.exe,1,10.0\n"
        "a.exe,1,20.0\n"
        "c.exe,3,30.0\n"
        "c.exe,3,40.0\n"
        "c.exe,3,50.0\n"
    )

    result = summarize_processes(text)

    assert result == [
        {"application": "c.exe", "process_id": 3, "rows": 3},
        {"application": "a.exe", "process_id": 1, "rows": 2},
        {"application": "b.exe", "process_id": 2, "rows": 1},
    ]


def test_summarize_processes_returns_empty_for_bad_header_rules():
    assert summarize_processes("") == []
    assert summarize_processes("Application,ProcessID\n") == []
    assert summarize_processes("MsBetweenPresents\n10.0\n") == []

"""Tests for app.automation.plan_text."""

from app.automation.plan_text import format_duration, plan_overview


def test_format_duration_zero():
    assert format_duration(0) == "0 s"


def test_format_duration_under_one_minute():
    assert format_duration(45) == "45 s"


def test_format_duration_exact_minute():
    assert format_duration(60) == "1 min"


def test_format_duration_minutes_and_seconds():
    assert format_duration(125) == "2 min 5 s"


def test_format_duration_exact_hour():
    assert format_duration(3600) == "1 h"


def test_format_duration_hours_and_minutes():
    assert format_duration(3720) == "1 h 2 min"


def test_format_duration_drops_seconds_in_hour_range():
    assert format_duration(3659) == "1 h"


def test_plan_overview_example():
    items = [
        {
            "display_name": "Tool A",
            "command": "a.exe -x",
            "repetitions": 3,
            "warmup_runs": 1,
            "timeout_seconds": 60,
            "verified": True,
            "is_test": False,
        }
    ]

    result = plan_overview(items, 10)

    assert result["total_runs"] == 4
    assert result["max_seconds"] == 270
    assert result["skipped"] == []
    assert result["text"] == (
        "Tool A: 1 warmup + 3 measured runs, timeout 1 min each\n"
        "  command: a.exe -x\n"
        "Cooldown between runs: 10 s\n"
        "Total: 4 runs, at most 4 min 30 s"
    )


def test_plan_overview_skipped_item_is_not_counted():
    items = [
        {
            "display_name": "Missing",
            "command": None,
            "repetitions": 5,
            "warmup_runs": 2,
            "timeout_seconds": 100,
            "verified": True,
            "is_test": False,
        },
        {
            "display_name": "Found",
            "command": "run",
            "repetitions": 2,
            "warmup_runs": 0,
            "timeout_seconds": 30,
            "verified": True,
            "is_test": False,
        },
    ]

    result = plan_overview(items, 0)

    assert result["total_runs"] == 2
    assert result["max_seconds"] == 60
    assert result["skipped"] == ["Missing"]
    assert result["text"] == (
        "Found: 0 warmup + 2 measured runs, timeout 30 s each\n"
        "  command: run\n"
        "Missing: skipped (not found)\n"
        "Cooldown between runs: 0 s\n"
        "Total: 2 runs, at most 1 min"
    )


def test_plan_overview_no_runnable_items():
    items = [
        {
            "display_name": "A",
            "command": None,
            "repetitions": 1,
            "warmup_runs": 0,
            "timeout_seconds": 10,
            "verified": True,
            "is_test": False,
        },
        {
            "display_name": "B",
            "command": None,
            "repetitions": 1,
            "warmup_runs": 0,
            "timeout_seconds": 10,
            "verified": True,
            "is_test": False,
        },
    ]

    result = plan_overview(items, 5)

    assert result["total_runs"] == 0
    assert result["max_seconds"] == 0
    assert result["skipped"] == ["A", "B"]
    assert result["text"] == (
        "A: skipped (not found)\n"
        "B: skipped (not found)\n"
        "Cooldown between runs: 5 s\n"
        "Total: 0 runs, at most 0 s"
    )


def test_plan_overview_warning_and_test_lines():
    items = [
        {
            "display_name": "Tool",
            "command": "tool --flag",
            "repetitions": 1,
            "warmup_runs": 1,
            "timeout_seconds": 10,
            "verified": False,
            "is_test": True,
        }
    ]

    result = plan_overview(items, 0)

    assert result["text"] == (
        "Tool: 1 warmup + 1 measured runs, timeout 10 s each\n"
        "  command: tool --flag\n"
        "  WARNING: this tool spec is not verified on a real machine\n"
        "  stored as test data (not a hardware result)\n"
        "Cooldown between runs: 0 s\n"
        "Total: 2 runs, at most 20 s"
    )


def test_plan_overview_multiple_items_and_cooldown():
    items = [
        {
            "display_name": "First",
            "command": "first",
            "repetitions": 2,
            "warmup_runs": 1,
            "timeout_seconds": 60,
            "verified": True,
            "is_test": False,
        },
        {
            "display_name": "Second",
            "command": "second",
            "repetitions": 1,
            "warmup_runs": 0,
            "timeout_seconds": 120,
            "verified": True,
            "is_test": False,
        },
    ]

    result = plan_overview(items, 15)

    assert result["total_runs"] == 4
    assert result["max_seconds"] == 345
    assert result["skipped"] == []
    assert result["text"] == (
        "First: 1 warmup + 2 measured runs, timeout 1 min each\n"
        "  command: first\n"
        "Second: 0 warmup + 1 measured runs, timeout 2 min each\n"
        "  command: second\n"
        "Cooldown between runs: 15 s\n"
        "Total: 4 runs, at most 5 min 45 s"
    )


def test_plan_overview_skipped_items_are_listed_after_runnable_items():
    items = [
        {
            "display_name": "A",
            "command": None,
            "repetitions": 1,
            "warmup_runs": 0,
            "timeout_seconds": 1,
            "verified": True,
            "is_test": False,
        },
        {
            "display_name": "B",
            "command": "b",
            "repetitions": 1,
            "warmup_runs": 0,
            "timeout_seconds": 1,
            "verified": True,
            "is_test": False,
        },
        {
            "display_name": "C",
            "command": None,
            "repetitions": 1,
            "warmup_runs": 0,
            "timeout_seconds": 1,
            "verified": True,
            "is_test": False,
        },
    ]

    result = plan_overview(items, 0)

    assert result["skipped"] == ["A", "C"]
    assert result["text"] == (
        "B: 0 warmup + 1 measured runs, timeout 1 s each\n"
        "  command: b\n"
        "A: skipped (not found)\n"
        "C: skipped (not found)\n"
        "Cooldown between runs: 0 s\n"
        "Total: 1 runs, at most 1 s"
    )


def test_plan_overview_empty_items():
    result = plan_overview([], 0)

    assert result["total_runs"] == 0
    assert result["max_seconds"] == 0
    assert result["skipped"] == []
    assert result["text"] == (
        "Cooldown between runs: 0 s\n"
        "Total: 0 runs, at most 0 s"
    )

"""Tests for app.frames.stats."""

import pytest

from app.frames.stats import (
    FrameStats,
    compute_frame_stats,
    low_fps,
    percentile,
    stats_to_metrics,
)


def test_low_fps_single_slowest_frame():
    assert low_fps([10.0, 10.0, 10.0, 40.0], 0.01) == 25.0


def test_low_fps_two_slowest_frames():
    values = [10.0] * 198 + [50.0, 30.0]
    assert low_fps(values, 0.01) == 25.0


def test_percentile_linear_interpolation():
    assert percentile([10.0, 20.0, 30.0, 40.0], 99) == pytest.approx(39.7)


def test_percentile_single_value():
    assert percentile([42.0], 99) == 42.0


def test_constant_capture_stats():
    stats = compute_frame_stats([10.0] * 100)

    assert stats.error is None
    assert stats.frames == 100
    assert stats.invalid_frames == 0
    assert stats.duration_seconds == pytest.approx(1.0)
    assert stats.avg_fps == pytest.approx(100.0)
    assert stats.low_1pct_fps == pytest.approx(100.0)
    assert stats.low_01pct_fps == pytest.approx(100.0)
    assert stats.p99_frametime_ms == pytest.approx(10.0)
    assert stats.p99_fps == pytest.approx(100.0)
    assert stats.median_frametime_ms == pytest.approx(10.0)
    assert stats.min_frametime_ms == pytest.approx(10.0)
    assert stats.max_frametime_ms == pytest.approx(10.0)
    assert stats.stdev_frametime_ms == pytest.approx(0.0)


def test_outlier_capture_stats():
    values = [10.0] * 99 + [110.0]
    stats = compute_frame_stats(values)

    assert stats.error is None
    assert stats.frames == 100
    assert stats.invalid_frames == 0
    assert stats.duration_seconds == pytest.approx(1.1)
    assert stats.avg_fps == pytest.approx(1000.0 * 100 / 1100.0)
    assert stats.low_1pct_fps == pytest.approx(1000.0 / 110.0)
    assert stats.low_01pct_fps == pytest.approx(1000.0 / 110.0)
    assert stats.p99_frametime_ms == pytest.approx(11.0)
    assert stats.p99_fps == pytest.approx(1000.0 / 11.0)
    assert stats.median_frametime_ms == pytest.approx(10.0)
    assert stats.min_frametime_ms == pytest.approx(10.0)
    assert stats.max_frametime_ms == pytest.approx(110.0)
    assert stats.stdev_frametime_ms == pytest.approx(10.0)


def test_invalid_values_are_counted_and_dropped():
    values = [10.0] * 100 + [
        "x",
        None,
        float("nan"),
        float("inf"),
        0,
        -5,
        True,
    ]
    stats = compute_frame_stats(values)

    assert stats.error is None
    assert stats.frames == 100
    assert stats.invalid_frames == 7
    assert stats.avg_fps == pytest.approx(100.0)


def test_too_few_frames_produces_error_and_no_numbers():
    stats = compute_frame_stats([10.0] * 99)

    assert stats.frames == 99
    assert stats.invalid_frames == 0
    assert stats.error == "too few frames: 99 (need 100)"
    assert stats.duration_seconds is None
    assert stats.avg_fps is None
    assert stats.low_1pct_fps is None
    assert stats.low_01pct_fps is None
    assert stats.p99_frametime_ms is None
    assert stats.p99_fps is None
    assert stats.median_frametime_ms is None
    assert stats.min_frametime_ms is None
    assert stats.max_frametime_ms is None
    assert stats.stdev_frametime_ms is None


def test_min_frames_one_single_frame():
    stats = compute_frame_stats([10.0], min_frames=1)

    assert stats.error is None
    assert stats.frames == 1
    assert stats.invalid_frames == 0
    assert stats.duration_seconds == pytest.approx(0.01)
    assert stats.avg_fps == pytest.approx(100.0)
    assert stats.low_1pct_fps == pytest.approx(100.0)
    assert stats.low_01pct_fps == pytest.approx(100.0)
    assert stats.p99_frametime_ms == pytest.approx(10.0)
    assert stats.p99_fps == pytest.approx(100.0)
    assert stats.median_frametime_ms == pytest.approx(10.0)
    assert stats.min_frametime_ms == pytest.approx(10.0)
    assert stats.max_frametime_ms == pytest.approx(10.0)
    assert stats.stdev_frametime_ms is None


def test_empty_capture_is_too_short():
    stats = compute_frame_stats([])

    assert stats.frames == 0
    assert stats.invalid_frames == 0
    assert stats.error == "too few frames: 0 (need 100)"
    assert stats.avg_fps is None


def test_string_numbers_are_accepted():
    stats = compute_frame_stats(["10.0"] * 100)

    assert stats.error is None
    assert stats.frames == 100
    assert stats.invalid_frames == 0
    assert stats.avg_fps == pytest.approx(100.0)


def test_non_finite_string_is_invalid():
    stats = compute_frame_stats([10.0] * 100 + ["nan"])

    assert stats.error is None
    assert stats.frames == 100
    assert stats.invalid_frames == 1
    assert stats.avg_fps == pytest.approx(100.0)


def test_stats_to_metrics_error_returns_empty_dict():
    stats = compute_frame_stats([10.0] * 99)
    assert stats_to_metrics(stats) == {}


def test_stats_to_metrics_constant_capture():
    stats = compute_frame_stats([10.0] * 100)
    metrics = stats_to_metrics(stats)

    assert metrics["avg_fps"] == (100.0, "fps")
    assert metrics["low_1pct_fps"] == (100.0, "fps")
    assert metrics["low_01pct_fps"] == (100.0, "fps")
    assert metrics["p99_fps"] == (100.0, "fps")
    assert metrics["p99_frametime_ms"] == (10.0, "ms")
    assert metrics["median_frametime_ms"] == (10.0, "ms")
    assert metrics["min_frametime_ms"] == (10.0, "ms")
    assert metrics["max_frametime_ms"] == (10.0, "ms")
    assert metrics["stdev_frametime_ms"] == (0.0, "ms")
    assert metrics["duration_seconds"] == (1.0, "s")
    assert metrics["frames"] == (100.0, "frames")
    assert metrics["invalid_frames"] == (0.0, "frames")
    assert len(metrics) == 12


def test_stats_to_metrics_single_frame_omits_stdev():
    stats = compute_frame_stats([10.0], min_frames=1)
    metrics = stats_to_metrics(stats)

    assert "stdev_frametime_ms" not in metrics
    assert metrics["frames"] == (1.0, "frames")
    assert metrics["invalid_frames"] == (0.0, "frames")
    assert metrics["avg_fps"] == (100.0, "fps")
    assert metrics["duration_seconds"] == (0.01, "s")


def test_helpers_never_return_zero_for_no_data():
    import pytest
    from app.frames.stats import low_fps, percentile
    with pytest.raises(ValueError):
        low_fps([], 0.01)
    with pytest.raises(ValueError):
        percentile([], 99)

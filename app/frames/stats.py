"""Frame-time statistics of one capture (milliseconds per frame, capture order).

Definitions (fixed so results stay comparable):
  avg_fps        = 1000 * frames / total frame time (frames over time, not the mean of per-frame FPS)
  low_1pct_fps   = average FPS of the slowest 1% of frames (k = ceil(n * 0.01), at least 1)
  low_01pct_fps  = the same for the slowest 0.1%
  p99_fps        = 1000 / 99th percentile frame time (linear interpolation)
A capture with too few valid frames gets an error and no numbers.

Drafted by the local agent, reviewed.
"""

import math
import statistics
from dataclasses import dataclass
from typing import Optional


@dataclass
class FrameStats:
    frames: int = 0
    invalid_frames: int = 0
    duration_seconds: Optional[float] = None
    avg_fps: Optional[float] = None
    low_1pct_fps: Optional[float] = None
    low_01pct_fps: Optional[float] = None
    p99_frametime_ms: Optional[float] = None
    p99_fps: Optional[float] = None
    median_frametime_ms: Optional[float] = None
    min_frametime_ms: Optional[float] = None
    max_frametime_ms: Optional[float] = None
    stdev_frametime_ms: Optional[float] = None
    error: Optional[str] = None


def low_fps(frametimes_ms: list, fraction: float) -> float:
    """Return the FPS represented by the slowest ``fraction`` of frames."""
    if not frametimes_ms:
        raise ValueError("no values")

    n = len(frametimes_ms)
    k = max(1, math.ceil(n * fraction))
    k = min(k, n)

    slowest = sorted(frametimes_ms, reverse=True)[:k]
    total = sum(slowest)

    return 1000.0 * k / total


def percentile(values: list, p: float) -> float:
    """Return the linear-interpolated percentile of ``values``."""
    if not values:
        raise ValueError("no values")

    ordered = sorted(values)
    n = len(ordered)

    if n == 1:
        return float(ordered[0])

    if p <= 0.0:
        return float(ordered[0])

    if p >= 100.0:
        return float(ordered[-1])

    pos = (n - 1) * p / 100.0
    lower = math.floor(pos)
    upper = math.ceil(pos)

    lower = max(0, min(n - 1, lower))
    upper = max(0, min(n - 1, upper))

    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower))


def compute_frame_stats(frametimes_ms: list, min_frames: int = 100) -> FrameStats:
    """Compute frame statistics from a capture of frame times in milliseconds."""
    if frametimes_ms is None:
        items = []
    elif isinstance(frametimes_ms, (str, bytes)):
        items = [frametimes_ms]
    else:
        try:
            items = list(frametimes_ms)
        except TypeError:
            items = []

    valid = []
    invalid_frames = 0

    for item in items:
        if isinstance(item, bool):
            invalid_frames += 1
            continue

        try:
            value = float(item)
        except (TypeError, ValueError, OverflowError):
            invalid_frames += 1
            continue

        if not math.isfinite(value) or value <= 0:
            invalid_frames += 1
            continue

        valid.append(value)

    frames = len(valid)

    try:
        required = int(min_frames)
    except (TypeError, ValueError, OverflowError):
        required = 100

    if required < 1:
        required = 1

    if frames < required:
        return FrameStats(
            frames=frames,
            invalid_frames=invalid_frames,
            error=f"too few frames: {frames} (need {min_frames})",
        )

    total_ms = sum(valid)
    duration_seconds = total_ms / 1000.0
    avg_fps = 1000.0 * frames / total_ms

    low_1pct_fps = low_fps(valid, 0.01)
    low_01pct_fps = low_fps(valid, 0.001)

    p99_frametime_ms = percentile(valid, 99)
    p99_fps = 1000.0 / p99_frametime_ms

    median_frametime_ms = statistics.median(valid)
    min_frametime_ms = min(valid)
    max_frametime_ms = max(valid)
    stdev_frametime_ms = statistics.stdev(valid) if frames >= 2 else None

    return FrameStats(
        frames=frames,
        invalid_frames=invalid_frames,
        duration_seconds=duration_seconds,
        avg_fps=avg_fps,
        low_1pct_fps=low_1pct_fps,
        low_01pct_fps=low_01pct_fps,
        p99_frametime_ms=p99_frametime_ms,
        p99_fps=p99_fps,
        median_frametime_ms=median_frametime_ms,
        min_frametime_ms=min_frametime_ms,
        max_frametime_ms=max_frametime_ms,
        stdev_frametime_ms=stdev_frametime_ms,
        error=None,
    )


def stats_to_metrics(stats: FrameStats) -> dict:
    """Convert successful frame statistics into a metric mapping."""
    if stats.error is not None:
        return {}

    metrics = {}

    def add(name: str, value: Optional[float], unit: str) -> None:
        if value is not None:
            metrics[name] = (value, unit)

    add("avg_fps", stats.avg_fps, "fps")
    add("low_1pct_fps", stats.low_1pct_fps, "fps")
    add("low_01pct_fps", stats.low_01pct_fps, "fps")
    add("p99_fps", stats.p99_fps, "fps")
    add("p99_frametime_ms", stats.p99_frametime_ms, "ms")
    add("median_frametime_ms", stats.median_frametime_ms, "ms")
    add("min_frametime_ms", stats.min_frametime_ms, "ms")
    add("max_frametime_ms", stats.max_frametime_ms, "ms")
    add("stdev_frametime_ms", stats.stdev_frametime_ms, "ms")
    add("duration_seconds", stats.duration_seconds, "s")
    add("frames", float(stats.frames), "frames")
    add("invalid_frames", float(stats.invalid_frames), "frames")

    return metrics

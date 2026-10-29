"""Cut one continuous PresentMon recording of a game into consecutive capture windows.

Game time is the running sum of the game's frame times; the first settle_seconds are skipped (loading),
then each window is one warmup or measured run. A frame belongs to the window that contains its END time.
A window the recording does not reach is None, never a partial result. Telemetry samples are cut into the
same windows, and the game's process is the one from its install folder with the most frames.

Drafted by the local agent, reviewed.
"""

from typing import List, Optional, Tuple


def window_bounds(settle_seconds: float, segment_seconds: float, count: int) -> List[Tuple[float, float]]:
    if settle_seconds < 0:
        raise ValueError("settle_seconds must be non-negative")
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be positive")
    if count < 1:
        raise ValueError("count must be at least 1")

    return [
        (settle_seconds + k * segment_seconds, settle_seconds + (k + 1) * segment_seconds)
        for k in range(count)
    ]


def game_seconds(frametimes_ms: List[float]) -> float:
    return sum(frametimes_ms) / 1000.0


def split_frames(frametimes_ms: List[float], windows: List[Tuple[float, float]]) -> List[Optional[List[float]]]:
    total_ms = sum(frametimes_ms)

    ends_ms: List[float] = []
    running_ms = 0.0
    for frame_ms in frametimes_ms:
        running_ms += frame_ms
        ends_ms.append(running_ms)

    result: List[Optional[List[float]]] = []
    for a, b in windows:
        if total_ms < b * 1000.0:
            result.append(None)
            continue

        a_ms = a * 1000.0
        b_ms = b * 1000.0
        selected: List[float] = []
        for frame_ms, end_ms in zip(frametimes_ms, ends_ms):
            if a_ms < end_ms <= b_ms:
                selected.append(frame_ms)
        result.append(selected)

    return result


def slice_samples(
    samples: List[Tuple[float, dict]],
    offset_seconds: float,
    windows: List[Tuple[float, float]],
) -> List[List[Tuple[float, dict]]]:
    result: List[List[Tuple[float, dict]]] = []

    for a, b in windows:
        bucket: List[Tuple[float, dict]] = []
        for sampler_time_seconds, readings in samples:
            game_time = sampler_time_seconds - offset_seconds
            if a <= game_time < b:
                bucket.append((round(game_time - a, 3), readings))
        result.append(bucket)

    return result


def pick_game_process(summary: List[dict], exe_names) -> Optional[Tuple[str, Optional[int]]]:
    if exe_names is None:
        return None

    try:
        allowed = {name.lower() for name in exe_names if isinstance(name, str)}
    except TypeError:
        return None

    if not allowed:
        return None

    best: Optional[Tuple[str, Optional[int]]] = None
    best_rows: Optional[int] = None

    for item in summary:
        if not isinstance(item, dict):
            continue

        application = item.get("application")
        if not isinstance(application, str):
            continue

        rows = item.get("rows")
        if not isinstance(rows, int) or isinstance(rows, bool):
            continue

        if application.lower() not in allowed:
            continue

        process_id = item.get("process_id")
        if not isinstance(process_id, int) or isinstance(process_id, bool):
            process_id = None

        if best is None or rows > best_rows:
            best = (application, process_id)
            best_rows = rows

    return best


def first_frame_offset(seen_at_seconds: float, game_seconds_then: float) -> float:
    return seen_at_seconds - game_seconds_then


LOADING_FRAME_MS = 200.0        # a frame this long is a loading screen, not a hitch (BL3: 231 ms and 219 ms)


def loading_bounds(frametimes_ms: List[float], before_seconds: float,
                   end_margin_seconds: float = 3.0) -> Tuple[Optional[float], Optional[float]]:
    """(start, end) in game seconds of a benchmark between two loading frames.

    start is where the last loading frame before `before_seconds` ended; end is end_margin_seconds before
    the next loading frame ends (the fade back to the menu). Either is None when there is no such frame.
    """
    start: Optional[float] = None
    end: Optional[float] = None
    running_ms = 0.0
    for frame_ms in frametimes_ms:
        running_ms += frame_ms
        if frame_ms < LOADING_FRAME_MS:
            continue
        t = running_ms / 1000.0
        if t <= before_seconds:
            start = t
        elif start is not None:
            end = t - end_margin_seconds
            break
    return start, end

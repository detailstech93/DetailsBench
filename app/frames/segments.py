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


FADE_SPEED = 3.0       # frames this many times shorter than the scenes' own are a fade between scenes
FADE_SECONDS = 1.0     # a faster stretch shorter than this stays (a look at the sky)


FIND_FADE_SECONDS = 30.0   # the fade is looked for from this long before the window's start (the mark is seen in it)
MIN_SCENE_SECONDS = 5.0    # a stretch between fades shorter than this is part of the fade


def faded_scenes(frametimes_ms: List[float], after: float, scenes: int, look_seconds: float = 270.0
                 ) -> Optional[Tuple[List[List[float]], float, float]]:
    """A benchmark of `scenes` scenes, each after a black fade, found in the frames: (each scene's frames, start,
    end) in game seconds, or None when the frames do not show it whole.

    RDR2 on a NucBox K10 (Intel UHD, 2026-11-14): its benchmark begins with a fade, and five 250 FPS fades of
    4-6 s lead into its five scenes (about 25, 26, 31, 30 and 26 FPS); the last scene ends 135-136 s after its
    fade with a few 3-4 ms frames, the cut to the results screen. A fade is a stretch of FADE_SECONDS or more of
    frames over FADE_SPEED times faster than the scenes' own; a shorter fast stretch inside a scene stays.
    """
    ends, at = [], 0.0
    for ms in frametimes_ms:
        at += ms
        ends.append(at / 1000.0)
    look = sorted(ms for ms, end in zip(frametimes_ms, ends) if after < end <= after + look_seconds)
    if not look:
        return None
    half, run, typical = sum(look) / 2.0, 0.0, 0.0
    for typical in look:                    # the frame time half of the time is spent under
        run += typical
        if run >= half:
            break
    limit = typical / FADE_SPEED
    # the frames after `after` as fades and the stretches between them: [is_fade, start s, frames]
    parts: List[list] = []
    fast: List[float] = []
    for ms, end in zip(frametimes_ms, ends):
        if end <= after:
            continue
        if ms < limit:
            fast.append(ms)
            continue
        if fast:
            began = end - (ms + sum(fast)) / 1000.0
            if sum(fast) >= FADE_SECONDS * 1000.0:
                parts.append([True, began, fast])
            elif parts:
                parts[-1][2] += fast        # a short fast stretch stays with what it is in (a scene, a fade)
            fast = []
        if parts and not parts[-1][0]:
            parts[-1][2].append(ms)
        else:
            parts.append([False, end - ms / 1000.0, [ms]])
    if fast:
        parts.append([True, ends[-1] - sum(fast) / 1000.0, fast])
    # a stretch under MIN_SCENE_SECONDS between fades is part of the fade (its slower frames, 2026-11-14)
    found = [p for p in parts if not p[0] and sum(p[2]) >= MIN_SCENE_SECONDS * 1000.0]
    fades = [p for p in parts if p[0]]
    if not fades or fades[0][1] - after > 2 * FIND_FADE_SECONDS:
        return None
    start = fades[0][1]
    found = [p for p in found if p[1] > start][:scenes]
    if len(found) < scenes:
        return None                         # the recording ended before the last scene
    kept = [[ms for ms in p[2] if ms >= limit] for p in found[:-1]]      # a fade's fast frames are not a scene's
    last, at = [], found[-1][1]
    for ms in found[-1][2]:
        at += ms / 1000.0
        if ms >= limit:
            last.append(ms)
        elif at - found[-1][1] > MIN_SCENE_SECONDS:     # the cut to the results screen
            return kept + [last], start, at - ms / 1000.0
    return None                             # the last scene did not end in the recording


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
LOAD_CLUSTER_SECONDS = 2.0      # loading frames this close are one load


def next_loading(frametimes_ms: List[float], after_seconds: float) -> Optional[float]:
    """When (game seconds) the first loading frame after after_seconds began, or None."""
    running_ms = 0.0
    for frame_ms in frametimes_ms:
        running_ms += frame_ms
        if frame_ms >= LOADING_FRAME_MS and (running_ms - frame_ms) / 1000.0 >= after_seconds:
            return (running_ms - frame_ms) / 1000.0
    return None


def loading_bounds(frametimes_ms: List[float], after_seconds: float, end_margin_seconds: float = 3.0,
                   within_seconds: float = 60.0, min_seconds: float = 0.0) -> Tuple[Optional[float], Optional[float]]:
    """(start, end) in game seconds of a benchmark between two loading frames.

    start is where the load after `after_seconds` (the Start click) ended; its loading frames come in a cluster
    (BL3: 223 ms then 244 ms, 0.3 s apart), so the last of them. It must begin within within_seconds of the
    click. end is end_margin_seconds before the next loading frame begins (the fade back to the menu). Either
    is None when there is no such frame. A long frame that would end it under min_seconds is a stutter inside it.
    """
    start: Optional[float] = None
    end: Optional[float] = None
    running_ms = 0.0
    for frame_ms in frametimes_ms:
        running_ms += frame_ms
        if frame_ms < LOADING_FRAME_MS:
            continue
        t = running_ms / 1000.0
        began = t - frame_ms / 1000.0
        if began < after_seconds:
            continue
        if start is None:
            if began - after_seconds > within_seconds:
                break
            start = t
        elif began - start <= LOAD_CLUSTER_SECONDS:
            start = t
        elif began - end_margin_seconds - start < min_seconds:
            continue        # NucBox K10, 2026-11-13: a 288 ms frame 107 s into BL3's 110 s was taken for the fade
        else:
            end = began - end_margin_seconds
            break
    return start, end

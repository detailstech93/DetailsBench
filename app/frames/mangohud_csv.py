"""Read a MangoHud frame log (Linux) into the same frame times a PresentMon capture gives (app/frames/stats.py).

MangoHud's own code (src/logging.cpp and src/overlay.cpp, master, read 2026-10-19; MIT license, facts only):
the log is "<program>_YYYY-MM-DD_HH-MM-SS.csv" in output_folder, with a "_summary.csv" next to it when logging
stops. Its first two lines are the system ("os,cpu,gpu,ram,kernel,driver,cpuscheduler" and the values), then the
frame columns "fps,frametime,cpu_load,...,elapsed". With log_interval=0 (the default) it writes one row per frame:
frametime in milliseconds (frametime_ns / 1000000), elapsed in nanoseconds since logging started. Older MangoHud
versions have fewer columns, so the columns are found by name. Not yet checked against a real log here.
"""

import csv
import io
import re
from pathlib import Path
from typing import List, Optional

from app.frames.presentmon_csv import CaptureParse

LOG_NAME = re.compile(r"^(?P<program>.+)_(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.csv$")


def _float(value: str) -> Optional[float]:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def parse_mangohud_log(text: str, start_seconds: float = 0.0, seconds: Optional[float] = None) -> CaptureParse:
    """The frames from start_seconds after logging began, for `seconds` (None: to the end)."""
    out = CaptureParse(frame_time_column="frametime")
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    header_at = next((i for i, r in enumerate(rows) if "frametime" in [c.strip().lower() for c in r]), None)
    if header_at is None:
        out.error = "not a MangoHud frame log (no frametime column)"
        return out
    header = [c.strip().lower() for c in rows[header_at]]
    ft, el = header.index("frametime"), (header.index("elapsed") if "elapsed" in header else None)
    end = None if seconds is None else start_seconds + seconds
    for row in rows[header_at + 1:]:
        out.rows_total += 1
        frametime = _float(row[ft]) if ft < len(row) else None
        elapsed = _float(row[el]) / 1e9 if el is not None and el < len(row) and _float(row[el]) is not None \
            else None
        if frametime is None or frametime <= 0:
            out.rows_skipped += 1
            continue
        if elapsed is None and (start_seconds > 0 or end is not None):
            out.rows_skipped += 1               # a time window needs the elapsed column
            continue
        if elapsed is not None and (elapsed < start_seconds or (end is not None and elapsed > end)):
            continue
        out.frametimes_ms.append(frametime)
        out.rows_used += 1
    if not out.frametimes_ms:
        out.error = "no frames in the MangoHud log" + (" in that time window" if seconds is not None else "")
    return out


def find_new_logs(folder: Path, since: float) -> List[Path]:
    """Frame logs (not the _summary.csv files) in folder written at or after `since` (a time.time()), newest
    first."""
    found = []
    try:
        for p in Path(folder).iterdir():
            if p.is_file() and LOG_NAME.match(p.name) and not p.name.endswith("_summary.csv"):
                mtime = p.stat().st_mtime
                if mtime >= since:
                    found.append((mtime, p))
    except OSError:
        return []
    return [p for _, p in sorted(found, reverse=True)]

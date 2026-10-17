"""Parse PresentMon CSV captures into per-process frame-time data.

Column names are looked up case-insensitively: "MsBetweenPresents" (PresentMon 1.x and
--v1_metrics) or "FrameTime" (2.x; unverified until a real 2.x capture is checked).
A capture with several processes of the same name is refused ("select one") rather than mixed.

Drafted by the local agent, reviewed.
"""

import csv
import io
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


FRAME_TIME_COLUMNS = ("MsBetweenPresents", "FrameTime")
APPLICATION_COLUMN = "Application"
PROCESS_ID_COLUMN = "ProcessID"
RUNTIME_COLUMNS = ("Runtime", "PresentRuntime")
DROPPED_COLUMN = "Dropped"


@dataclass
class CaptureParse:
    frametimes_ms: List[float] = field(default_factory=list)
    frame_time_column: Optional[str] = None
    application: Optional[str] = None
    process_id: Optional[int] = None
    runtimes: List[str] = field(default_factory=list)
    rows_total: int = 0
    rows_used: int = 0
    rows_skipped: int = 0
    dropped: int = 0
    error: Optional[str] = None


def _remove_bom(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _is_blank_row(row: List[str]) -> bool:
    if not row:
        return True
    if len(row) == 1 and row[0].strip() == "":
        return True
    return False


def _read_rows(text: str) -> Optional[List[List[str]]]:
    try:
        reader = csv.reader(io.StringIO(_remove_bom(text)))
        return [row for row in reader if not _is_blank_row(row)]
    except Exception:
        return None


def _build_header_map(header: List[str]) -> dict:
    header_map = {}
    for index, raw_name in enumerate(header):
        key = raw_name.strip().lower()
        if key not in header_map:
            header_map[key] = (raw_name.strip(), index)
    return header_map


def _find_column(header_map: dict, names: Tuple[str, ...]) -> Optional[Tuple[str, int]]:
    for name in names:
        key = name.strip().lower()
        if key in header_map:
            return header_map[key]
    return None


def _cell(row: List[str], index: Optional[int]) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    return row[index].strip()


def _to_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _format_process_pair(application: str, process_id: Optional[int]) -> str:
    return f"{application} ({process_id})"


def parse_presentmon_csv(
    text: str,
    process_name: Optional[str] = None,
    process_id: Optional[int] = None,
) -> CaptureParse:
    rows = _read_rows(text)
    if rows is None:
        return CaptureParse(error="invalid csv")

    if not rows:
        return CaptureParse(error="empty file")

    header = rows[0]
    data_rows = rows[1:]
    rows_total = len(data_rows)

    header_map = _build_header_map(header)

    frame_info = _find_column(header_map, FRAME_TIME_COLUMNS)
    if frame_info is None:
        return CaptureParse(
            rows_total=rows_total,
            error="no frame time column (MsBetweenPresents or FrameTime)",
        )

    frame_column, frame_index = frame_info

    app_info = header_map.get(APPLICATION_COLUMN.strip().lower())
    pid_info = header_map.get(PROCESS_ID_COLUMN.strip().lower())

    if app_info is None and pid_info is None:
        return CaptureParse(
            rows_total=rows_total,
            frame_time_column=frame_column,
            error="no Application or ProcessID column",
        )

    runtime_info = _find_column(header_map, RUNTIME_COLUMNS)
    dropped_info = header_map.get(DROPPED_COLUMN.strip().lower())

    selected_rows = []
    for row in data_rows:
        app_value = _cell(row, app_info[1]) if app_info is not None else ""
        pid_value = _cell(row, pid_info[1]) if pid_info is not None else ""

        name_matches = True
        if process_name is not None:
            name_matches = app_value.lower() == process_name.strip().lower()

        pid_matches = True
        if process_id is not None:
            row_pid = _to_int(pid_value) if pid_info is not None else None
            pid_matches = row_pid == process_id

        if name_matches and pid_matches:
            selected_rows.append((row, app_value, pid_value))

    grouped = {}
    for row, app_value, pid_value in selected_rows:
        row_pid = _to_int(pid_value) if pid_info is not None else None
        pair = (app_value, row_pid)
        grouped.setdefault(pair, []).append(row)

    if not grouped:
        if process_name is None and process_id is None:
            error = "no frames in file"
        else:
            error = "no frames for the selected process"
        return CaptureParse(
            rows_total=rows_total,
            frame_time_column=frame_column,
            error=error,
        )

    if len(grouped) > 1:
        sorted_pairs = sorted(
            grouped.keys(),
            key=lambda pair: (pair[0].lower(), pair[1] if pair[1] is not None else -1),
        )
        parts = [_format_process_pair(app, pid) for app, pid in sorted_pairs]
        error = "several processes in capture: " + ", ".join(parts) + "; select one"
        return CaptureParse(
            rows_total=rows_total,
            frame_time_column=frame_column,
            error=error,
        )

    (application, process_id_value), process_rows = next(iter(grouped.items()))

    frametimes_ms = []
    runtimes = set()
    rows_used = 0
    rows_skipped = 0
    dropped = 0

    for row in process_rows:
        frame_value = _cell(row, frame_index)
        frame_time = _to_float(frame_value)
        if frame_time is None:
            rows_skipped += 1
            continue

        frametimes_ms.append(frame_time)
        rows_used += 1

        if runtime_info is not None:
            runtime_value = _cell(row, runtime_info[1])
            if runtime_value:
                runtimes.add(runtime_value)

        if dropped_info is not None and _cell(row, dropped_info[1]) == "1":
            dropped += 1

    if not frametimes_ms:
        return CaptureParse(
            frame_time_column=frame_column,
            application=application if app_info is not None else None,
            process_id=process_id_value,
            rows_total=rows_total,
            rows_used=0,
            rows_skipped=rows_skipped,
            error="no usable frame times",
        )

    return CaptureParse(
        frametimes_ms=frametimes_ms,
        frame_time_column=frame_column,
        application=application if app_info is not None else None,
        process_id=process_id_value,
        runtimes=sorted(runtimes),
        rows_total=rows_total,
        rows_used=rows_used,
        rows_skipped=rows_skipped,
        dropped=dropped,
    )


def summarize_processes(text: str) -> List[dict]:
    rows = _read_rows(text)
    if rows is None:
        return []

    if not rows:
        return []

    header = rows[0]
    data_rows = rows[1:]

    header_map = _build_header_map(header)

    frame_info = _find_column(header_map, FRAME_TIME_COLUMNS)
    if frame_info is None:
        return []

    app_info = header_map.get(APPLICATION_COLUMN.strip().lower())
    pid_info = header_map.get(PROCESS_ID_COLUMN.strip().lower())

    if app_info is None and pid_info is None:
        return []

    counts = {}
    for row in data_rows:
        app_value = _cell(row, app_info[1]) if app_info is not None else ""
        pid_value = _cell(row, pid_info[1]) if pid_info is not None else ""
        row_pid = _to_int(pid_value) if pid_info is not None else None
        pair = (app_value, row_pid)
        counts[pair] = counts.get(pair, 0) + 1

    summary = [
        {"application": app, "process_id": pid, "rows": count}
        for (app, pid), count in counts.items()
    ]
    summary.sort(key=lambda item: (-item["rows"], item["application"].lower()))
    return summary

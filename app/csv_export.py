"""Run rows to CSV text, safe for spreadsheets (formula-injection guard on text cells).

Drafted by the local agent, reviewed.
"""
import csv
import io
import math
from collections.abc import Iterable, Mapping
from typing import Optional

CSV_COLUMNS = [
    "session_id",
    "session_name",
    "benchmark_name",
    "benchmark_type",
    "benchmark_version",
    "run_index",
    "status",
    "is_warmup",
    "is_test",
    "is_manual",
    "score",
    "unit",
    "timestamp",
    "note",
]


def safe_cell(value: object) -> str:
    if value is None:
        return ""

    if isinstance(value, bool):
        return "1" if value else "0"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        try:
            finite = math.isfinite(value)
        except Exception:
            return ""
        if not finite:
            return ""
        try:
            return repr(value)
        except Exception:
            return ""

    try:
        text = str(value)
    except Exception:
        return ""

    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text

    return text


def _safe_get(mapping: Optional[Mapping], key: str) -> object:
    if mapping is None:
        return None
    try:
        return mapping.get(key)
    except Exception:
        return None


def runs_to_csv(runs: Optional[Iterable[Mapping]], session: Optional[Mapping]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_COLUMNS)

    if runs is None:
        return buffer.getvalue()

    try:
        iterator = iter(runs)
    except TypeError:
        return buffer.getvalue()

    session_id_fallback = _safe_get(session, "id")
    session_name = _safe_get(session, "name")

    for row in iterator:
        if not isinstance(row, Mapping):
            continue

        out_row = []
        for column in CSV_COLUMNS:
            if column == "session_id":
                value = _safe_get(row, "session_id")
                if value is None:
                    value = session_id_fallback
            elif column == "session_name":
                value = session_name
            else:
                value = _safe_get(row, column)

            out_row.append(safe_cell(value))

        writer.writerow(out_row)

    return buffer.getvalue()

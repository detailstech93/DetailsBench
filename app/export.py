"""Session export: one dict with everything about a session, written as JSON, CSV or HTML.

build_session_export() gathers the session, its manifest, every run (test,
warmup and failed runs included, flagged) and per-benchmark statistics.
Statistics use the same rules as the Compare tab: within one session, test
runs count only when the session itself is a test session.

Files are written to "<name>.partial" first and renamed when complete, so an
interrupted export never leaves a half-written file under the final name.
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.stats import summarize_runs

EXPORT_FORMAT = "pc-benchmark-manager.session"
EXPORT_FORMAT_VERSION = 1


def build_session_export(db, session_id: int, now: Optional[str] = None) -> dict:
    session = next((s for s in db.search_sessions(include_test=True) if s["id"] == session_id), None)
    if session is None:
        raise KeyError(f"session {session_id} not found")
    runs = db.get_runs(session_id, include_test=True)
    grouped = {}
    for run in runs:
        grouped.setdefault(run["benchmark_name"], []).append(run)
    summaries = {name: summarize_runs(group, include_test=bool(session["is_test"]))
                 for name, group in sorted(grouped.items())}
    manifest = db.get_session_manifest(session_id)
    from app.metrics_summary import metrics_dict, summarize_metrics
    run_metrics = {r["id"]: db.get_result_metrics(r["id"]) for r in runs}
    run_series = {r["id"]: db.get_result_series(r["id"]) for r in runs} if hasattr(db, "get_result_series") else {}
    metric_summaries = summarize_metrics(runs, lambda r: run_metrics.get(r["id"]), include_test=bool(session["is_test"]))
    try:
        from app.history import earlier_comparisons
        earlier = earlier_comparisons(db, session, manifest, runs)
    except Exception:               # the comparison never stops a report
        import logging
        logging.getLogger(__name__).exception("Could not compare with earlier runs")
        earlier = []
    from app import __version__
    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": now or datetime.now().isoformat(timespec="seconds"),
        "app": {"version": __version__,
                "git_commit": ((manifest or {}).get("app") or {}).get("git_commit")},
        "session": {k: session.get(k) for k in ("id", "name", "description", "created_at")}
                   | {"is_test": bool(session.get("is_test")), "is_manual": bool(session.get("is_manual"))},
        "manifest": manifest,
        "runs": [dict(r, is_warmup=bool(r["is_warmup"]), is_test=bool(r["is_test"]),
                      is_manual=bool(r["is_manual"]), metrics=metrics_dict(run_metrics[r["id"]]),
                      series=run_series.get(r["id"], {})) for r in runs],
        "summaries": summaries,
        "metric_summaries": metric_summaries,
        "earlier": earlier,         # the same tests on the same PC in earlier sessions (app/history.py)
    }


def _json_safe(value):
    """NaN/inf are not valid JSON: write them as null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def export_to_json(export: dict) -> str:
    return json.dumps(_json_safe(export), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def export_to_csv(export: dict) -> str:
    from app.csv_export import runs_to_csv
    return runs_to_csv(export.get("runs") or [], export.get("session"))


def export_to_html(export: dict) -> str:
    from app.report import render_session_report
    return render_session_report(export)


FORMATS = {"json": export_to_json, "csv": export_to_csv, "html": export_to_html}


def write_export(export: dict, path: Path, fmt: str) -> Path:
    """Render export in fmt ("json", "csv", "html") and write it atomically to path."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown export format {fmt!r}")
    text = FORMATS[fmt](export)
    path = Path(path)
    partial = path.with_name(path.name + ".partial")
    # utf-8-sig for CSV so Excel detects UTF-8 (Arabic names); csv text already has \r\n.
    encoding = "utf-8-sig" if fmt == "csv" else "utf-8"
    with open(partial, "w", encoding=encoding, newline="") as f:
        f.write(text)
    partial.replace(path)
    return path


def default_filename(export: dict, fmt: str) -> str:
    session = export.get("session") or {}
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session.get("name") or "session"))
    return f"session_{session.get('id')}_{name[:40].strip('_') or 'session'}.{fmt}"

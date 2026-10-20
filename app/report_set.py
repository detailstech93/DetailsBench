"""The reports of one run (owner, 2026-10-20).

- Every report is named after the test, then the date and time the run started: "Cinebench 2026 (Single Thread)" run
  at 2026-10-20 09:15:30 -> "Cinebench 2026 Single Thread_2026-10-20_09-15-30.html".
- A run of one test: that one report in the reports folder.
- A run of several tests: a folder named by the start date and time (2026-10-20_09-15-30) in the reports folder, with
  one report per test and a final report (Summary_<date and time>.html): the environment, then each test with how
  many times it ran, a short result (the score, or that it FAILED and why) and the time it took, linked to its
  own report.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Mapping, Optional

SAFE = re.compile(r"[^\w\-. ]+")


def stamp(created_at) -> str:
    """'2026-10-20T09:15:30.123' -> '2026-10-20_09-15-30' (no ':' in a Windows file name)."""
    try:
        when = datetime.fromisoformat(str(created_at).replace("Z", ""))
    except ValueError:
        when = datetime.now()
    return when.strftime("%Y-%m-%d_%H-%M-%S")


def safe_name(text: str) -> str:
    name = re.sub(r"\s+", " ", SAFE.sub(" ", str(text))).strip(" .")
    return name[:120] or "test"


def tested_names(export: Mapping) -> List[str]:
    """The tests of the session in run order (real runs only), each once."""
    names = []
    for r in export.get("runs") or []:
        name = r.get("benchmark_name")
        if not r.get("is_test") or (export.get("session") or {}).get("is_test"):
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return names


def display_names(export: Mapping) -> dict:
    used = (export.get("manifest") or {}).get("benchmarks") or []
    return {str(b.get("name")): str(b.get("display_name")) for b in used
            if isinstance(b, Mapping) and b.get("name") and b.get("display_name")}


def one_test(export: Mapping, name: str) -> dict:
    """The session's export cut down to one test: its runs, numbers and earlier results only."""
    out = dict(export)
    out["runs"] = [r for r in export.get("runs") or [] if r.get("benchmark_name") == name]
    out["summaries"] = {k: v for k, v in (export.get("summaries") or {}).items() if k == name}
    out["metric_summaries"] = {k: v for k, v in (export.get("metric_summaries") or {}).items() if k == name}
    out["earlier"] = [e for e in export.get("earlier") or [] if isinstance(e, Mapping)
                      and name in (e.get("name"), e.get("benchmark_name"), e.get("benchmark"))]
    return out


def duration_text(seconds: Optional[float]) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "-"
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return (f"{h} h {m} min" if h else f"{m} min {s} s" if m else f"{s} s")


def test_outcome(export: Mapping, name: str) -> dict:
    """How one test went: runs, a short result, passed or not and the time it took."""
    runs = [r for r in export.get("runs") or [] if r.get("benchmark_name") == name]
    measured = [r for r in runs if not r.get("is_warmup")]
    done = [r for r in measured if r.get("status") == "completed"]
    warmups = len(runs) - len(measured)
    times = [r.get("duration_seconds") for r in runs if isinstance(r.get("duration_seconds"), (int, float))]
    stats = ((export.get("summaries") or {}).get(name) or {}).get("stats") or {}
    mean = stats.get("mean")
    unit = (done[-1].get("unit") if done else None) or ""
    if done and isinstance(mean, (int, float)):
        result = f"{mean:,.2f}".rstrip("0").rstrip(".") + (f" {unit}" if unit else "")
        if len(done) > 1:
            result += f" (average of {len(done)} runs)"
    elif done:
        result = f"{done[-1].get('score')} {unit}".strip()
    else:
        last = measured[-1] if measured else (runs[-1] if runs else {})
        why = str(last.get("note") or last.get("status") or "no result").split("; ")[0]
        result = f"FAILED ({last.get('status') or 'no run'}): {why}"
    count = f"{len(done)} of {len(measured)} measured run(s) completed" + (
        f", {warmups} warm-up" if warmups else "")
    return {"name": name, "passed": bool(done), "result": result, "runs": count,
            "seconds": sum(times) if times else None, "time": duration_text(sum(times) if times else None)}


def write_report_set(export: Mapping, root: Path, only: Optional[str] = None, at: Optional[str] = None) -> Path:
    """Write the run's reports under root; return the one to open (the test's report, or the final report).
    only: just that test's report (a score typed in by the owner joins the open session: its own report, not the
    session's); at: the time in its name instead of the session's start (ISO)."""
    from app.export import write_export
    from app.report import render_summary_report
    root = Path(root)
    when = stamp(at or (export.get("session") or {}).get("created_at"))
    names = [only] if only else (tested_names(export) or [str((export.get("session") or {}).get("name") or "session")])
    titles = display_names(export)
    if len(names) == 1:
        root.mkdir(parents=True, exist_ok=True)
        return write_export(one_test(export, names[0]), _free(root / f"{safe_name(titles.get(names[0], names[0]))}_"
                                                                     f"{when}.html"), "html")
    folder = _free(root / when)
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in names:
        path = write_export(one_test(export, name),
                            _free(folder / f"{safe_name(titles.get(name, name))}_{when}.html"), "html")
        files[name] = path.name
    outcomes = [dict(test_outcome(export, n), title=titles.get(n, n), file=files[n]) for n in names]
    summary = folder / f"Summary_{when}.html"
    text = render_summary_report(export, outcomes)
    partial = summary.with_name(summary.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    partial.replace(summary)
    return summary


def _free(path: Path) -> Path:
    """path, or path with _2, _3... when that name is taken (two runs started in the same second)."""
    if not path.exists():
        return path
    for i in range(2, 1000):
        other = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not other.exists():
            return other
    return path

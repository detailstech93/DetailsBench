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
# a step's text: "<display name>: run 2", "...: warmup 0 (retry)", "...: run 1 completed (12.5 pts)"; a display name
# may hold ': ' itself ("Stress test: CPU", "llama-benchy: Gemma 4 12B (Q4_0)")
STEP = re.compile(r"^(.*?): (?:warmup|run) \d+(?: \((?:retry|extra)\))?(?: \w+ \(.*\))?$", re.S)


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
    """The tests of the session in the order they ran (their first stored run; real runs only), each once."""
    first = {}
    for i, r in enumerate(export.get("runs") or []):
        name = r.get("benchmark_name")
        if not r.get("is_test") or (export.get("session") or {}).get("is_test"):
            if isinstance(name, str) and name:
                order = r.get("id") if isinstance(r.get("id"), int) else i
                first[name] = min(first.get(name, order), order)
    return sorted(first, key=first.get)


EMULATED = " (emulated)"     # next to a test that ran emulated (owner, 2026-10-23)


def display_names(export: Mapping) -> dict:
    used = (export.get("manifest") or {}).get("benchmarks") or []
    return {str(b.get("name")): str(b.get("display_name")) + (EMULATED if b.get("comparable_to_native") is False
                                                               else "")
            for b in used if isinstance(b, Mapping) and b.get("name") and b.get("display_name")}


def one_test(export: Mapping, name: str) -> dict:
    """The session's export cut down to one test: its runs, numbers and earlier results only."""
    out = dict(export)
    out["runs"] = [r for r in export.get("runs") or [] if r.get("benchmark_name") == name]
    out["summaries"] = {k: v for k, v in (export.get("summaries") or {}).items() if k == name}
    out["metric_summaries"] = {k: v for k, v in (export.get("metric_summaries") or {}).items() if k == name}
    out["earlier"] = [e for e in export.get("earlier") or [] if isinstance(e, Mapping)
                      and name in (e.get("name"), e.get("benchmark_name"), e.get("benchmark"))]
    return out


# the numbers worth seeing next to a test's score in the final report (owner, 2026-10-20: "so many points multi-core,
# so many single-core"): (test id pattern, [(metric name pattern, label)]); a {} in the label is the pattern's group
STORAGE = [(r"seq1m_q8t1_read_mib_s", "seq. read"), (r"seq1m_q8t1_write_mib_s", "seq. write"),
           (r"rnd4k_q32t1_read_iops", "4K read"), (r"rnd4k_q32t1_write_iops", "4K write")]
KEY_METRICS = [
    (r"geekbench\d*_cpu", [(r"multi_core_score", "Multi-Core"), (r"single_core_score", "Single-Core")]),
    (r"diskspd|fio", STORAGE),
    (r"llama_benchy.*", [(r"pp_tok_s@d0_c1", "prompt"), (r"tg_tok_s@d0_c1", "generation"),
                         (r"e2e_ttft_ms@d0_c1", "first token")]),
    (r"mlperf_client.*", [(r"ttft_s", "first token")]),
    (r"blender_benchmark.*", [(r"samples_per_minute@(\w+)", "{}")]),
    (r"sevenzip_fm_benchmark", [(r"compress_rating", "compress"), (r"decompress_rating", "decompress")]),
    (r"stress_.*", [(r"(cpu|gpu)_stability", "{} stability"), (r"(cpu|gpu)_errors", "{} wrong answers"),
                    (r"stress_minutes", "length (min)")]),
]


def _number(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}".rstrip("0").rstrip(".")


def key_numbers(export: Mapping, name: str) -> str:
    """"Multi-Core 19,963 points · Single-Core 2,033 points": a test's main numbers besides its score (the mean of
    its measured runs), or ""."""
    metrics = (export.get("metric_summaries") or {}).get(name) or {}
    parts = []
    for test, wanted in KEY_METRICS:
        if not re.fullmatch(test, name):
            continue
        for pattern, label in wanted:
            for metric, m in sorted(metrics.items()):
                found = re.fullmatch(pattern, metric)
                if found and isinstance(m, Mapping) and isinstance(m.get("mean"), (int, float)):
                    unit = m.get("unit") if m.get("unit") not in (None, "-", "count", "min") else ""
                    text = label.format(*(g.upper() if len(g) == 3 else g for g in found.groups()))
                    parts.append(f"{text} {_number(m['mean'])}{' ' + unit if unit else ''}")
        break
    return " · ".join(parts)


def duration_text(seconds: Optional[float]) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "-"
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return (f"{h} h {m} min" if h else f"{m} min {s} s" if m else f"{s} s")


def skipped_note(runs) -> str:
    """The Skip button's note ("Skipped by the user after 2 of 3 runs"), or "" when the test was not skipped."""
    from app.automation.runner import SKIPPED_BY_USER
    return next((str(r.get("note")) for r in runs if r.get("status") == "skipped"
                 and str(r.get("note") or "").startswith(SKIPPED_BY_USER)), "")


def test_outcome(export: Mapping, name: str) -> dict:
    """How one test went: runs, a short result, passed, skipped (Skip button) or not, and the time it took."""
    runs = [r for r in export.get("runs") or [] if r.get("benchmark_name") == name]
    skipped = skipped_note(runs)
    measured = [r for r in runs if not r.get("is_warmup") and not (skipped and r.get("note") == skipped)]
    done = [r for r in measured if r.get("status") == "completed"]
    warmups = sum(1 for r in runs if r.get("is_warmup"))
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
    elif skipped:
        result = skipped
    else:
        last = measured[-1] if measured else (runs[-1] if runs else {})
        why = str(last.get("note") or last.get("status") or "no result").split("; ")[0]
        if last.get("status") == "skipped":      # not run, and why (owner, 2026-10-23)
            result = skipped = f"Skipped: {why}"
        else:
            result = f"FAILED ({last.get('status') or 'no run'}): {why}"
    if skipped and done:
        result += f"; {skipped[0].lower()}{skipped[1:]}"
    count = f"{len(done)} of {len(measured)} measured run(s) completed" + (
        f", {warmups} warm-up" if warmups else "")
    return {"name": name, "passed": bool(done) and not skipped, "skipped": skipped, "result": result, "runs": count,
            "details": key_numbers(export, name) if done else "",
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
    folder = run_folder(export, root)           # the same folder the reports of the finished tests went to
    folder.mkdir(parents=True, exist_ok=True)
    files = {name: write_test_report(export, root, name).name for name in names}
    outcomes = [dict(test_outcome(export, n), title=titles.get(n, n), file=files[n]) for n in names]
    summary = folder / f"Summary_{when}.html"
    text = render_summary_report(export, outcomes)
    partial = summary.with_name(summary.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    partial.replace(summary)
    return summary


def run_folder(export: Mapping, root: Path) -> Path:
    return Path(root) / stamp((export.get("session") or {}).get("created_at"))


def write_test_report(export: Mapping, root: Path, name: str) -> Path:
    """One test's report in the run's folder, written as soon as that test has finished (owner, 2026-10-20: saved
    after each test, only the final report is opened); written again (the same file) at the end."""
    from app.export import write_export
    folder = run_folder(export, root)
    folder.mkdir(parents=True, exist_ok=True)
    when = stamp((export.get("session") or {}).get("created_at"))
    title = display_names(export).get(name, name)
    return write_export(one_test(export, name), folder / f"{safe_name(title)}_{when}.html", "html")


def test_of_step(text: str) -> str:
    """The display name of the test a step's text is about (all of it, ': ' included)."""
    m = STEP.match(str(text or ""))
    return (m.group(1) if m else str(text or "").split(":")[0]).strip()


def finished_tests(export: Mapping, current: str) -> List[str]:
    """The tests of a running plan that are over: every test with a stored run except the one going on now
    (current: the step's text, "<display name>: run 2"); tests run one after the other."""
    titles = display_names(export)
    now = test_of_step(current)
    return [n for n in tested_names(export) if titles.get(n, n) != now and n != now]


def _free(path: Path) -> Path:
    """path, or path with _2, _3... when that name is taken (two runs started in the same second)."""
    if not path.exists():
        return path
    for i in range(2, 1000):
        other = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not other.exists():
            return other
    return path

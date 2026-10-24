"""One package per finished test of a session: what the hardware results library stores (phase 3).

A package is a small JSON file written once and never edited: the PC (unit id, device, OS), the session, the test
(id, version, unit, the settings it ran with), where it is filed (app/library/attribution.py), and the result: the
mean of the measured runs (warm-ups, failed and cancelled runs left out, as in the reports), N, spread, min/max,
every measured score and the extra numbers' means. Its hash covers everything else, so a changed or cut file is
seen. Built from the session export (app/export.py), so it says the same as the session's report.
"""

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Callable, List, Mapping, Optional, Tuple

from app.library.attribution import attribute, flags_of, system_part

FORMAT = "detailsbench.library.package"
FORMAT_VERSION = 1
# Every app reads every package it can (owner, 2026-10-24): a newer app only ADDS fields, which an older one does
# not look at, so FORMAT_VERSION stays as it is. Only when the meaning of a field already there changes (mean
# becomes median, another unit) does READABLE_BY go up: an older app then leaves those packages out and says how
# many need a newer app, instead of comparing numbers that do not mean the same.
READABLE_BY = 1
NOISY_CV = 3.0                  # spread above this gets a warning (plan: "CV over 3% gets a warning")


def accuracy(n: int, cv: Optional[float]) -> str:
    if n <= 0:
        return "no measured run"
    if n == 1:
        return "1 run (low confidence)"
    return f"average of {n}" + (f" (+/-{cv:.1f}%)" if cv is not None else "")


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def content_hash(package: Mapping) -> str:
    body = {k: v for k, v in package.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def safe_name(text) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text))[:80] or "_"


def package_id(unit_id: str, session_id, test_id: str) -> str:
    return f"{safe_name(unit_id)}_s{session_id}_{safe_name(test_id)}"


def _settings(manifest: Mapping, test_id: str) -> dict:
    """What the test ran with, as far as the session recorded it: its entry in the manifest (version, model,
    drive, architecture) and the run settings that change scores."""
    entry = next((b for b in (manifest.get("benchmarks") or []) if b.get("name") == test_id), {}) or {}
    keep = {k: entry.get(k) for k in ("version", "architecture", "model", "drive", "comparable_to_native")
            if entry.get(k) is not None}
    run = manifest.get("run_settings") or {}
    if run.get("stress_minutes"):
        keep["stress_minutes"] = run["stress_minutes"]
    if run.get("ai_model"):
        keep["ai_model"] = run["ai_model"]
    return keep


def _display_name(manifest: Mapping, test_id: str) -> str:
    entry = next((b for b in (manifest.get("benchmarks") or []) if b.get("name") == test_id), {}) or {}
    return entry.get("display_name") or test_id


def build_packages(export: Mapping, read_ai_run: Callable[[str], Optional[dict]] = lambda test_id: None,
                   now: Optional[str] = None) -> Tuple[List[dict], List[Tuple[str, str]]]:
    """(packages, [(test id, why it is left out)]) for one session. A test with no measured run, one the library
    does not take (games for now, stress, the self-test), or a session without its PC's parts gives no package."""
    manifest = export.get("manifest") or {}
    environment = manifest.get("environment") or {}
    hw = environment.get("hardware") or {}
    session = export.get("session") or {}
    left: List[Tuple[str, str]] = []
    if session.get("is_test"):
        return [], [("*", "a test session of the app")]
    if not hw.get("unit_id"):
        return [], [("*", "this session has no record of the PC's parts (made before 0.1.3)")]
    runs_by_test = {}
    for run in export.get("runs") or []:
        runs_by_test.setdefault(run.get("benchmark_name"), []).append(run)
    out = []
    for test_id, summary in sorted((export.get("summaries") or {}).items()):
        runs = runs_by_test.get(test_id, [])
        measured = [r for r in runs if not r.get("is_warmup") and r.get("status") == "completed"
                    and _finite(r.get("score")) and not r.get("is_test")]
        if not measured:
            left.append((test_id, "no measured run"))
            continue
        group = measured[0].get("benchmark_type") or ""
        settings = _settings(manifest, test_id)
        minutes = next((m.get("mean") for name, m in ((export.get("metric_summaries") or {}).get(test_id) or {}).items()
                        if name == "stress_minutes"), None)
        if minutes and "stress_minutes" not in settings:
            settings["stress_minutes"] = minutes        # a stress run compares only with one of the same length
        note = next((r.get("note") for r in measured if r.get("note")), None)
        emulated = settings.get("comparable_to_native") is False
        attr = attribute(test_id, group, hw, ai_run=read_ai_run(test_id) if group == "ai" else None, note=note,
                         drive=settings.get("drive"), flags=flags_of(environment, manifest.get("conditions"),
                                                                     emulated))
        if not attr["library"]:
            left.append((test_id, attr["why_not"]))
            continue
        stats = (summary or {}).get("stats") or {}
        cv = stats.get("cv_percent")
        units = {r.get("unit") for r in measured}
        fingerprint = hashlib.sha256(json.dumps({"test": test_id, **settings}, sort_keys=True,
                                                default=str).encode()).hexdigest()[:16]
        pkg = {
            "format": FORMAT, "format_version": FORMAT_VERSION, "readable_by": READABLE_BY,
            "package_id": package_id(hw["unit_id"], session.get("id"), test_id),
            "created_at": now or datetime.now().isoformat(timespec="seconds"),
            "app": export.get("app"),
            "pc": {"unit_id": hw["unit_id"], "device": hw.get("device"), "os": hw.get("os"),
                   "system_key": (system_part(hw) or {}).get("part_key")},
            "session": {k: session.get(k) for k in ("id", "name", "created_at")},
            "test": {"id": test_id, "name": _display_name(manifest, test_id), "group": group, "version": measured[0].get("benchmark_version"),
                     "unit": units.pop() if len(units) == 1 else None, "settings": settings,
                     "fingerprint": fingerprint},
            "attribution": attr,
            "result": {"mean": stats.get("mean"), "median": stats.get("median"), "min": stats.get("min"),
                       "max": stats.get("max"), "stdev": stats.get("stdev"), "cv_percent": cv,
                       "runs": len(measured), "accuracy": accuracy(len(measured), cv),
                       "noisy": cv is not None and cv > NOISY_CV,
                       "scores": [r["score"] for r in measured],
                       "measured_at": [r.get("timestamp") for r in measured],
                       "metrics": (export.get("metric_summaries") or {}).get(test_id) or {}},
        }
        pkg["hash"] = content_hash(pkg)
        out.append(pkg)
    return out, left


def check(package: Mapping, newest: int = FORMAT_VERSION) -> Optional[str]:
    """None when the package can be read by this app, else why not."""
    if package.get("format") != FORMAT:
        return "not a library package"
    needs = package.get("readable_by", package.get("format_version"))
    if not isinstance(needs, int) or needs > newest:
        return f"needs a newer app (its numbers mean something else in format {needs}): update the app to read it"
    if package.get("hash") != content_hash(package):
        return "the file was changed or cut after it was written"
    return None

"""Plan files: what a run from the Tests screen ran, to run the same again later (ReBench's experiment file idea,
owner 2026-10-19).

Every run started from the Tests screen writes its plan as JSON into <data dir>/plans/ (plan_<date>_<time>.json and
a copy as last.json): the test ids, measured runs, warmup runs, pause, extra runs, stress minutes and the game
preset. "Run a saved plan..." reads one back, ticks the same tests (a test that is gone or can't run is named, not
run) and opens the usual run dialog filled with the saved numbers, so the owner still confirms.
A plan file is data only: it never holds a path or a command, and every value is checked when it is read.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

FORMAT = "pc-benchmark-manager.plan"
VERSION = 1
FOLDER = "plans"
LAST = "last.json"
MAX_BYTES = 256 * 1024
LIMITS = {"repetitions": (1, 50), "warmup_runs": (0, 10), "cooldown_seconds": (0, 3600), "extra_runs": (0, 10)}


def folder(data_dir) -> Path:
    return Path(data_dir) / FOLDER


def plan_dict(test_ids: List[str], choice) -> dict:
    preset = getattr(choice, "preset", None)
    return {"format": FORMAT, "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
            "tests": list(test_ids), "repetitions": int(choice.repetitions), "warmup_runs": int(choice.warmup_runs),
            "cooldown_seconds": int(choice.cooldown_seconds), "extra_runs": int(getattr(choice, "extra_runs", 0)),
            "stress_minutes": getattr(choice, "stress_minutes", None),
            "game_preset": preset.to_dict() if preset is not None else None}


def save(data_dir, test_ids: List[str], choice, now: Optional[datetime] = None) -> Path:
    """Write the plan file and last.json; the plan file's path."""
    out = folder(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(plan_dict(test_ids, choice), indent=2, ensure_ascii=False) + "\n"
    path = out / f"plan_{(now or datetime.now()).strftime('%Y%m%d_%H%M%S')}.json"
    for target in (path, out / LAST):
        tmp = target.with_suffix(".partial")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
    return path


def load(path) -> Tuple[Optional[dict], Optional[str]]:
    """(plan, None) or (None, why). Values out of range or of the wrong type make the file refused."""
    try:
        p = Path(path)
        if p.stat().st_size > MAX_BYTES:
            return None, "the file is too large for a plan"
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"the plan could not be read: {e}"
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None, "this is not a PC Benchmark Manager plan file"
    if data.get("version") != VERSION:
        return None, f"plan version {data.get('version')!r} is not known by this app"
    tests = data.get("tests")
    if not isinstance(tests, list) or not tests or not all(isinstance(t, str) and 0 < len(t) <= 200 for t in tests):
        return None, "the plan names no tests"
    out = {"tests": list(dict.fromkeys(tests))}
    for key, (low, high) in LIMITS.items():
        value = data.get(key, 0 if key == "extra_runs" else None)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return None, f"{key} must be a whole number from {low} to {high}"
        out[key] = value
    minutes = data.get("stress_minutes")
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 600):
        return None, "stress_minutes must be a whole number of minutes"
    out["stress_minutes"] = minutes
    preset = data.get("game_preset")
    if preset is not None and not isinstance(preset, dict):
        return None, "game_preset must be a set of values"
    out["game_preset"] = preset             # GamePreset.from_dict keeps only known values
    out["created"] = str(data.get("created") or "")
    return out, None


def resolve(plan: dict, entries) -> Tuple[list, List[str]]:
    """(entries to run, in the plan's order; ids that are not on this PC now or cannot run)."""
    by_id = {e.id: e for e in entries}
    found, missing = [], []
    for tid in plan["tests"]:
        e = by_id.get(tid)
        (found if e is not None and e.can_run() else missing).append(e if e is not None and e.can_run() else tid)
    return found, missing

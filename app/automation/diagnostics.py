"""Diagnostic snapshot written when a run does not complete.

diagnostics.json holds the command, exit code, error, duration, the last part
of stdout/stderr and a small system snapshot (CPU load, free memory). A
screenshot is taken only when the caller passes a screenshot function (the
GUI does, using Qt); its absence is recorded, not hidden.
"""

import json
import platform
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil

TAIL_CHARS = 4000


def _tail(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"<unreadable: {e}>"
    return text[-TAIL_CHARS:]


def system_snapshot() -> dict:
    snap = {"time": datetime.now().isoformat(timespec="seconds"), "platform": platform.platform()}
    try:
        snap["cpu_percent"] = psutil.cpu_percent(interval=0.2)
        mem = psutil.virtual_memory()
        snap["memory_available_gb"] = round(mem.available / 2**30, 2)
        snap["memory_percent"] = mem.percent
        from app.automation.awake import screen_locked
        snap["screen_locked"] = screen_locked()
    except Exception as e:      # best effort: report, never fail the run because of it
        snap["error"] = str(e)
    return snap


def write_snapshot(directory: Path, workload_id: str, result, ctx_info: dict,
                   screenshot: Optional[Callable[[Path], bool]] = None) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    shot = None
    if screenshot is None:
        shot = {"taken": False, "reason": "no screenshot function (not running in the GUI)"}
    else:
        target = directory / "screenshot.png"
        try:
            ok = bool(screenshot(target))
            shot = {"taken": ok, "path": str(target) if ok else None}
        except Exception as e:
            shot = {"taken": False, "reason": str(e)}
    data = {
        "workload": workload_id,
        "run": ctx_info,
        "result": asdict(result),
        "stdout_tail": _tail(result.stdout_path),
        "stderr_tail": _tail(result.stderr_path),
        "system": system_snapshot(),
        "screenshot": shot,
    }
    path = directory / "diagnostics.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path

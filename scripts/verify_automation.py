#!/usr/bin/env python
"""
Real-machine check of the automation framework (Phase 5), with no mocks:
  1. load data/tools/*.json and print each tool's availability and command;
  2. run the shipped selftest_python spec (1 warmup + 2 measured runs) through
     AutomationRunner and print the stored rows and the raw stdout of each run;
  3. emergency stop: a 30 s child process (which starts its own child) is stopped
     after 1 s; checks that both processes are gone and the run is "cancelled";
  4. timeout: a 30 s child with a 1 s timeout must end as "timed_out" without a score.

By default everything goes into a TEMPORARY database (the production database is untouched).
Usage:
    python scripts/verify_automation.py
    python scripts/verify_automation.py --production   # store step 2 as a TEST session in the real database
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import psutil

from app.automation.cli_workload import CliWorkload, load_workloads
from app.automation.runner import AutomationRunner, PlanItem
from app.automation.tool_spec import validate_spec
from app.database import Database
from app.paths import resolve_database_path

# The child starts a grandchild, prints both PIDs, then sleeps: the stop must end the whole tree.
_TREE = ("import subprocess, sys, time; "
         "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
         "print('PIDS', __import__('os').getpid(), g.pid, flush=True); time.sleep(30)")


def _spec(wid, code, timeout):
    spec, errors = validate_spec({"id": wid, "display_name": wid, "executable": {"python": True},
                                  "args": ["-c", code], "timeout_seconds": timeout,
                                  "parser": {"type": "regex", "pattern": r"^SCORE=(?P<score>\S+)$"},
                                  "verified": True})
    assert not errors, errors
    return CliWorkload(spec)


def _pids_alive(stdout_path):
    for line in Path(stdout_path).read_text(encoding="utf-8").splitlines():
        if line.startswith("PIDS "):
            pids = [int(p) for p in line.split()[1:]]
            return pids, [p for p in pids if psutil.pid_exists(p)
                          and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]
    return [], []


def main(argv) -> int:
    ok = True
    work = Path(tempfile.mkdtemp(prefix="pcbm_automation_"))
    print(f"Run folders: {work}")

    print("\n== 1. Tool specs ==")
    workloads, errors = load_workloads()
    for wl in workloads:
        print(f"{wl.id}: {wl.availability()[1]}\n  command: {wl.describe_command()}")
    for e in errors:
        print(f"SPEC ERROR {e}")
    ok &= not errors

    print("\n== 2. selftest_python through the runner ==")
    if "--production" in argv:
        path, _ = resolve_database_path()
        db = Database(str(path))
    else:
        path = work / "verify.db"
        db = Database(str(path))
    print(f"Database: {path}")
    sid = db.create_session("Automation self-test", "verify_automation.py", is_test=True, is_manual=False)
    selftest = next(w for w in workloads if w.id == "selftest_python")
    summary = AutomationRunner(db, sid, [PlanItem(selftest, repetitions=2, warmup_runs=1)], work / "runs",
                               cooldown_seconds=1, on_progress=lambda m: print("  progress:", m)).run()
    for rec in summary.records:
        raw = Path(rec.result.stdout_path).read_text(encoding="utf-8").strip()
        print(f"  run_index={rec.run_index} warmup={rec.is_warmup} status={rec.result.status} "
              f"score={rec.result.score} exit={rec.result.exit_code} raw stdout={raw!r}")
    rows = db.get_runs(sid, include_test=True)
    for r in rows:
        print(f"  DB row: id={r['id']} run_index={r['run_index']} is_warmup={r['is_warmup']} "
              f"status={r['status']} score={r['score']} unit={r['unit']}")
    ok &= len(rows) == 3 and all(r["status"] == "completed" and r["score"] > 0 for r in rows)
    ok &= db.get_runs(sid) == []          # test data stays out of normal statistics

    print("\n== 3. Emergency stop (process tree) ==")
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    sid2 = db.create_session("Automation stop check", is_test=True)
    started = time.monotonic()
    rec = AutomationRunner(db, sid2, [PlanItem(_spec("selftest_stop", _TREE, 60), 3, 0)], work / "runs",
                           stop_event=stop).run()
    pids, alive = _pids_alive(rec.records[0].result.stdout_path)
    print(f"  status={rec.records[0].result.status} score={rec.records[0].result.score} "
          f"not_started={rec.not_started} took={time.monotonic() - started:.1f}s pids={pids} still alive={alive}")
    ok &= rec.records[0].result.status == "cancelled" and len(pids) == 2 and not alive and rec.not_started == 2

    print("\n== 4. Timeout ==")
    sid3 = db.create_session("Automation timeout check", is_test=True)
    rec = AutomationRunner(db, sid3, [PlanItem(_spec("selftest_timeout", _TREE, 60), 1, 0, timeout_seconds=1)],
                           work / "runs").run()
    result = rec.records[0].result
    pids, alive = _pids_alive(result.stdout_path)
    print(f"  status={result.status} score={result.score} error={result.error!r} pids={pids} still alive={alive}"
          f" diagnostics={rec.records[0].diagnostics}")
    ok &= bool(result.status == "timed_out" and result.score is None and not alive and rec.records[0].diagnostics)

    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Real-machine check (run manually): kill a running plan by force, then resume it from its journal.

A child process runs a plan (the shipped 7-Zip `7z b` spec, 2 measured runs, no warm-up) against a temporary
database; after the first run is stored and the second started, the child and its 7-Zip are ended by force
(like a crash or power loss). A new process then resumes the plan from unfinished_plan.json and the rows of the
session are printed: run 2 failed without a score, run 3 completed... nothing is written to the app's database.
    .venv\\Scripts\\python.exe scripts\\verify_resume.py
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.automation import journal as jr  # noqa: E402
from app.automation.process import terminate_tree  # noqa: E402
from app.automation.runner import AutomationRunner, PlanItem, averages_text, plan_averages  # noqa: E402
from app.database import Database  # noqa: E402

WORKLOAD = "sevenzip_benchmark"


def workload():
    from app.automation.cli_workload import load_workloads
    return next(w for w in load_workloads()[0] if w.id == WORKLOAD)


def child(folder: Path):
    db = Database(str(folder / "resume.db"))
    sid = db.create_session("verify_resume", is_manual=False)
    out = folder / "runs"
    AutomationRunner(db, sid, [PlanItem(workload(), repetitions=3, warmup_runs=0, timeout_seconds=600)], out,
                     journal=jr.PlanJournal(out / jr.JOURNAL_NAME), on_progress=print).run()


def main():
    folder = Path(tempfile.mkdtemp(prefix="pcbm_resume_"))
    path = folder / "runs" / jr.JOURNAL_NAME
    proc = subprocess.Popen([sys.executable, __file__, "--child", str(folder)], cwd=str(ROOT))
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        data = jr.read_journal(path) if path.exists() else None
        if data and data["current"] == [WORKLOAD, 2, False]:
            break
        time.sleep(0.5)
    else:
        print("run 2 never started; journal:", path.read_text(encoding="utf-8") if path.exists() else None)
        terminate_tree(proc.pid)
        return
    time.sleep(3)
    print("killing pids", terminate_tree(proc.pid, grace=0))
    proc.wait(30)
    data = jr.read_journal(path)
    print("journal after the crash:", json.dumps({k: data[k] for k in ("session_id", "done", "current")}))
    print("runs left:", jr.remaining_runs(data))
    plan, done, interrupted, missing = jr.plan_from_journal(data, [workload()])
    db = Database(str(folder / "resume.db"))
    summary = AutomationRunner(db, data["session_id"], plan, folder / "runs", journal=jr.PlanJournal(path),
                               done=done, interrupted=interrupted, stamp=data["stamp"], on_progress=print).run()
    rows = db.get_runs(data["session_id"])
    for r in rows:
        print("row", r["run_index"], r["status"], r["score"], r["unit"], (r.get("note") or "")[:60])
    print("records of the resumed part:", [(r.run_index, r.result.status) for r in summary.records])
    print(averages_text(plan_averages(rows)))
    print("journal left:", path.exists(), "folder:", folder)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        child(Path(sys.argv[2]))
    else:
        main()

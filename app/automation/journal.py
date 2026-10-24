"""Unattended runs: a journal of the running plan, so it can be resumed after a crash or power loss.

The runner writes `unfinished_plan.json` in the output folder before the first run and after every run
(written to a temporary file, then renamed, so a crash never leaves half a file): the session, the plan
(workload ids, repetitions, warm-ups, time limits, chosen settings), the runs already stored and the run
in progress. A plan that ends normally, or is stopped by the user, removes it. If the app ends
during a run, the file stays: on the next start the Automated runs section offers to resume. The
interrupted run is then stored as "failed" (it has no result; never a score) and the runs not yet done
continue in the same session and run folder.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

JOURNAL_NAME = "unfinished_plan.json"
INTERRUPTED_ERROR = ("the app ended during this run (crash, power loss or forced close); "
                     "the run was not finished and has no result")

RunKey = Tuple[str, int, bool]          # workload id, run index, is warm-up


def _key(item) -> RunKey:
    return str(item[0]), int(item[1]), bool(item[2])


def _item(p) -> dict:
    return {"workload_id": p.workload.id, "repetitions": p.repetitions, "warmup_runs": p.warmup_runs,
            "timeout_seconds": p.timeout_seconds, "settings": p.settings,
            "retries": p.retries, "max_extra_runs": p.max_extra_runs}


class PlanJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}

    def _write(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def begin(self, session_id: int, stamp: str, plan, cooldown_seconds: float, done: Sequence[RunKey] = ()):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {
            "session_id": session_id, "stamp": stamp, "cooldown_seconds": cooldown_seconds,
            "created": datetime.now().isoformat(timespec="seconds"),
            "items": [_item(p) for p in plan],
            "done": [list(k) for k in done], "current": None,
        }
        self._write()

    def add_items(self, plan):
        """Tests queued while the plan runs (Queue next): a resumed plan runs them too."""
        self.data["items"] += [_item(p) for p in plan]
        self._write()

    def run_started(self, key: RunKey):
        self.data["current"] = list(key)
        self._write()

    def run_done(self, key: RunKey):
        self.data["done"].append(list(key))
        self.data["current"] = None
        self._write()

    def finish(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def read_journal(path: Path) -> Optional[dict]:
    """The unfinished plan, or None (no file, or a file this app cannot use)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        int(data["session_id"]), str(data["stamp"])
        [_key(k) for k in data["done"]]
        if data.get("current") is not None:
            _key(data["current"])
        for item in data["items"]:
            str(item["workload_id"]), int(item["repetitions"]), int(item["warmup_runs"])
        return data
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def remaining_runs(data: dict) -> int:
    done = {_key(k) for k in data["done"]}
    total = sum(int(i["repetitions"]) + int(i["warmup_runs"]) for i in data["items"])
    return total - len(done)


def plan_from_journal(data: dict, workloads) -> Tuple[list, Set[RunKey], Optional[RunKey], List[str]]:
    """(plan items, runs already done, the interrupted run, ids of workloads no longer available)."""
    from app.automation.runner import PlanItem
    by_id: Dict[str, object] = {w.id: w for w in workloads}
    plan, missing = [], []
    for item in data["items"]:
        wl = by_id.get(item["workload_id"])
        if wl is None or not wl.availability()[0]:
            missing.append(item["workload_id"])
            continue
        if item.get("settings") and hasattr(wl, "configure"):
            wl.configure(item["settings"])
        plan.append(PlanItem(wl, repetitions=int(item["repetitions"]), warmup_runs=int(item["warmup_runs"]),
                             timeout_seconds=item.get("timeout_seconds"), settings=item.get("settings"),
                             retries=int(item.get("retries") or 0), max_extra_runs=int(item.get("max_extra_runs") or 0)))
    current = _key(data["current"]) if data.get("current") is not None else None
    return plan, {_key(k) for k in data["done"]}, current, missing

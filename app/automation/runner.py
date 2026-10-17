"""Run a plan of workloads in sequence and store every run in the database.

For each plan item: warmup runs first (stored with is_warmup, run_index 0..),
then the measured runs (run_index 1..n), with a cooldown between runs. The
emergency stop (stop_event) ends the current run as "cancelled" and starts no
further run; runs that never started are reported, not stored. A run that
does not complete gets a diagnostic snapshot in its run folder.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from app.automation.diagnostics import write_snapshot
from app.automation.workload import RunContext, Workload, WorkloadResult


@dataclass
class PlanItem:
    workload: Workload
    repetitions: int = 3
    warmup_runs: int = 1
    timeout_seconds: Optional[float] = None    # None: the workload's own default


@dataclass
class RunRecord:
    workload_id: str
    run_index: int
    is_warmup: bool
    result: WorkloadResult
    result_id: Optional[int]
    run_dir: Path
    diagnostics: Optional[Path] = None


@dataclass
class RunSummary:
    records: List[RunRecord] = field(default_factory=list)
    not_started: int = 0
    stopped: bool = False


class AutomationRunner:
    def __init__(self, db, session_id: int, plan: List[PlanItem], output_root: Path,
                 cooldown_seconds: float = 0.0, stop_event: Optional[threading.Event] = None,
                 on_progress: Optional[Callable[[str], None]] = None,
                 screenshot: Optional[Callable[[Path], bool]] = None):
        self.db = db
        self.session_id = session_id
        self.plan = plan
        self.output_root = Path(output_root)
        self.cooldown_seconds = cooldown_seconds
        self.stop_event = stop_event or threading.Event()
        self.on_progress = on_progress or (lambda message: None)
        self.screenshot = screenshot

    def stop(self):
        self.stop_event.set()

    def _schedule(self):
        for item in self.plan:
            for i in range(item.warmup_runs):
                yield item, i, True
            for i in range(1, item.repetitions + 1):
                yield item, i, False

    def run(self) -> RunSummary:
        summary = RunSummary()
        schedule = list(self._schedule())
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for position, (item, run_index, is_warmup) in enumerate(schedule):
            if self.stop_event.is_set():
                summary.stopped = True
                summary.not_started = len(schedule) - position
                break
            if position and self.cooldown_seconds > 0:
                self.on_progress(f"Cooling down {self.cooldown_seconds:g} s")
                if self.stop_event.wait(self.cooldown_seconds):
                    summary.stopped = True
                    summary.not_started = len(schedule) - position
                    break
            wl = item.workload
            label = f"{'warmup' if is_warmup else 'run'} {run_index}"
            run_dir = (self.output_root / f"session_{self.session_id}_{stamp}"
                       / f"{wl.id}_{'w' if is_warmup else 'r'}{run_index}")
            run_dir.mkdir(parents=True, exist_ok=True)
            self.on_progress(f"{wl.display_name}: {label} started")
            ctx = RunContext(output_dir=run_dir, timeout_seconds=item.timeout_seconds,
                             stop_event=self.stop_event, run_index=run_index, is_warmup=is_warmup)
            try:
                result = wl.run(ctx)
            except Exception as e:      # a broken workload must not end the whole plan
                result = WorkloadResult(status="failed", error=f"workload raised: {e}")
            record = RunRecord(wl.id, run_index, is_warmup, result, None, run_dir)
            if result.status != "completed":
                record.diagnostics = write_snapshot(
                    run_dir, wl.id, result,
                    {"run_index": run_index, "is_warmup": is_warmup, "timeout": item.timeout_seconds},
                    self.screenshot)
            record.result_id = self.db.create_result(
                self.session_id, wl.id, wl.benchmark_type, result.score, unit=result.unit or "points",
                benchmark_version=wl.version(), is_test=wl.is_test, is_manual=False,
                status=result.status, is_warmup=is_warmup, run_index=run_index,
                note=result.error)
            summary.records.append(record)
            shown = f"{result.score:g} {result.unit or ''}".strip() if result.score is not None else result.error
            self.on_progress(f"{wl.display_name}: {label} {result.status} ({shown})")
            if result.status == "cancelled":
                summary.stopped = True
                summary.not_started = len(schedule) - position - 1
                break
        return summary

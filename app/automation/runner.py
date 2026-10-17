"""Run a plan of workloads in sequence and store every run in the database.

For each plan item: warmup runs first (stored with is_warmup, run_index 0..),
then the measured runs (run_index 1..n), with a cooldown between runs. The
emergency stop (stop_event) ends the current run as "cancelled" and starts no
further run; runs that never started are reported, not stored. A run that
does not complete gets a diagnostic snapshot in its run folder.
"""

import json
import math
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
                 screenshot: Optional[Callable[[Path], bool]] = None,
                 telemetry: Optional[Callable[[], object]] = None):
        self.db = db
        self.session_id = session_id
        self.plan = plan
        self.output_root = Path(output_root)
        self.cooldown_seconds = cooldown_seconds
        self.stop_event = stop_event or threading.Event()
        self.on_progress = on_progress or (lambda message: None)
        self.screenshot = screenshot
        self.telemetry = telemetry          # factory: a new, not started TelemetrySampler per run

    def stop(self):
        self.stop_event.set()

    def _store_metrics(self, result_id: int, metrics: dict):
        """Numbers become rows ({name: value} or {name: (value, unit)}); text metrics are skipped.
        A metrics problem never changes the stored run."""
        rows = {}
        for name, value in metrics.items():
            value, unit = value if isinstance(value, tuple) else (value, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                rows[name] = (value, unit)
        if rows:
            try:
                self.db.save_result_metrics(result_id, rows)
            except Exception as e:
                self.on_progress(f"metrics not stored: {e}")

    def _start_telemetry(self):
        if self.telemetry is None:
            return None
        try:
            return self.telemetry().start()
        except Exception as e:              # telemetry never blocks a run
            self.on_progress(f"telemetry not started: {e}")
            return None

    def _finish_telemetry(self, sampler, run_dir: Path) -> dict:
        """Stop sampling; keep telemetry.csv and telemetry.json in the run folder; return summary metrics."""
        if sampler is None:
            return {}
        from app.telemetry.sampler import summarize, write_csv
        try:
            result = sampler.stop()
            write_csv(result, run_dir / "telemetry.csv")
            (run_dir / "telemetry.json").write_text(json.dumps(
                {"samples": len(result.samples), "unavailable": result.unavailable,
                 "read_errors": result.errors, "last_error": result.last_error}, indent=2), encoding="utf-8")
            return summarize(result)
        except Exception as e:
            self.on_progress(f"telemetry not stored: {e}")
            return {}

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
            sampler = self._start_telemetry()
            try:
                result = wl.run(ctx)
            except Exception as e:      # a broken workload must not end the whole plan
                result = WorkloadResult(status="failed", error=f"workload raised: {e}")
            telemetry_metrics = self._finish_telemetry(sampler, run_dir)
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
            if result.status == "completed" and (result.metrics or telemetry_metrics):
                self._store_metrics(record.result_id, {**telemetry_metrics, **(result.metrics or {})})
            summary.records.append(record)
            shown = f"{result.score:g} {result.unit or ''}".strip() if result.score is not None else result.error
            self.on_progress(f"{wl.display_name}: {label} {result.status} ({shown})")
            if result.status == "cancelled":
                summary.stopped = True
                summary.not_started = len(schedule) - position - 1
                break
        return summary

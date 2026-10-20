"""Run a plan of workloads in sequence and store every run in the database.

For each plan item: warmup runs first (stored with is_warmup, run_index 0..),
then the measured runs (run_index 1..n), with a cooldown between runs. The
emergency stop (stop_event) ends the current run as "cancelled" and starts no
further run; runs that never started are reported, not stored. A run that
does not complete gets a diagnostic snapshot in its run folder. With a journal
(app/automation/journal.py) the plan can be resumed after a crash: runs already
done are skipped and the interrupted one is stored as failed.

Reliability (ReBench's ideas, owner 2026-10-19), both only when the plan item asks for them (Tests screen plans):
- retries: a measured run that ends "failed" or "timed_out" is stored as it is (never hidden) and then run once
  more with the same run number, its note starting "Retry after a failed try". Not for warmups, "could not start"
  (the program is missing: a second try ends the same), "invalid", "skipped" or "cancelled".
- max_extra_runs: when an item's measured runs are done and their scores vary more than CV_LIMIT_PERCENT (at least
  3 scores), one more measured run is added, again until the spread is below the limit or the extra runs are used
  up. Each one's note says why.

Screen (overnight run, 2026-10-20): the whole run holds keep_awake() (app/automation/awake.py) so the screen does
not go off and lock; a workload with needs_desktop (it draws on the screen or needs its window in front) is not
started while the screen is locked: it is stored as "skipped" with the reason instead of waiting for its timeout.
"""

import json
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from app.automation import awake
from app.automation.diagnostics import write_snapshot
from app.automation.journal import INTERRUPTED_ERROR
from app.automation.workload import RunContext, Workload, WorkloadResult


@dataclass
class PlanItem:
    workload: Workload
    repetitions: int = 3
    warmup_runs: int = 1
    timeout_seconds: Optional[float] = None    # None: the workload's own default
    settings: Optional[dict] = None            # chosen before the plan (llama-benchy), kept to resume it
    retries: int = 0                           # a failed measured run is run again this many times
    max_extra_runs: int = 0                    # extra measured runs when the scores vary a lot


CV_LIMIT_PERCENT = 5.0          # coefficient of variation above which the scores are called uneven
RETRY_STATUSES = ("failed", "timed_out")
NO_RETRY_ERRORS = ("could not start",)


def should_retry(result: WorkloadResult) -> bool:
    return (result.status in RETRY_STATUSES
            and not any(str(result.error or "").startswith(e) for e in NO_RETRY_ERRORS))


def spread_percent(scores: List[float]) -> Optional[float]:
    """Coefficient of variation in percent (sample standard deviation / mean), or None below 3 scores."""
    from app.stats import compute_stats
    return compute_stats(scores)["cv_percent"] if len(scores) >= 3 else None


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
                 telemetry: Optional[Callable[[], object]] = None,
                 journal=None, done: Iterable[Tuple[str, int, bool]] = (),
                 interrupted: Optional[Tuple[str, int, bool]] = None, stamp: Optional[str] = None,
                 on_step: Optional[Callable[[int, int, str], None]] = None,
                 screen_locked: Optional[Callable[[], Optional[bool]]] = None,
                 keep_awake: Optional[Callable] = None):
        self.db = db
        self.screen_locked = screen_locked or awake.screen_locked
        self.keep_awake = keep_awake or awake.keep_awake
        self.on_step = on_step or (lambda done, total, text: None)   # runs finished, runs planned, now doing
        self.session_id = session_id
        self.plan = plan
        self.output_root = Path(output_root)
        self.cooldown_seconds = cooldown_seconds
        self.stop_event = stop_event or threading.Event()
        self.on_progress = on_progress or (lambda message: None)
        self.screenshot = screenshot
        self.telemetry = telemetry          # factory: a new, not started TelemetrySampler per run
        self.journal = journal              # PlanJournal, or None
        self.done = set(done)               # resumed plan: runs stored before the crash
        self.interrupted = interrupted      # resumed plan: the run the crash ended
        self.stamp = stamp                  # resumed plan: the same run folder

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

    def _start_telemetry(self, wl=None):
        factory = self.telemetry
        if factory is None and getattr(wl, "benchmark_type", None) == "ai":
            from app.telemetry import sources       # RAM and GPU memory always, for an AI test
            factory = sources.new_memory_sampler
        if factory is None:
            return None
        try:
            return factory().start()
        except Exception as e:              # telemetry never blocks a run
            self.on_progress(f"telemetry not started: {e}")
            return None

    def _finish_telemetry(self, sampler, run_dir: Path, window=None) -> dict:
        """Stop sampling; keep telemetry.csv and telemetry.json in the run folder; return summary metrics
        (over the measured window only, when the workload says which seconds were measured)."""
        self._telemetry_series = {}
        if sampler is None:
            return {}
        from app.telemetry.sampler import chart_series, summarize, write_csv
        try:
            result = sampler.stop()
            try:
                self._telemetry_series = chart_series(result)
            except Exception:
                self._telemetry_series = {}
            missing = f"; not available: {', '.join(sorted(result.unavailable))}" if result.unavailable else ""
            self.on_progress(f"telemetry: {len(result.samples)} samples{missing}")
            write_csv(result, run_dir / "telemetry.csv")
            (run_dir / "telemetry.json").write_text(json.dumps(
                {"samples": len(result.samples), "read": getattr(result, "available", {}),
                 "unavailable": result.unavailable,
                 "read_errors": result.errors, "last_error": result.last_error,
                 "summary_window_seconds": list(window) if window else None}, indent=2), encoding="utf-8")
            summary = summarize(result, window) if window else {}
            return summary or summarize(result)
        except Exception as e:
            self.on_progress(f"telemetry not stored: {e}")
            return {}

    def _schedule(self):
        for item in self.plan:
            for i in range(item.warmup_runs):
                yield item, i, True
            for i in range(1, item.repetitions + 1):
                yield item, i, False

    def _store_interrupted(self, summary: RunSummary, stamp: str):
        wid, run_index, is_warmup = self.interrupted
        item = next((p for p in self.plan if p.workload.id == wid), None)
        if item is None:
            return
        result = WorkloadResult(status="failed", error=INTERRUPTED_ERROR)
        run_dir = self.output_root / f"session_{self.session_id}_{stamp}" / f"{wid}_{'w' if is_warmup else 'r'}{run_index}"
        result_id = self.db.create_result(
            self.session_id, wid, item.workload.benchmark_type, None, unit="points",
            benchmark_version=item.workload.version(), is_test=item.workload.is_test, is_manual=False,
            status="failed", is_warmup=is_warmup, run_index=run_index, note=INTERRUPTED_ERROR)
        summary.records.append(RunRecord(wid, run_index, is_warmup, result, result_id, run_dir))
        self.done.add(self.interrupted)
        if self.journal is not None:
            self.journal.run_done(self.interrupted)
        self.on_progress(f"{item.workload.display_name}: {'warmup' if is_warmup else 'run'} {run_index} "
                         f"was interrupted by a crash; stored as failed")

    def run(self) -> RunSummary:
        summary = RunSummary()
        stamp = self.stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.journal is not None:
            self.journal.begin(self.session_id, stamp, self.plan, self.cooldown_seconds, sorted(self.done))
        if self.interrupted is not None and self.interrupted not in self.done:
            self._store_interrupted(summary, stamp)
        schedule = [s for s in self._schedule() if (s[0].workload.id, s[1], s[2]) not in self.done]
        with self.keep_awake():
            summary = self._run_schedule(summary, schedule, stamp)
        if self.journal is not None:
            self.journal.finish()           # ended normally or stopped by the user: nothing to resume
        return summary

    def _scores(self, summary: RunSummary, wid: str) -> List[float]:
        return [r.result.score for r in summary.records if r.workload_id == wid and not r.is_warmup
                and r.result.status == "completed" and r.result.score is not None]

    def _run_schedule(self, summary: RunSummary, schedule: list, stamp: str) -> RunSummary:
        schedule = [(item, run_index, is_warmup, "") for item, run_index, is_warmup in schedule]
        retried: Dict[Tuple[str, int], int] = {}
        extra: Dict[str, int] = {}
        position = -1
        while position + 1 < len(schedule):
            position += 1
            total = len(schedule)
            item, run_index, is_warmup, why = schedule[position]
            if self.stop_event.is_set():
                summary.stopped = True
                summary.not_started = len(schedule) - position
                break
            if position and self.cooldown_seconds > 0:
                self.on_progress(f"Cooling down {self.cooldown_seconds:g} s")
                self.on_step(position, total, f"Pause {self.cooldown_seconds:g} s before the next run")
                if self.stop_event.wait(self.cooldown_seconds):
                    summary.stopped = True
                    summary.not_started = len(schedule) - position
                    break
            wl = item.workload
            label = f"{'warmup' if is_warmup else 'run'} {run_index}" + (" (retry)" if why.startswith("Retry") else
                                                                       " (extra)" if why else "")
            tries = retried.get((wl.id, run_index), 0)
            run_dir = (self.output_root / f"session_{self.session_id}_{stamp}"
                       / (f"{wl.id}_{'w' if is_warmup else 'r'}{run_index}" + (f"_retry{tries}" if tries else "")))
            run_dir.mkdir(parents=True, exist_ok=True)
            self.on_progress(f"{wl.display_name}: {label} started")
            self.on_step(position, total, f"{wl.display_name}: {label}")
            ctx = RunContext(output_dir=run_dir, timeout_seconds=item.timeout_seconds,
                             stop_event=self.stop_event, run_index=run_index, is_warmup=is_warmup)
            key = (wl.id, run_index, is_warmup)
            if self.journal is not None:
                self.journal.run_started(key)
            sampler = self._start_telemetry(wl)
            try:
                if getattr(wl, "needs_desktop", False) and self.screen_locked():
                    result = WorkloadResult(status="skipped", error=awake.LOCKED_ERROR)
                else:
                    result = wl.run(ctx)
            except Exception as e:      # a broken workload must not end the whole plan
                result = WorkloadResult(status="failed", error=f"workload raised: {e}")
            telemetry_metrics = self._finish_telemetry(sampler, run_dir, result.measured_window)
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
                note="; ".join(p for p in (why, result.error or result.note) if p) or None,
                duration_seconds=result.duration_seconds)
            if result.status == "completed" and (result.metrics or telemetry_metrics):
                self._store_metrics(record.result_id, {**telemetry_metrics, **(result.metrics or {})})
            if result.status == "completed" and (result.series or getattr(self, "_telemetry_series", None)):
                try:
                    self.db.save_result_series(record.result_id,
                                               {**getattr(self, "_telemetry_series", {}), **(result.series or {})})
                except Exception as e:
                    self.on_progress(f"charts not stored: {e}")
            if self.journal is not None:
                self.journal.run_done(key)
            summary.records.append(record)
            shown = f"{result.score:g} {result.unit or ''}".strip() if result.score is not None else result.error
            self.on_progress(f"{wl.display_name}: {label} {result.status} ({shown})")
            self.on_step(position + 1, total, f"{wl.display_name}: {label} {result.status} ({shown})")
            if result.status == "cancelled":
                summary.stopped = True
                summary.not_started = len(schedule) - position - 1
                break
            if is_warmup or self.stop_event.is_set():
                continue
            if should_retry(result) and tries < item.retries:
                retried[(wl.id, run_index)] = tries + 1
                schedule.insert(position + 1, (item, run_index, False,
                                               f"Retry after a failed try ({result.status}: {result.error or '-'})"))
                self.on_progress(f"{wl.display_name}: {label} {result.status}; it is run once more")
                continue
            later = any(s[0] is item for s in schedule[position + 1:])
            if not later and extra.get(wl.id, 0) < item.max_extra_runs:
                cv = spread_percent(self._scores(summary, wl.id))
                if cv is not None and cv > CV_LIMIT_PERCENT:
                    extra[wl.id] = extra.get(wl.id, 0) + 1
                    nxt = max(r.run_index for r in summary.records if r.workload_id == wl.id and not r.is_warmup) + 1
                    schedule.insert(position + 1, (item, nxt, False,
                                                   f"Extra run: the scores varied {cv:.1f}% (above "
                                                   f"{CV_LIMIT_PERCENT:g}%), extra run {extra[wl.id]} of "
                                                   f"{item.max_extra_runs}"))
                    self.on_progress(f"{wl.display_name}: scores vary {cv:.1f}%; one more run")
        return summary


def plan_averages(rows: List[dict]) -> Dict[str, dict]:
    """Per benchmark: statistics of the completed measured runs of a session (Database.get_runs rows;
    warm-ups and failures left out). Rows, not the runner's records, so a resumed plan counts the runs
    stored before the crash too."""
    from app.stats import compute_stats
    scores: Dict[str, list] = {}
    units: Dict[str, Optional[str]] = {}
    for row in rows:
        if row["is_warmup"] or row["status"] != "completed" or row["score"] is None:
            continue
        scores.setdefault(row["benchmark_name"], []).append(row["score"])
        units.setdefault(row["benchmark_name"], row["unit"])
    return {wid: {**compute_stats(values), "unit": units[wid]} for wid, values in scores.items()}


def averages_text(averages: Dict[str, dict]) -> str:
    lines = []
    for wid, s in averages.items():
        spread = f", CV {s['cv_percent']:.1f}%" if s.get("cv_percent") is not None else ""
        lines.append(f"{wid}: mean {s['mean']:.6g} {s['unit'] or ''} over {s['count']} run(s){spread}".replace("  ", " "))
    return "\n".join(lines)

"""Benchmark one Steam game end to end: start PresentMon, launch the game, capture, store.

One UAC prompt per benchmark: PresentMon starts elevated ONCE, before the game, and records every
process (no process filter, because the game's exe name is known only after it starts). Then the game
is launched through Steam (app/steam/launch.py rules: the appid comes from a local manifest, a new
process inside the game's install folder confirms it). While PresentMon writes, its CSV is read every
second; the game's process is the one in the install folder with the most frames. When the game's
summed frame times cover the settle time plus every capture window, PresentMon (our own elevated child)
is stopped. The recording is cut into warmup and measured windows (app/frames/segments.py); each
window is one stored run (score = average FPS). A window the recording does not reach is stored as
"invalid" without a score. Telemetry samples are cut into the same windows; their alignment with the
frames is approximate (about one second, the live check's interval).

The game itself is never closed by this app; the user closes it.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.automation.workload import RunContext, Workload, WorkloadResult
from app.frames.elevated import run_elevated
from app.frames.presentmon import CSV_NAME, START_MARGIN_SECONDS, capture_all_args, workload_id_for
from app.frames.presentmon_csv import parse_presentmon_csv, summarize_processes
from app.frames.segments import (
    first_frame_offset, game_seconds, pick_game_process, slice_samples, split_frames, window_bounds,
)
from app.frames.stats import compute_frame_stats, stats_to_metrics

logger = logging.getLogger(__name__)

UAC_WAIT_SECONDS = 120.0          # time to answer the UAC prompt (PresentMon's CSV appears after it)
LAUNCH_TIMEOUT_SECONDS = 120.0    # Steam may show its own dialog or update check first
STALL_SECONDS = 30.0              # no new game frames for this long: the game was closed or paused


@dataclass
class GameBenchmarkSpec:
    game_name: str
    app_id: str
    install_path: str
    discovered_appids: List[str]
    presentmon_path: str
    seconds: int = 60
    warmup_runs: int = 1
    repetitions: int = 3
    settle_seconds: int = 30
    min_frames: int = 100

    @property
    def count(self) -> int:
        return self.warmup_runs + self.repetitions

    def windows(self) -> List[Tuple[float, float]]:
        return window_bounds(self.settle_seconds, self.seconds, self.count)

    def max_record_seconds(self) -> int:
        """PresentMon's --timed limit: enough for launch, settle and every window, then it stops itself."""
        return int(UAC_WAIT_SECONDS + LAUNCH_TIMEOUT_SECONDS + self.settle_seconds
                   + self.seconds * self.count + STALL_SECONDS + 60)


@dataclass
class WindowOutcome:
    is_warmup: bool
    run_index: int
    result: WorkloadResult
    samples: list = field(default_factory=list)


@dataclass
class GameCapture:
    status: str                                   # "recorded" (windows below), "cancelled", "failed"
    message: str = ""
    process: Optional[Tuple[str, Optional[int]]] = None
    windows: List[WindowOutcome] = field(default_factory=list)
    command: Optional[list] = None
    telemetry_unavailable: dict = field(default_factory=dict)


def _exe_names_in(snapshot, folder: str) -> set:
    root = os.path.normcase(os.path.normpath(folder)) + os.sep
    return {os.path.basename(path) for _, path in snapshot
            if path and os.path.normcase(os.path.normpath(path)).startswith(root)}


class GameBenchmark:
    def __init__(self, spec: GameBenchmarkSpec, run_dir: Path, stop_event: Optional[threading.Event] = None,
                 on_progress: Callable[[str], None] = lambda m: None,
                 sampler_factory: Optional[Callable[[], object]] = None,
                 elevated_runner: Callable = run_elevated, launcher: Optional[Callable] = None,
                 snapshot: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, poll_seconds: float = 1.0):
        self.spec = spec
        self.run_dir = Path(run_dir)
        self.stop_event = stop_event or threading.Event()
        self.on_progress = on_progress
        self.sampler_factory = sampler_factory
        self.elevated_runner = elevated_runner
        if launcher is None:
            from app.steam.launch import launch_and_confirm
            launcher = launch_and_confirm
        self.launcher = launcher
        if snapshot is None:
            from app.benchmark_manager import _get_process_snapshot
            snapshot = _get_process_snapshot
        self.snapshot = snapshot
        self.clock = clock
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.csv_path = self.run_dir / CSV_NAME

    def _read_csv(self) -> Optional[str]:
        try:
            return self.csv_path.read_text(encoding="utf-8", errors="replace")
        except OSError:           # not written yet, or locked for a moment by PresentMon
            return None

    def run(self) -> GameCapture:
        spec = self.spec
        self.run_dir.mkdir(parents=True, exist_ok=True)
        args = capture_all_args(str(self.csv_path), spec.max_record_seconds())
        command = [spec.presentmon_path] + args
        halt = threading.Event()        # stops our elevated PresentMon (user stop, or enough recorded)
        outcome = {}

        def record():
            outcome["out"] = self.elevated_runner(spec.presentmon_path, args, str(self.run_dir),
                                                  spec.max_record_seconds() + START_MARGIN_SECONDS, halt)
        thread = threading.Thread(target=record, name="presentmon", daemon=True)
        self.on_progress("Starting PresentMon (accept the UAC prompt)...")
        thread.start()

        def finish(status, message, **kw) -> GameCapture:
            halt.set()
            thread.join(30)
            return GameCapture(status, message, command=command, **kw)

        deadline = self.clock() + UAC_WAIT_SECONDS
        while not self.csv_path.exists():
            if self.stop_event.is_set():
                return finish("cancelled", "stopped by user before the game was started")
            if not thread.is_alive():
                out = outcome.get("out")
                if out is not None and out.status == "uac_declined":
                    return finish("cancelled", "UAC prompt declined; the game was not started")
                return finish("failed", "PresentMon ended before recording: "
                              + (getattr(out, "error", None) or f"exit code {getattr(out, 'exit_code', None)}"))
            if self.clock() >= deadline:
                return finish("failed", f"PresentMon wrote nothing within {UAC_WAIT_SECONDS:g} s; the game was not started")
            self.sleep(0.5)

        self.on_progress(f"PresentMon recording; launching {spec.game_name} through Steam...")
        launched = self.launcher(spec.app_id, spec.install_path, spec.discovered_appids, snapshot=self.snapshot,
                                 timeout=LAUNCH_TIMEOUT_SECONDS, poll=1.0, sleep=self.sleep,
                                 clock=lambda: float("inf") if self.stop_event.is_set() else self.clock())
        if self.stop_event.is_set():
            return finish("cancelled", "stopped by user while the game was starting")
        if launched.get("status") != "launched":
            return finish("failed", f"the game did not start: {launched.get('message')}")

        sampler = None
        if self.sampler_factory is not None:
            try:
                sampler = self.sampler_factory().start()
            except Exception as e:
                self.on_progress(f"telemetry not started: {e}")
        t0 = self.clock()
        windows = spec.windows()
        needed = windows[-1][1]
        names, pick, offset = set(), None, None
        covered, last_growth = 0.0, self.clock()
        stopped = False
        while True:
            if self.stop_event.is_set():
                stopped = True
                break
            if not thread.is_alive():
                break
            names |= _exe_names_in(self.snapshot(), spec.install_path)
            text = self._read_csv()
            if text:
                now_pick = pick_game_process(summarize_processes(text), names)
                if now_pick is not None:
                    frames = parse_presentmon_csv(text, process_id=now_pick[1]).frametimes_ms
                    seconds = game_seconds(frames)
                    if now_pick != pick:
                        pick, offset = now_pick, first_frame_offset(self.clock() - t0, seconds)
                    if seconds > covered:
                        covered, last_growth = seconds, self.clock()
                    shown = min(covered, needed)
                    self.on_progress(f"{spec.game_name} ({pick[0]}): {shown:.0f} of {needed:.0f} s recorded "
                                     f"(first {spec.settle_seconds} s skipped as loading)")
                    if covered >= needed:
                        break
            if pick is not None and self.clock() - last_growth >= STALL_SECONDS:
                break                   # the game stopped presenting (closed, minimized or paused)
            if pick is None and self.clock() - t0 >= LAUNCH_TIMEOUT_SECONDS + spec.settle_seconds:
                break
            self.sleep(self.poll_seconds)

        halt.set()
        thread.join(30)
        tel = sampler.stop() if sampler is not None else None
        if pick is None and stopped:
            return GameCapture("cancelled", "stopped by user before the game showed frames", command=command)
        if pick is None:
            return GameCapture("failed", "PresentMon recorded no frames from the game's process "
                               f"(processes in its folder: {', '.join(sorted(names)) or 'none'})",
                               command=command)
        text = self._read_csv() or ""
        parsed = parse_presentmon_csv(text, process_id=pick[1])
        frame_windows = split_frames(parsed.frametimes_ms if not parsed.error else [], windows)
        sample_windows = slice_samples(tel.samples if tel else [], offset or 0.0, windows)
        recorded = game_seconds(parsed.frametimes_ms) if not parsed.error else 0.0
        common = {"command": command}
        out = []
        for k, frames in enumerate(frame_windows):
            is_warmup = k < spec.warmup_runs
            run_index = k if is_warmup else k - spec.warmup_runs + 1
            a, b = windows[k]
            if frames is None:
                if stopped:
                    result = WorkloadResult(status="cancelled", error="stopped by user", **common)
                else:
                    result = WorkloadResult(status="invalid", error=(
                        f"the recording ended at {recorded:.0f} s of game time; this window needs {a:g}-{b:g} s"),
                        **common)
            else:
                stats = compute_frame_stats(frames, spec.min_frames)
                if stats.error:
                    result = WorkloadResult(status="invalid", error=f"capture not usable: {stats.error}", **common)
                else:
                    metrics = stats_to_metrics(stats)
                    metrics["window_start_s"] = (float(a), "s")
                    result = WorkloadResult(status="completed", score=stats.avg_fps, unit="fps",
                                            metrics=metrics, duration_seconds=float(b - a), **common)
            out.append(WindowOutcome(is_warmup, run_index, result, sample_windows[k]))
        return GameCapture("recorded", f"{recorded:.0f} s of {pick[0]} recorded", process=pick, windows=out,
                           command=command, telemetry_unavailable=dict(tel.unavailable) if tel else {})


class RecordedWindow(Workload):
    """Hands one already recorded window to AutomationRunner, so it is stored like any other run."""

    benchmark_type = "game"

    def __init__(self, workload_id: str, display_name: str, outcomes: List[WindowOutcome],
                 version: Optional[str] = None, unavailable: Optional[dict] = None):
        self.id = workload_id
        self.display_name = display_name
        self._outcomes = list(outcomes)
        self._version = version
        self._unavailable = unavailable or {}

    def version(self) -> Optional[str]:
        return self._version

    def run(self, ctx: RunContext) -> WorkloadResult:
        outcome = self._outcomes.pop(0)
        result = outcome.result
        if outcome.samples:
            from app.telemetry.sampler import TelemetryResult, summarize, write_csv
            tel = TelemetryResult(samples=outcome.samples, unavailable=dict(self._unavailable))
            write_csv(tel, Path(ctx.output_dir) / "telemetry.csv")
            if result.status == "completed":
                result.metrics = {**summarize(tel), **result.metrics}
        return result


def plan_for(capture: GameCapture, spec: GameBenchmarkSpec, version: Optional[str] = None):
    """AutomationRunner plan that stores every recorded window (warmups first, as the runner orders them)."""
    from app.automation.runner import PlanItem
    name = capture.process[0] if capture.process else spec.game_name
    workload = RecordedWindow(workload_id_for(name), f"Game benchmark: {spec.game_name}",
                              capture.windows, version, capture.telemetry_unavailable)
    return [PlanItem(workload, repetitions=spec.repetitions, warmup_runs=spec.warmup_runs)]

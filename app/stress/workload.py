"""Stress test workloads (Tests screen, group "Stress tests"): CPU, GPU, or both together, for a set number of
minutes (settings.json "stress_minutes", chosen in the run dialog).

Every second the work done is recorded (CPU answers, GPU passes). The score is the stability: the slowest
10-second stretch as a percentage of the fastest one (like 3DMark's stress tests; 97 % or more is stable).
A wrong answer from the self-check is an error: the run still completes (its charts and numbers are kept) but it
is marked NOT stable. Power, temperatures and clocks come from the run's telemetry like every other test.
"""

import time
from typing import Callable, Dict, List, Optional, Tuple

from app.automation.workload import RunContext, Workload, WorkloadResult

MINUTES_KEY = "stress_minutes"
DEFAULT_MINUTES = 10
MINUTE_CHOICES = (1, 5, 10, 20, 30, 60)
WINDOW_SECONDS = 10
STABLE_PERCENT = 97.0
JUDGE_MINUTES = 3                                   # shorter runs are mostly warm-up: no stable / NOT stable
MODES = {
    "cpu": ("stress_cpu", "Stress test: CPU"),
    "gpu": ("stress_gpu", "Stress test: GPU"),
    "both": ("stress_cpu_gpu", "Stress test: CPU + GPU together"),
}


def stress_minutes(data_dir=None) -> int:
    from app.paths import get_data_dir, load_settings
    try:
        value = load_settings(data_dir or get_data_dir())[0].get(MINUTES_KEY, DEFAULT_MINUTES)
    except (OSError, ValueError):
        return DEFAULT_MINUTES
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 240 else DEFAULT_MINUTES


def warm_up_seconds(total: int) -> int:
    """Seconds left out of the stability at the start: the parts speed up and (on a laptop) share out the power
    over the first half minute, so a run of 3 minutes or more skips its first minute, a shorter one 10 s."""
    return 60 if total >= 180 else WINDOW_SECONDS


def stability(per_second: List[float], window: int = WINDOW_SECONDS, skip: Optional[int] = None) -> Optional[float]:
    """Slowest window / fastest window x 100 over the per-second work (the first skip seconds are the ramp up)."""
    skip = warm_up_seconds(len(per_second)) if skip is None else skip
    values = per_second[skip:] if len(per_second) >= skip + 2 * window else per_second
    if len(values) < window:
        return None
    sums = [sum(values[i:i + window]) for i in range(0, len(values) - window + 1)]
    best = max(sums)
    return 100.0 * min(sums) / best if best > 0 else None


class StressWorkload(Workload):
    benchmark_type = "stress"

    def __init__(self, mode: str, minutes: Optional[Callable[[], int]] = None, cpu_factory=None, gpu_factory=None,
                 clock: Callable[[], float] = time.monotonic, tick: float = 1.0):
        self.mode = mode
        self.id, self.display_name = MODES[mode]
        self.minutes = minutes or stress_minutes
        self.cpu_factory = cpu_factory
        self.gpu_factory = gpu_factory
        self.clock = clock
        self.tick = tick

    @property
    def timeout_seconds(self) -> float:
        return self.minutes() * 60 + 180            # start-up and stopping on top of the stress time

    verified = True
    progress = None                                 # (fraction, seconds left) while running, for the progress bar
    fixed_counts = (0, 1)                           # no warm-up, one run: the minutes are the test

    def availability(self) -> Tuple[bool, str]:
        from app.stress import cpu, gpu
        parts = []
        if self.mode in ("cpu", "both"):
            ok, why = cpu.available()
            if not ok:
                return False, why
            parts.append(why)
        if self.mode in ("gpu", "both"):
            ok, why = gpu.available()
            if not ok:
                return False, why
            parts.append(why)
        return True, f"{self.minutes()} min; " + ", ".join(parts)

    def describe_command(self) -> Optional[str]:
        return f"{self.display_name}, {self.minutes()} minutes (built in: no program is started)"

    def _components(self):
        import os
        from app.stress.cpu import CpuStress
        from app.stress.gpu import GpuStress
        out = {}
        if self.mode in ("cpu", "both"):
            workers = max(1, (os.cpu_count() or 2) - 1) if self.mode == "both" else None   # one CPU feeds the GPU
            out["cpu"] = (self.cpu_factory or (lambda: CpuStress(workers, low_priority=self.mode == "both")))()
        if self.mode in ("gpu", "both"):
            out["gpu"] = (self.gpu_factory or GpuStress)()
        return out

    def run(self, ctx: RunContext) -> WorkloadResult:
        seconds = self.minutes() * 60
        started = self.clock()
        self.progress = None
        parts = self._components()
        running = {}
        try:
            for name, part in parts.items():
                try:
                    running[name] = part.start()
                except Exception as e:
                    return WorkloadResult("failed", error=f"the {name.upper()} stress could not start: {e}",
                                          duration_seconds=self.clock() - started)
            t0 = self.clock()
            last = {n: p.read() for n, p in running.items()}
            per_second: Dict[str, List[float]] = {n: [] for n in running}
            times: List[float] = []
            status, error = "completed", None
            while self.clock() - t0 < seconds:
                if ctx.stop_event.wait(self.tick):
                    status, error = "cancelled", "stopped by the user"
                    break
                now = self.clock()
                self.progress = (min(1.0, (now - t0) / seconds), max(0.0, seconds - (now - t0)))
                times.append(round(now - started, 1))
                for name, part in running.items():
                    done, errs = part.read()
                    per_second[name].append(float(done - last[name][0]))
                    last[name] = (done, errs)
                dead = [n for n, p in running.items() if not _alive(p)]
                if dead:
                    status = "failed"
                    error = f"the {', '.join(n.upper() for n in dead)} stress stopped by itself after " \
                            f"{now - t0:.0f} s" + _why(running, dead)
                    break
            measured = (t0 - started, self.clock() - started)
        finally:
            self.progress = None
            for part in running.values():
                part.stop()
        duration = self.clock() - started
        if status != "completed":
            return WorkloadResult(status, error=error, duration_seconds=duration)
        metrics, notes, scores = {"stress_minutes": (float(self.minutes()), "min")}, [], []
        judged = self.minutes() >= JUDGE_MINUTES
        series = {}
        for name in running:
            work = per_second[name]
            errors = last[name][1]
            score = stability(work)
            metrics[f"{name}_errors"] = (float(errors), "count")
            if score is not None:
                metrics[f"{name}_stability"] = (score, "%")
                scores.append(score)
            best = max(work) if work else 0
            series[f"{name}_work"] = ("% of best second", [(t, 100.0 * w / best if best else 0.0)
                                                         for t, w in zip(times, work)])
            verdict = ("NOT stable" if errors or (score is not None and score < STABLE_PERCENT) else "stable")                 if judged or errors else f"too short to judge (run {JUDGE_MINUTES} min or more)"
            notes.append(f"{name.upper()} {verdict}: stability {score:.0f} %, {errors} wrong answers"
                         if score is not None else f"{name.upper()}: too short to judge, {errors} wrong answers")
        if not scores:
            return WorkloadResult("invalid", error="the stress ran too briefly to measure its stability",
                                  duration_seconds=duration)
        return WorkloadResult("completed", score=min(scores), unit="%", metrics=metrics, duration_seconds=duration,
                              note=f"Stress: {self.minutes()} min; " + "; ".join(notes), measured_window=measured,
                              series=series)


def _alive(part) -> bool:
    """Every CPU worker process (alive() counts them) or the GPU thread is still running."""
    alive = part.alive()
    if isinstance(alive, int) and not isinstance(alive, bool):
        return alive >= getattr(part, "workers", 1)
    return bool(alive)


def _why(running, dead) -> str:
    reasons = [getattr(running[n], "failure", None) for n in dead]
    reasons = [r for r in reasons if r]
    return f" ({'; '.join(reasons)})" if reasons else " (a crash of the stress is itself a sign of instability)"


def stress_workloads() -> list:
    return [StressWorkload(mode) for mode in MODES]

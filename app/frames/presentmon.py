"""Frame capture with the official PresentMon command line (Intel, GameTechDev/PresentMon).

The user downloads PresentMon and selects the file once; the path is kept in
settings.json ("presentmon_path"). We never search for or use the copies bundled
inside other products (for example NVIDIA FrameView).

PresentMon reads ETW events, which needs administrator rights (or membership in
"Performance Log Users"; not used here). Every capture therefore runs elevated
through the UAC prompt, after the user confirms, while the app stays non-elevated.
PresentMon stops itself after the capture time (--timed with --terminate_after_timed).
Only the PresentMon process we started is ever terminated; the game is never touched.

CAPTURE_FLAGS_VERIFIED: verified with PresentMon 2.6.0 x64 by scripts/verify_frames.py on
2026-10-17 (--help lists every flag; a real 10 s capture at the local console gave 590 frames,
59.4 FPS). Captures inside a Remote Desktop session recorded no frames on that machine.
"""

import re
from pathlib import Path
from typing import Callable, List, Optional

from app.automation.workload import RunContext, Workload, WorkloadResult
from app.frames.elevated import run_elevated
from app.frames.presentmon_csv import parse_presentmon_csv
from app.frames.stats import compute_frame_stats, stats_to_metrics

SETTING_KEY = "presentmon_path"
CSV_NAME = "presentmon.csv"
SESSION_NAME = "PCBenchmarkManager"      # own ETW session name; never stops another tool's session
CAPTURE_FLAGS = ("--process_name", "--process_id", "--output_file", "--timed",
                 "--terminate_after_timed", "--session_name")
CAPTURE_FLAGS_VERIFIED = True
START_MARGIN_SECONDS = 30                # PresentMon startup and CSV flush on top of the capture time


def check_presentmon_path(path: Optional[str]) -> Optional[str]:
    """None when usable, else the reason."""
    if not path:
        return "PresentMon not selected"
    p = Path(path)
    if not p.is_file():
        return f"file not found: {path}"
    if p.suffix.lower() != ".exe":
        return "not an .exe file"
    if "frameview" in str(p).lower():
        return "this copy is bundled inside NVIDIA FrameView; select the official PresentMon download"
    return None


def capture_args(output_file: str, seconds: int, process_name: Optional[str] = None,
                 process_id: Optional[int] = None) -> List[str]:
    if (process_name is None) == (process_id is None):
        raise ValueError("give exactly one of process_name / process_id")
    target = ["--process_name", process_name] if process_name else ["--process_id", str(process_id)]
    return target + ["--output_file", output_file, "--timed", str(int(seconds)), "--terminate_after_timed",
                     "--session_name", SESSION_NAME]


def capture_game_args(output_file: str, exe_names: List[str], seconds: int) -> List[str]:
    """Every exe of a game's folder (--process_name repeated), started BEFORE the game. PresentMon ends by
    itself when the game's processes have exited (--terminate_on_proc_exit), which writes the whole CSV;
    --timed is only the upper limit. (PresentMon keeps its CSV locked while it writes, so it cannot be
    read live.) Flags checked in PresentMon 2.6.0 --help on 2026-10-17."""
    args = []
    for name in exe_names:
        args += ["--process_name", name]
    return args + ["--output_file", output_file, "--terminate_on_proc_exit", "--timed", str(int(seconds)),
                   "--terminate_after_timed", "--session_name", SESSION_NAME]


def workload_id_for(process_name: str) -> str:
    stem = re.sub(r"\.exe$", "", process_name.strip(), flags=re.IGNORECASE)
    return "frames_" + (re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_") or "process")


class FrameCaptureWorkload(Workload):
    """One timed PresentMon capture of one process; score = average FPS."""

    benchmark_type = "game"

    def __init__(self, presentmon_path: str, process_name: str, seconds: int = 60,
                 process_id: Optional[int] = None, min_frames: int = 100,
                 elevated_runner: Callable = run_elevated, version_reader: Optional[Callable] = None):
        self.presentmon_path = presentmon_path
        self.process_name = process_name
        self.process_id = process_id
        self.seconds = int(seconds)
        self.min_frames = min_frames
        self.id = workload_id_for(process_name)
        self.display_name = f"Frame capture: {process_name}"
        self.is_test = False
        self._run_elevated = elevated_runner
        self._version_reader = version_reader

    def availability(self):
        problem = check_presentmon_path(self.presentmon_path)
        return (False, problem) if problem else (True, self.presentmon_path)

    def version(self) -> Optional[str]:
        """PresentMon's file version, read from the file (never by running it)."""
        if self._version_reader is None:
            from app.benchmark_manager import get_pe_version
            self._version_reader = get_pe_version
        try:
            return self._version_reader(self.presentmon_path)
        except Exception:
            return None

    def _args(self, output_file: str) -> List[str]:
        # a pid wins over the name: it pins one process when several share the name
        if self.process_id is not None:
            return capture_args(output_file, self.seconds, process_id=self.process_id)
        return capture_args(output_file, self.seconds, process_name=self.process_name)

    def describe_command(self) -> Optional[str]:
        import subprocess
        return subprocess.list2cmdline([self.presentmon_path] + self._args("<run folder>\\" + CSV_NAME))

    def run(self, ctx: RunContext) -> WorkloadResult:
        problem = check_presentmon_path(self.presentmon_path)
        if problem:
            return WorkloadResult(status="skipped", error=problem)
        csv_path = Path(ctx.output_dir) / CSV_NAME
        args = self._args(str(csv_path))
        timeout = ctx.timeout_seconds or (self.seconds + START_MARGIN_SECONDS)
        out = self._run_elevated(self.presentmon_path, args, str(ctx.output_dir), timeout, ctx.stop_event)
        common = {"command": [self.presentmon_path] + args, "exit_code": out.exit_code,
                  "duration_seconds": round(out.duration_seconds, 3)}
        if out.status == "uac_declined":
            return WorkloadResult(status="cancelled", error="UAC prompt declined; nothing captured", **common)
        if out.status == "start_failed":
            return WorkloadResult(status="failed", error=out.error, **common)
        if out.status in ("stopped", "timed_out"):
            note = "" if out.terminated else f"; {out.error}"
            status = "cancelled" if out.status == "stopped" else "timed_out"
            reason = "stopped by user" if status == "cancelled" else f"timeout after {timeout:g} s"
            return WorkloadResult(status=status, error=reason + note, **common)
        if out.exit_code != 0:
            return WorkloadResult(status="failed", error=f"PresentMon exit code {out.exit_code}", **common)
        if not csv_path.is_file():
            return WorkloadResult(status="failed", error="PresentMon wrote no capture file", **common)
        parsed = parse_presentmon_csv(csv_path.read_text(encoding="utf-8", errors="replace"),
                                      process_name=None if self.process_id is not None else self.process_name,
                                      process_id=self.process_id)
        if parsed.error:
            return WorkloadResult(status="invalid", error=f"capture not usable: {parsed.error}", **common)
        stats = compute_frame_stats(parsed.frametimes_ms, self.min_frames)
        if stats.error:
            return WorkloadResult(status="invalid", error=f"capture not usable: {stats.error}", **common)
        metrics = stats_to_metrics(stats)
        metrics.update({"rows_skipped": (float(parsed.rows_skipped), "rows"),
                        "dropped_presents": (float(parsed.dropped), "frames")})
        return WorkloadResult(status="completed", score=stats.avg_fps, unit="fps", metrics=metrics, **common)

"""Cinebench 2026 (Maxon) as Workloads: Multiple Threads and Single Thread, run from its command line.

The program comes from Maxon's own download server, installed by the Install button into the app's tools folder
(data/installers.py, one zip per architecture: Windows x64 and ARM64; Maxon has no Linux build). Its command line
is the one on Maxon's page "Tech Info Cinebench" (maxon.net/tech-info-cinebench, read 2026-10-19):

    start /b /wait "parentconsole" Cinebench.exe g_CinebenchCpuXTest=true      Multiple Threads only
    start /b /wait "parentconsole" Cinebench.exe g_CinebenchCpu1Test=true      Single Thread only

Maxon: on Windows the `start /b /wait "parentconsole"` part is needed for a console log; Cinebench runs the test,
quits and prints the result in the console (no result file). A minimum test duration is active by default
(g_CinebenchMinimumTestDuration changes it; the app keeps Maxon's default so scores match other people's).
The score line is read as "CB <points>" (LTT Labs' MarkBench reads it so, 2026-10-18); not checked on a real
run here yet, so the tests are not marked verified. Scores of Cinebench 2026 can't be compared with 2024 or R23.
Nothing is uploaded.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

# test id: (title, command-line option)
MODES = {
    "cinebench_2026": ("Multiple Threads", "g_CinebenchCpuXTest=true"),
    "cinebench_2026_single": ("Single Thread", "g_CinebenchCpu1Test=true"),
}
UNIT = "pts"
SCORE = re.compile(r"\bCB\s+(\d+(?:\.\d+)?)")


def program_plan(arch: Optional[str] = None) -> Optional[dict]:
    """The zip plan for this machine's architecture (Windows only)."""
    from data.installers import INSTALLERS
    if sys.platform != "win32":
        return None
    if arch is None:
        from app.frames.provision import native_arch
        arch = native_arch()
    return ((INSTALLERS.get("cinebench_2026") or {}).get("windows") or {}).get("zip_by_arch", {}).get(arch)


def program_path(data_dir: Optional[Path] = None) -> Optional[Path]:
    from app.install import direct
    plan = program_plan()
    if not plan:
        return None
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def build_command(exe: str, option: str) -> str:
    """Maxon's documented Windows command, run by cmd.exe. A string, not a list: cmd needs the quoted
    "parentconsole" title exactly as written (a list would escape its quotes)."""
    return f'cmd.exe /d /c start /b /wait "parentconsole" "{exe}" {option}'


def parse_score(text: str) -> Tuple[Optional[float], Optional[str]]:
    """(points, None) from the last "CB <points>" line, or (None, why)."""
    found = SCORE.findall(text)
    if not found:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return None, "no 'CB <score>' line in Cinebench's output" + (f" (it ended with: {lines[-1][:200]})"
                                                                      if lines else " (it printed nothing)")
    score = float(found[-1])
    if score <= 0:
        return None, f"the score {found[-1]} is not a result"
    return score, None


class CinebenchWorkload(Workload):
    timeout_seconds = 3600          # Maxon's default minimum duration plus loading; a slow PC takes longer

    def __init__(self, test_id: str = "cinebench_2026", exe: Optional[Path] = None):
        self.id = test_id
        self.verified = False       # the score line has not been seen on a real run here yet
        self.title, self.option = MODES[test_id]
        self.exe = exe if exe is not None else program_path()
        self.display_name = f"Cinebench 2026 ({self.title})"
        self.benchmark_type = "cpu"

    def availability(self) -> Tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Cinebench 2026 runs on Windows (x64, ARM64) and macOS only; Maxon has no Linux build"
        if not self.exe:
            return False, "Cinebench 2026 is not installed (Install button)"
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "the app's tools folder"

    def version(self) -> Optional[str]:
        return "2026"

    def describe_command(self) -> Optional[str]:
        return build_command(str(self.exe), self.option) if self.exe else None

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=why)
        folder = Path(ctx.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        command = build_command(str(self.exe), self.option)
        out, err = folder / "stdout_cinebench.txt", folder / "stderr_cinebench.txt"
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(self.exe.parent))
        common = {"command": [command], "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3), "stdout_path": str(out),
                  "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s (a license "
                                  "window waiting for an answer? open Cinebench once and accept it)", **common)
        text = out.read_text(encoding="utf-8", errors="replace")
        score, why = parse_score(text)
        if score is None:
            return WorkloadResult(status="invalid", error=why + (f"; exit code {outcome.exit_code}"
                                                                 if outcome.exit_code else ""), **common)
        if outcome.exit_code != 0:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}", **common)
        note = f"Cinebench 2026; test: {self.title}; Maxon's default minimum test duration"
        return WorkloadResult(status="completed", score=round(score, 2), unit=UNIT, metrics={}, note=note, **common)


def cinebench_workloads() -> list:
    return [CinebenchWorkload(t) for t in MODES]

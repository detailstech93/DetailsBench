"""vkmark (Vulkan) and glmark2 (OpenGL) as Workloads: the usual open-source GPU benchmarks on Linux.

Both come from the distribution (Ubuntu 24.04: vkmark 2017.08+git20220909, glmark2-x11 2023.01), installed by the
Install button with the system's package manager (data/installers.py). Each runs its default scene list in its
default 800x600 window, the size their public result lists use, and ends with one line:

    glmark2 Score: 1234
    vkmark Score: 1234

That number is the score (points, higher is faster). They need a desktop with a working GPU driver: under WSL
the GPU goes through a translation layer (or software rendering), so only a real Linux install gives numbers
that mean anything for the card. Not checked on a real run here yet, so the tests are not marked verified.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult
from app.pro import software_gpu

UNIT = "points"
# test id: (display name, program, API, project page)
PROGRAMS = {
    "vkmark": ("vkmark", "vkmark", "Vulkan", "https://github.com/vkmark/vkmark"),
    "glmark2": ("glmark2", "glmark2", "OpenGL", "https://github.com/glmark2/glmark2"),
}


def parse_score(program: str, text: str) -> Tuple[Optional[int], Optional[str]]:
    """(score, None) from the last "<program> Score: N" line, or (None, why)."""
    found = re.findall(rf"\b{re.escape(program)} Score:\s*([0-9]+)", text)
    if not found:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return None, (f"no '{program} Score:' line in its output"
                      + (f" (it ended with: {lines[-1][:200]})" if lines else " (it printed nothing)"))
    score = int(found[-1])
    if score <= 0:
        return None, f"a score of {score} is not a result"
    return score, None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


class LinuxGpuWorkload(Workload):
    timeout_seconds = 900
    needs_desktop = True            # opens a window: it needs the unlocked desktop

    def __init__(self, test_id: str, platform: Optional[str] = None, which=shutil.which):
        self.id = test_id
        self.verified = False       # the output has not been seen on a real run here yet
        self.display_name, self.program, self.api, self.url = PROGRAMS[test_id]
        self.platform = platform or sys.platform
        self.which = which
        self.benchmark_type = "gpu"

    def system_blocks(self) -> Optional[str]:
        if not self.platform.startswith("linux"):
            return f"{self.program} runs on Linux only"
        return None

    def _path(self) -> Optional[str]:
        return self.which(self.program) if not self.system_blocks() else None

    def availability(self) -> Tuple[bool, str]:
        blocked = self.system_blocks()
        if blocked:
            return False, blocked
        path = self._path()
        return (True, path) if path else (False, f"{self.program} is not installed (Install button)")

    def executable(self) -> Tuple[Optional[str], str]:
        return self._path(), "PATH"

    def version(self) -> Optional[str]:
        return None

    def describe_command(self) -> Optional[str]:
        path = self._path()
        return subprocess.list2cmdline([path]) if path else None

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"{self.program}: {why}")
        folder = Path(ctx.output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        command = [why]
        out, err = folder / f"stdout_{self.program}.txt", folder / f"stderr_{self.program}.txt"
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event)
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3), "stdout_path": str(out),
                  "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        if outcome.exit_code != 0:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code} (no {self.api} driver or "
                                  "no desktop?)", **common)
        text = _read(out) + "\n" + _read(err)
        renderer = software_gpu.software_renderer(text)
        if renderer:
            return WorkloadResult(status="invalid", error=software_gpu.why(renderer, self.api), **common)
        score, why_not = parse_score(self.program, text)
        if score is None:
            return WorkloadResult(status="invalid", error=why_not, **common)
        note = f"{self.program}, {self.api}, default scenes, 800x600 window"
        return WorkloadResult(status="completed", score=score, unit=UNIT, metrics={}, note=note, **common)


def linux_gpu_workloads() -> list:
    return [LinuxGpuWorkload(t) for t in PROGRAMS]

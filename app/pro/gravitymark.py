"""GravityMark (Tellusim) as Workloads: its GPU benchmark, full screen, with Vulkan or OpenGL (Windows and Linux,
so the same card can be compared on both) and Direct3D 12 (Windows).

The program comes from tellusim.com (linked from gravitymark.tellusim.com), version 1.89, installed by the Install
button into the app's tools folder (data/installers.py): Windows' .msi as an administrative image, Linux' makeself
.run unpacked without running it. x64 only here.

Command line, from the run_fullscreen_*.sh / .bat files and GravityMark_Manual.pdf inside the download, and the
Phoronix Test Suite's gravitymark-1.10.0 profile (which runs the same):

    GravityMark -vsync 0 -fps 1 -benchmark 1 -close 1 -fullscreen 1 -status 1 -times <run folder>/times.txt -vulkan

-benchmark 1 runs the benchmark scene, -close 1 closes the program when it is over, -status 1 makes a quit by the
user a non-zero exit code, -times writes every frame's time (columns: index, time, delta time, pass; manual,
"Report formats"). Phoronix reads the average from the "FPS:" line of the console output; that is the score here,
in frames per second, higher is faster. Without such a line the average is taken from the frame times file. Not
checked on a real run here yet (the output was not seen), so the tests are not marked verified. Nothing is
uploaded: the leaderboard needs the GUI's own button.

License (GravityMark_EULA.pdf in the download): an evaluation license, for evaluation and testing only and NOT
for any commercial purpose (pre-sale testing and demonstrations included); publishing tests of commercial
products needs Tellusim's paid Commercial License. The app asks the owner once before the first run and keeps the
answer in settings.json.
"""

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult
from app.pro import software_gpu

VERSION = "1.89"
LICENSE_URL = "https://gravitymark.tellusim.com/"
LICENSE_KEY = "gravitymark_license_accepted"
# test id: (title, API option, operating systems)
MODES = {
    "gravitymark_vulkan": ("Vulkan", "-vulkan", ("windows", "linux")),
    "gravitymark_opengl": ("OpenGL", "-opengl", ("windows", "linux")),
    "gravitymark_d3d12": ("Direct3D 12", "-direct3d12", ("windows",)),
}
UNIT = "FPS"
DLL_MISSING = (0xC0000135, -1073741515, 0xC000007B, -1073741701)     # a DLL not found / the wrong one
FPS = re.compile(r"\bFPS:\s*([0-9]+(?:\.[0-9]+)?)", re.I)
SCORE = re.compile(r"\bScore:\s*([0-9]+(?:\.[0-9]+)?)", re.I)


def _os_key(platform: Optional[str] = None) -> str:
    platform = platform or sys.platform
    return "windows" if platform == "win32" else "linux" if platform.startswith("linux") else platform


def program_plan(platform: Optional[str] = None) -> Optional[dict]:
    from data.installers import INSTALLERS
    return ((INSTALLERS.get("gravitymark_vulkan") or {}).get(_os_key(platform)) or {}).get("zip")


def program_path(data_dir: Optional[Path] = None) -> Optional[Path]:
    from app.install import direct
    plan = program_plan()
    if not plan:
        return None
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def license_accepted(data_dir: Optional[Path] = None) -> bool:
    from app.paths import get_data_dir, load_settings
    value = load_settings(Path(data_dir or get_data_dir()))[0].get(LICENSE_KEY)
    return isinstance(value, dict) and value.get("url") == LICENSE_URL


def accept_license(data_dir: Optional[Path] = None) -> None:
    from app.paths import get_data_dir, save_setting
    save_setting(Path(data_dir or get_data_dir()), LICENSE_KEY, {"url": LICENSE_URL, "date": date.today().isoformat()})


def license_question() -> str:
    return ("GravityMark is made by Tellusim Technologies. Its free license is an EVALUATION license: for evaluation "
            "and testing only, NOT for any commercial purpose (pre-sale testing and demonstrations included). "
            "Testing commercial products and publishing the results needs Tellusim's paid Commercial License "
            "(sales@tellusim.com). The license text is GravityMark_EULA.pdf in the installed folder; the site is:\n\n"
            f"{LICENSE_URL}\n\nRead it. Is your use allowed by it, and do you accept it? Nothing is uploaded.")


def build_command(exe: str, api: str, folder: str) -> list:
    return [exe, "-vsync", "0", "-fps", "1", "-benchmark", "1", "-close", "1", "-fullscreen", "1", "-status", "1",
            "-times", str(Path(folder) / "times.txt"), api]


def fps_from_times(text: str) -> Optional[float]:
    """Average frames per second from the frame times file (index, time, delta time, pass), or None."""
    times = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                times.append(float(parts[1]))
            except ValueError:
                continue
    if len(times) < 2 or times[-1] <= times[0]:
        return None
    return (len(times) - 1) / (times[-1] - times[0])


def parse_result(text: str, times_text: str = "") -> Tuple[Optional[float], Dict[str, str], Optional[str]]:
    """(average FPS, details, None) from the last "FPS:" line, else from the frame times, or (None, {}, why)."""
    details = {}
    score = SCORE.findall(text)
    if score:
        details["GravityMark score"] = score[-1]
    found = FPS.findall(text)
    if found:
        fps = float(found[-1])
        details["Read from"] = "the FPS line of GravityMark's output"
    else:
        fps = fps_from_times(times_text)
        if fps is None:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            return None, {}, ("no 'FPS:' line in GravityMark's output and no frame times"
                              + (f" (it ended with: {lines[-1][:200]})" if lines else " (it printed nothing)"))
        details["Read from"] = "the frame times file (no FPS line in the output)"
    if fps <= 0:
        return None, {}, f"{fps} FPS is not a result"
    return fps, details, None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


class GravityMarkWorkload(Workload):
    timeout_seconds = 900           # Phoronix' log shows about 3 minutes for one pass
    needs_desktop = True            # full screen: it needs the unlocked screen

    def __init__(self, test_id: str = "gravitymark_vulkan", exe: Optional[Path] = None,
                 data_dir: Optional[Path] = None, platform: Optional[str] = None):
        self.id = test_id
        self.verified = False       # the output has not been seen on a real run here yet
        self.title, self.api, self.systems = MODES[test_id]
        self.platform = platform or sys.platform
        self.exe = exe if exe is not None else program_path()
        self.data_dir = data_dir
        self.display_name = f"GravityMark ({self.title})"
        self.benchmark_type = "gpu"
        self.license = (LICENSE_KEY, "GravityMark", license_question)   # asked once by app/ui/run_tests.py

    def license_ok(self) -> bool:
        return license_accepted(self.data_dir)

    def accept_license(self) -> None:
        accept_license(self.data_dir)

    def system_blocks(self) -> Optional[str]:
        if _os_key(self.platform) not in self.systems:
            return f"GravityMark's {self.title} runs on {' and '.join(s.title() for s in self.systems)} only"
        return None

    def availability(self) -> Tuple[bool, str]:
        blocked = self.system_blocks()
        if blocked:
            return False, blocked
        if not self.exe:
            return False, "GravityMark is not installed (Install button)"
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "the app's tools folder"

    def version(self) -> Optional[str]:
        return VERSION

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        return subprocess.list2cmdline(build_command(str(self.exe), self.api, "<run folder>"))

    def _env(self) -> Optional[dict]:
        if _os_key(self.platform) != "linux":
            return None
        env = dict(os.environ)          # its own libTellusim_x64.so sits next to it (the vendor's scripts do this)
        env["LD_LIBRARY_PATH"] = os.pathsep.join(p for p in (str(self.exe.parent), env.get("LD_LIBRARY_PATH")) if p)
        return env

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"GravityMark: {why}")
        if not self.license_ok():
            return WorkloadResult(status="skipped", error="GravityMark: its license was not accepted in the app, "
                                                          "so it was not run")
        folder = Path(ctx.output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        command = build_command(str(self.exe), self.api, str(folder))
        out, err = folder / "stdout_gravitymark.txt", folder / "stderr_gravitymark.txt"
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(self.exe.parent),
                              env=self._env())
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3), "stdout_path": str(out),
                  "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        renderer = software_gpu.software_renderer(_read(out) + "\n" + _read(err))
        if renderer:                    # the WSL run crashed on llvmpipe (exit -11); a finished one is no GPU score
            return WorkloadResult(status="invalid", error=software_gpu.why(renderer, self.title), **common)
        if outcome.exit_code in DLL_MISSING:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}: a DLL is missing (Windows: "
                                  "GravityMark needs Microsoft's Visual C++ Redistributable x64; its own "
                                  "vc_redist.x64.exe is in its bin folder, or get it from Microsoft's site)", **common)
        if outcome.exit_code != 0:      # -status 1: closed by hand, or the API could not start
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code} (closed before the end, or "
                                  f"{self.title} is not available on this GPU)", **common)
        fps, details, why = parse_result(_read(out) + "\n" + _read(err), _read(folder / "times.txt"))
        if fps is None:
            return WorkloadResult(status="invalid", error=why, **common)
        parts = [("GravityMark", VERSION), ("API", self.title), ("Mode", "full screen, vsync off")] + list(details.items())
        note = "; ".join(f"{k}: {v}" for k, v in parts if v)
        return WorkloadResult(status="completed", score=round(fps, 2), unit=UNIT, metrics={}, note=note, **common)


def gravitymark_workloads() -> list:
    return [GravityMarkWorkload(t) for t in MODES]

"""y-cruncher (Alexander J. Yee) as Workloads: its Pi benchmark, 1 billion and 2.5 billion digits, on every thread.

The program comes from the author's own download server (cdn.numberworld.org, linked from numberworld.org/
y-cruncher), installed by the Install button into the app's tools folder (data/installers.py): the Windows zip and
the Linux "static" tar.xz (works on most distributions; the dynamic build needs TBB and NUMA libraries). x64 only:
the author ships no ARM build. Its sizes and SHA-256 equal the ones GitHub lists for the same files in the
author's release (github.com/Mysticial/y-cruncher, v0.8.7.9547b / v0.8.7.9547), checked on 2026-10-19.
y-cruncher.exe / y-cruncher is a small launcher that picks the binary made for this CPU (Binaries/).

Command line, from the "Command Lines.txt" manual inside the download:

    y-cruncher skip-warnings pause:-2 colors:0 status:none logfile:"<run folder>/ycruncher_log.txt"
               bench 1b -o "<run folder>" -od:0

skip-warnings: no ENTER needed at the start; pause:-2: never waits for a key, not even on an error; status:none:
no live status lines in the captured output; logfile: the console output also goes to a file; -od:0: no digits
written (1 billion digits would be a 1 GB file), only the validation file. Phoronix Test Suite runs the same
"skip-warnings bench <size>" and reads "Total Computation Time: <s> seconds" (its y-cruncher-1.5.0 profile): that
time is the score, in seconds, lower is faster. Read from the log file, the console output or the validation file,
whichever has it. Not checked on a real run here yet, so the tests are not marked verified.

License (Read Me.txt and numberworld.org/y-cruncher/license.html): free for non-commercial use and for academic
use including technology reviews, even commercial ones; other commercial use needs the author's permission. The
app asks the owner once (app/ui/run_tests.py) and keeps the answer in settings.json. Nothing is uploaded.
Windows: the build is not statically linked any more, so a PC without Microsoft's Visual C++ runtime cannot start
it (the run says so).
"""

import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

VERSION = "0.8.7.9547"
LICENSE_URL = "https://www.numberworld.org/y-cruncher/license.html"
LICENSE_KEY = "ycruncher_license_accepted"
# test id: (title, size argument)
MODES = {
    "ycruncher_pi_1b": ("Pi, 1 billion digits", "1b"),
    "ycruncher_pi_2_5b": ("Pi, 2.5 billion digits", "2.5b"),
}
UNIT = "s"
TOTAL = re.compile(r"Total Computation Time:\s*([0-9]+(?:\.[0-9]+)?)\s*seconds", re.I)
DETAILS = (("Start-to-End Wall Time", re.compile(r"Start-to-End Wall Time:\s*([0-9.]+\s*seconds)", re.I)),
           ("Binary", re.compile(r"^\s*Binary:\s*(.+?)\s*$", re.I | re.M)),
           ("Tuning profile", re.compile(r"^\s*Tuning Profile:\s*(.+?)\s*$", re.I | re.M)),
           ("Logical cores", re.compile(r"^\s*Logical Cores:\s*(\d+)\s*$", re.I | re.M)))
DLL_MISSING = (0xC0000135, -1073741515, 0xC000007B, -1073741701)     # a DLL not found / the wrong one


def _os_key() -> str:
    return "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def program_plan() -> Optional[dict]:
    from data.installers import INSTALLERS
    return ((INSTALLERS.get("ycruncher_pi_1b") or {}).get(_os_key()) or {}).get("zip")


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
    return ("y-cruncher is made by Alexander J. Yee. Its license lets you use it free of charge for any "
            "non-commercial purpose, and for academic purposes including technology reviews, even commercial ones; "
            "any other commercial use needs the author's permission (a-yee@u.northwestern.edu):\n\n"
            f"{LICENSE_URL}\n\nOpen the link and read it. Is your use one of the free ones, and do you accept it? "
            "Nothing is uploaded.")


def build_command(exe: str, size: str, folder: str) -> list:
    return [exe, "skip-warnings", "pause:-2", "colors:0", "status:none",
            "logfile:" + str(Path(folder) / "ycruncher_log.txt"), "bench", size, "-o", folder, "-od:0"]


def parse_result(text: str) -> Tuple[Optional[float], Dict[str, str], Optional[str]]:
    """(seconds, details, None) from the last "Total Computation Time" line, or (None, {}, why)."""
    found = TOTAL.findall(text)
    if not found:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return None, {}, ("no 'Total Computation Time' line in y-cruncher's output"
                          + (f" (it ended with: {lines[-1][:200]})" if lines else " (it printed nothing)"))
    seconds = float(found[-1])
    if seconds <= 0:
        return None, {}, f"the time {found[-1]} s is not a result"
    details = {}
    for name, pattern in DETAILS:
        hits = pattern.findall(text)
        if hits:
            details[name] = hits[-1].strip()
    return seconds, details, None


def _texts(folder: Path, out: Path):
    """Where the result may be: the log file, the console output, then the validation file(s)."""
    paths = [folder / "ycruncher_log.txt", out] + sorted(p for p in folder.glob("*.txt")
                                                         if p.name.lower().startswith(("pi", "validation")))
    for path in paths:
        try:
            yield path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue


class YCruncherWorkload(Workload):
    timeout_seconds = 3600          # 2.5 billion digits on a slow PC with little memory takes long

    def __init__(self, test_id: str = "ycruncher_pi_1b", exe: Optional[Path] = None, data_dir: Optional[Path] = None):
        self.id = test_id
        self.verified = False       # the output has not been seen on a real run here yet
        self.title, self.size = MODES[test_id]
        self.exe = exe if exe is not None else program_path()
        self.data_dir = data_dir
        self.display_name = f"y-cruncher ({self.title})"
        self.benchmark_type = "cpu"
        self.license = (LICENSE_KEY, "y-cruncher", license_question)   # asked once by app/ui/run_tests.py

    def license_ok(self) -> bool:
        return license_accepted(self.data_dir)

    def accept_license(self) -> None:
        accept_license(self.data_dir)

    def availability(self) -> Tuple[bool, str]:
        if _os_key() not in ("windows", "linux"):
            return False, "y-cruncher's builds are for Windows and Linux only"
        if not self.exe:
            return False, "y-cruncher is not installed (Install button)"
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "the app's tools folder"

    def version(self) -> Optional[str]:
        return VERSION

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        return subprocess.list2cmdline(build_command(str(self.exe), self.size, "<run folder>"))

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"y-cruncher: {why}")
        if not self.license_ok():
            return WorkloadResult(status="skipped", error="y-cruncher: its license was not accepted in the app, "
                                                          "so it was not run")
        folder = Path(ctx.output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        command = build_command(str(self.exe), self.size, str(folder))
        out, err = folder / "stdout_ycruncher.txt", folder / "stderr_ycruncher.txt"
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(self.exe.parent))
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3), "stdout_path": str(out),
                  "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        if outcome.exit_code in DLL_MISSING:
            return WorkloadResult(status="failed", error="y-cruncher could not start: a library is missing (this "
                                  "build needs Microsoft's Visual C++ Redistributable, x64, from Microsoft's site)",
                                  **common)
        result = (None, {}, "y-cruncher wrote no output")
        for text in _texts(folder, out):
            result = parse_result(text)
            if result[0] is not None:
                break
        seconds, details, why = result
        if seconds is None:
            return WorkloadResult(status="invalid", error=why + (f"; exit code {outcome.exit_code}"
                                                                 if outcome.exit_code else ""), **common)
        if outcome.exit_code != 0:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}", **common)
        parts = [("y-cruncher", VERSION), ("Test", self.title + ", all threads")] + list(details.items())
        note = "; ".join(f"{k}: {v}" for k, v in parts if v)
        return WorkloadResult(status="completed", score=round(seconds, 3), unit=UNIT, metrics={}, note=note, **common)


def ycruncher_workloads() -> list:
    return [YCruncherWorkload(t) for t in MODES]

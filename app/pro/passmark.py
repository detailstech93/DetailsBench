"""PassMark PerformanceTest (Windows) as Workloads: CPU Mark, 2D Graphics Mark, 3D Graphics Mark, Memory Mark.

PerformanceTest is installed by the Install button (winget, PassMark.PerformanceTest; data/installers.py) into
Program Files\\PerformanceTest. The app writes a small PassMark script into the run folder and starts

    PerformanceTest64.exe /s <script>

The script commands are PassMark's: the sample script it ships (SampleScript.ptscript: RUN CPU_ALL, CLEARRESULTS,
EXPORTHTML ...), the command names inside PerformanceTest64.exe 11.1 on the development PC (RUN CPU_ALL /
G2D_ALL / G3D_ALL / ME_ALL, EXPORTCSV, EXPORTTEXT, SUPPRESSWARNINGS) and PassMark staff on their forum (/s runs a
script; EXPORTCSV and EXIT save the results and close the program; the /a switches cannot be used with /s).
Read 2026-10-19. What the exported files look like has not been seen on a real run, so the tests are not
marked verified and a file without the mark is "invalid" (never a 0).

PerformanceTest asks for administrator rights (its manifest: requireAdministrator), so each run goes through the
Windows UAC prompt (app/frames/elevated.py); a declined prompt is a skipped run. PassMark's license page lists
"disable automatic uploading of results" as a paid feature: the free version may send results to PassMark. The
owner accepted that on 2026-10-19 on the condition that every run says so first (WARNING below).
Disk Mark is left out: it writes test files to a drive (DiskSpd and fio cover the drives). No Linux build is
used yet (PassMark's Linux version is a different command-line program with CPU and memory tests only).
"""

import csv
import io
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.automation.workload import RunContext, Workload, WorkloadResult

# test id: (mark name, script test group)
MODES = {
    "passmark_cpu": ("CPU Mark", "CPU_ALL"),
    "passmark_2d": ("2D Graphics Mark", "G2D_ALL"),
    "passmark_3d": ("3D Graphics Mark", "G3D_ALL"),
    "passmark_memory": ("Memory Mark", "ME_ALL"),
}
UNIT = "points"
EXE_NAME = "PerformanceTest64.exe"          # the x64 program (seen in PerformanceTest 11.1, 2026-10-19)
SW_SHOWNORMAL = 1                           # its 2D and 3D tests draw in its windows: shown normally
WARNING = ("PassMark PerformanceTest's free version may send the results to PassMark by itself (turning that "
           "off needs a PassMark license). Windows asks for administrator permission at the start of each run; "
           "PassMark's window and its test windows open meanwhile: do not use the computer.")


def program_path() -> Optional[Path]:
    if sys.platform != "win32":
        return None
    for root in {os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")} - {None}:
        exe = Path(root) / "PerformanceTest" / EXE_NAME
        if exe.is_file():
            return exe
    return None


def script_text(group: str, csv_path: Path, text_path: Path) -> str:
    return "\r\n".join([
        "# written by PC Benchmark Manager for one run",
        "SUPPRESSWARNINGS",
        "CLEARRESULTS",
        f"RUN {group}",
        f'EXPORTCSV "{csv_path}"',
        f'EXPORTTEXT "{text_path}"',
        "EXIT",
        ""])


def _number(text: str) -> Optional[float]:
    text = text.strip().replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def parse_mark(mark: str, csv_text: str, plain_text: str = "") -> Tuple[Optional[float], Optional[str]]:
    """(the mark, None), or (None, why). The CSV may list "<mark>,<value>" on one row or put the mark in a header
    with its value below; the text export may say "<mark>: <value>"."""
    want = _key(mark)
    rows = [r for r in csv.reader(io.StringIO(csv_text)) if any(c.strip() for c in r)]
    for k, row in enumerate(rows):
        keys = [_key(c) for c in row]
        if want not in keys:
            continue
        col = keys.index(want)
        for cell in row[col + 1:]:              # the value next to the name
            value = _number(cell)
            if value is not None:
                return value, None
        for below in rows[k + 1:]:              # a header: the value in the same column below
            if col < len(below) and _number(below[col]) is not None:
                return _number(below[col]), None
    m = re.search(re.escape(mark) + r"\s*[:=\t ]\s*([\d,]+(?:\.\d+)?)", plain_text, re.IGNORECASE)
    if m and _number(m.group(1)) is not None:
        return _number(m.group(1)), None
    if not csv_text.strip() and not plain_text.strip():
        return None, "PerformanceTest wrote no results file"
    return None, f"no {mark!r} in PerformanceTest's results"


class PassMarkWorkload(Workload):
    timeout_seconds = 1800          # a full test group with its windows; a slow PC takes longer
    warning = WARNING

    def __init__(self, test_id: str = "passmark_cpu", exe: Optional[Path] = None, elevated=None):
        self.id = test_id
        self.verified = False       # the exported files have not been seen on a real run here yet
        self.mark, self.group = MODES[test_id]
        self.exe = exe if exe is not None else program_path()
        self.display_name = f"PassMark PerformanceTest ({self.mark})"
        self.benchmark_type = "gpu" if test_id in ("passmark_2d", "passmark_3d") else "cpu"   # memory: with the CPU
        self._elevated = elevated

    def availability(self) -> Tuple[bool, str]:
        if sys.platform != "win32":
            return False, "PassMark PerformanceTest for Windows; its Linux version is not supported by the app yet"
        if not self.exe:
            return False, "PassMark PerformanceTest is not installed (Install button)"
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "Program Files\\PerformanceTest"

    def version(self) -> Optional[str]:
        if not self.exe:
            return None
        from app.benchmark_manager import get_pe_version
        return get_pe_version(str(self.exe))

    def describe_command(self) -> Optional[str]:
        return f'"{self.exe}" /s <run folder>\\passmark_script.ptscript (RUN {self.group})' if self.exe else None

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=why)
        folder = Path(ctx.output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        csv_path, text_path = folder / "passmark_results.csv", folder / "passmark_results.txt"
        script = folder / "passmark_script.ptscript"
        script.write_text(script_text(self.group, csv_path, text_path), encoding="ascii")
        run = self._elevated
        if run is None:
            from app.frames.elevated import run_elevated as run
        args = ["/s", str(script)]
        outcome = run(str(self.exe), args, str(self.exe.parent), ctx.timeout_seconds, ctx.stop_event,
                      show=SW_SHOWNORMAL)
        common = {"command": [str(self.exe)] + args, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds or 0.0, 3), "stdout_path": str(csv_path),
                  "stderr_path": str(text_path)}
        if outcome.status == "uac_declined":
            return WorkloadResult(status="skipped", error="the administrator prompt was declined; nothing ran",
                                  **common)
        if outcome.status == "start_failed":
            return WorkloadResult(status="failed", error=outcome.error or "could not start", **common)
        if outcome.status == "stopped":
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.status == "timed_out":
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s (a PassMark "
                                  "message waiting for an answer?)", **common)

        def read(p: Path) -> str:
            try:
                return p.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return ""
        score, why = parse_mark(self.mark, read(csv_path), read(text_path))
        if score is None:
            return WorkloadResult(status="invalid", error=why + (f"; exit code {outcome.exit_code}"
                                                                 if outcome.exit_code else ""), **common)
        note = f"PassMark PerformanceTest; test: {self.mark} ({self.group}); PassMark's default test length"
        return WorkloadResult(status="completed", score=round(score, 1), unit=UNIT, metrics={}, note=note,
                              **common)


def passmark_workloads() -> list:
    return [PassMarkWorkload(t) for t in MODES]

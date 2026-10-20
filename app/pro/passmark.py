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

PerformanceTest asks for administrator rights (its manifest: requireAdministrator), so it runs through the app's
administrator helper (app/frames/admin_helper.py): one UAC prompt for a whole run of tests, or one for this test
alone. The helper writes the script and PassMark writes its results in a folder only administrators can open;
the results come back as text. A declined prompt is a skipped run. PassMark's license page lists
"disable automatic uploading of results" as a paid feature: the free version may send results to PassMark. The
owner accepted that on 2026-10-19 on the condition that every run says so first (WARNING below).
Disk Mark is left out: it writes test files to a drive (DiskSpd and fio cover the drives).

Linux: PassMark's own command-line program (CPU Mark and Memory Mark only; no 2D/3D), unpacked by the Install
button from PassMark's zip into the app's tools folder (data/installers.py), started in the run folder as

    PerformanceTest_Linux_<arch> -r 1      (CPU Suite only; -r 2: Memory Suite only)

PassMark's Linux FAQ (passmark.com/support/pt_linux_faq.php, read 2026-10-19): -r runs the tests by itself and
writes results_cpu.yml / results_memory.yml; "Autorun will only export results to a file, it will not upload the
results." The results go to the working directory (PassMark staff on their forum). The program's own help text
lists the same options; the result keys SUMM_CPU and SUMM_ME and "Results Complete" are names inside the
program (11.0 Build 1004), not yet seen in a real results file, so these runs are not marked verified either.
It needs the ncurses 5 library (the FAQ: libncurses5 on Ubuntu, ncurses-compat-libs on Fedora and Red Hat); the
Install button installs it after the zip when it is missing.
"""

import csv
import io
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from app.automation.process import run_process
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
           "off needs a PassMark license). Windows asks once for administrator permission when the run starts; "
           "PassMark's window and its test windows open meanwhile: do not use the computer.")

# Linux: test id: (the -r value, the key in its results file, the results file)
LINUX_MODES = {
    "passmark_cpu": ("1", "SUMM_CPU", "results_cpu.yml"),
    "passmark_memory": ("2", "SUMM_ME", "results_memory.yml"),
}
LINUX_WARNING = ("PassMark's Linux program in its autorun mode only writes the results to a file and does not "
                 "send them to PassMark (PassMark's FAQ). Do not use the computer while it runs.")
# the ncurses 5 package by distribution family, as PassMark's Linux FAQ names it (Ubuntu: libncurses5; Fedora and
# Red Hat: ncurses-compat-libs). Ubuntu 24.04 and newer have no libncurses5 package: the FAQ installs PassMark's
# listed .deb files by hand there, which the app does not do.
NCURSES5_PACKAGES = {"debian": "libncurses5", "fedora": "ncurses-compat-libs"}
LIB_DIRS = ("/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/local/lib")
FAQ_URL = "https://passmark.com/support/pt_linux_faq.php"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def ncurses5_present(dirs=LIB_DIRS) -> bool:
    """libncurses.so.5 in a library folder (or one level down: /usr/lib/x86_64-linux-gnu and the like)."""
    for d in dirs:
        base = Path(d)
        for folder in [base] + ([p for p in base.iterdir() if p.is_dir()] if base.is_dir() else []):
            try:
                if any(folder.glob("libncurses.so.5*")):
                    return True
            except OSError:
                continue
    return False


def linux_plan() -> Optional[dict]:
    from app.install.plans import plan_for
    plan = plan_for("passmark_cpu")
    return plan if plan and plan.get("kind") == "zip" else None


def linux_program_path(plan: Optional[dict] = None, data_dir: Optional[Path] = None) -> Optional[Path]:
    from app.install import direct
    plan = plan if plan is not None else linux_plan()
    if not plan:
        return None
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def _family() -> Optional[str]:
    from app.install.plans import this_host
    return this_host().family


def ncurses_install_text(present=ncurses5_present, family=_family) -> str:
    """What the Install button does after unpacking PassMark's Linux zip (shown before the owner says Yes)."""
    if present():
        return "The ncurses 5 library it needs is already installed."
    package = NCURSES5_PACKAGES.get(family() or "")
    if package:
        return (f"It needs the ncurses 5 library: after the zip the app installs {package} (the package PassMark's "
                "FAQ names) with your distribution's package manager, and the system asks for your password "
                f"(pkexec). Ubuntu 24.04 and newer have no such package; PassMark's FAQ ({FAQ_URL}) shows the "
                "files to install there by hand.")
    return (f"It needs the ncurses 5 library, which the app cannot install on this distribution: see PassMark's "
            f"FAQ ({FAQ_URL}).")


def install_ncurses5(progress=lambda text: None, present=ncurses5_present, family=_family, commands=None,
                     run=None):
    """After the zip: install the ncurses 5 package through pkexec when it is missing (winget.Outcome)."""
    from app.install import winget
    if present():
        return winget.Outcome(True, "PassMark PerformanceTest for Linux installed; ncurses 5 is already there.")
    package = NCURSES5_PACKAGES.get(family() or "")
    if not package:
        return winget.Outcome(False, "PassMark PerformanceTest for Linux was unpacked, but it needs the ncurses 5 "
                                     f"library, which the app cannot install here: see {FAQ_URL}")
    if commands is None:
        from app.install.plans import linux_commands as commands
    if run is None:
        from app.ui.frame_capture_panel import run_commands as run
    argv, problem = commands(family(), package)
    if problem:
        return winget.Outcome(False, f"PassMark PerformanceTest for Linux was unpacked; {package} was not "
                                     f"installed: {problem}")
    progress(f"Installing {package} (enter your password in the system prompt)...")
    text = run(argv)
    if text != "done":
        return winget.Outcome(False, f"PassMark PerformanceTest for Linux was unpacked; {package} was not "
                                     f"installed: {text}. See {FAQ_URL}")
    return winget.Outcome(True, f"PassMark PerformanceTest for Linux and {package} installed.")


def parse_linux_results(key: str, text: str) -> Tuple[Optional[float], Optional[str]]:
    """(the mark, None) or (None, why) from results_cpu.yml / results_memory.yml ("SUMM_CPU: 12345.6")."""
    if not text.strip():
        return None, "PerformanceTest wrote no results file"
    done = re.search(r"^\s*Results Complete:\s*(\S+)", text, re.MULTILINE)
    if done and done.group(1).strip("'\"").lower() in ("false", "no", "0"):
        return None, "PerformanceTest says its results are not complete"
    m = re.search(r"^\s*" + re.escape(key) + r":\s*['\"]?([\d.]+)", text, re.MULTILINE)
    value = _number(m.group(1)) if m else None
    if value is None:
        return None, f"no {key} in PerformanceTest's results"
    return value, None


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
        "# written by DetailsBench for one run",
        "SUPPRESSWARNINGS",
        "CLEARRESULTS",
        f"RUN {group}",
        f'EXPORTCSV "{csv_path}"',
        f'EXPORTTEXT "{text_path}"',
        "EXIT",
        ""])


def _number(text: str) -> Optional[float]:
    text = text.strip().replace(",", "")
    text = re.sub(r"\s*\(\d+(?:\.\d+)?%\)$", "", text)      # "61060.5 (98%)": the percentile PassMark adds
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _name_key(text: str) -> str:
    """A column's name without what PassMark adds in brackets: "CPU Mark (Composite average)" -> "cpumark"."""
    return _key(re.sub(r"\s*\([^)]*\)\s*$", "", text))


def parse_mark(mark: str, csv_text: str, plain_text: str = "") -> Tuple[Optional[float], Optional[str]]:
    """(the mark, None), or (None, why). The CSV may list "<mark>,<value>" on one row or put the mark in a header
    with its value below; the text export may say "<mark>: <value>".

    PerformanceTest 11 (seen 2026-10-19) writes a header "Computer, ..., CPU Mark (Composite average), ...", then
    seven "Baseline #n" rows of other computers, then "This Computer" with "61060.5 (98%)". Only the row of this
    computer is ever read: a baseline is another PC's score."""
    want = _key(mark)
    rows = [r for r in csv.reader(io.StringIO(csv_text)) if any(c.strip() for c in r)]
    for k, row in enumerate(rows):
        keys = [_name_key(c) for c in row]
        if want not in keys:
            continue
        col = keys.index(want)
        for cell in row[col + 1:]:              # the value next to the name
            value = _number(cell)
            if value is not None:
                return value, None
        below = [r for r in rows[k + 1:] if r and not _key(r[0]).startswith("baseline")]
        own = [r for r in below if _key(r[0]) == "thiscomputer"]
        for r in own or below:                  # a header: the value in the same column below
            if col < len(r) and _number(r[col]) is not None:
                return _number(r[col]), None
    m = re.search(re.escape(mark) + r"\s*[:=\t ]\s*([\d,]+(?:\.\d+)?)", plain_text, re.IGNORECASE)
    if m and _number(m.group(1)) is not None:
        return _number(m.group(1)), None
    if not csv_text.strip() and not plain_text.strip():
        return None, "PerformanceTest wrote no results file"
    return None, f"no {mark!r} in PerformanceTest's results"


class PassMarkWorkload(Workload):
    timeout_seconds = 600           # a group took 80 - 115 s here (2026-10-19/20); a slow PC takes longer
    warning = WARNING

    needs_admin = True              # one UAC prompt for the whole run: app/frames/admin_helper.py

    def __init__(self, test_id: str = "passmark_cpu", exe: Optional[Path] = None, helper=None, ncurses=None):
        self.id = test_id
        self.verified = False       # the exported files have not been seen on a real run here yet
        self.mark, self.group = MODES[test_id]
        self._linux = _is_linux()
        self.exe = exe if exe is not None else (linux_program_path() if self._linux else program_path())
        self.display_name = f"PassMark PerformanceTest ({self.mark})"
        self.benchmark_type = "gpu" if test_id in ("passmark_2d", "passmark_3d") else "cpu"   # memory: with the CPU
        # 3D: five DirectX scenes and GPU compute, longer than the others; it needs the unlocked screen to draw (the
        # overnight run of 2026-10-20 had a locked screen: GPU 0% until the 1800 s timeout, three times)
        self.timeout_seconds = 900 if test_id == "passmark_3d" else type(self).timeout_seconds
        self.needs_desktop = test_id == "passmark_3d"
        self._helper = helper
        self._ncurses = ncurses or ncurses5_present
        if self._linux:             # no administrator rights and no upload on Linux
            self.needs_admin = False
            self.warning = LINUX_WARNING

    def availability(self) -> Tuple[bool, str]:
        if self._linux:
            if self.id not in LINUX_MODES:
                return False, "PassMark's Linux program has the CPU and Memory tests only"
            if not self.exe:
                return False, "PassMark PerformanceTest for Linux is not installed (Install button)"
            if not self._ncurses():
                return False, ("PassMark's Linux program needs the ncurses 5 library (libncurses.so.5): the Install "
                               "button installs it on Debian/Ubuntu and Fedora/Red Hat; otherwise see " + FAQ_URL)
            return True, str(self.exe)
        if sys.platform != "win32":
            return False, "PassMark PerformanceTest runs on Windows and Linux"
        if not self.exe:
            return False, "PassMark PerformanceTest is not installed (Install button)"
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        if self._linux:
            return (str(self.exe) if self.exe else None), "the app's tools folder (Install button)"
        return (str(self.exe) if self.exe else None), "Program Files\\PerformanceTest"

    def version(self) -> Optional[str]:
        if not self.exe:
            return None
        if self._linux:             # the pinned zip's version (the program prints it only in its screen)
            return (linux_plan() or {}).get("version")
        from app.benchmark_manager import get_pe_version
        return get_pe_version(str(self.exe))

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        if self._linux:
            return f'"{self.exe}" -r {LINUX_MODES[self.id][0]} (in the run folder)' if self.id in LINUX_MODES else None
        return f'"{self.exe}" /s <script: RUN {self.group}> (through the administrator helper)'

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=why)
        if self._linux:
            return self._run_linux(ctx)
        folder = Path(ctx.output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        csv_path, text_path = folder / "passmark_results.csv", folder / "passmark_results.txt"
        from app.frames import admin_helper
        helper, own = self._helper or admin_helper.session(), None
        command = [str(self.exe), "/s", f"<script: RUN {self.group}>"]
        if helper is None:                  # no helper for this run of tests: one for this test only
            own = helper = admin_helper.AdminHelper()
            why = own.start()
            if why is not None:
                return WorkloadResult(status="skipped" if "declined" in why else "failed",
                                      error=why + "; nothing ran", command=command)
        try:
            answer = helper.passmark(self.group, ctx.timeout_seconds, ctx.stop_event)
        finally:
            if own is not None:
                own.close()
        for path, key in ((csv_path, "csv"), (text_path, "txt")):    # written by the app, not elevated
            try:
                path.write_text(str(answer.get(key) or ""), encoding="utf-8")
            except OSError:
                pass
        exit_code = answer.get("exit_code")
        common = {"command": command, "exit_code": exit_code,
                  "duration_seconds": round(float(answer.get("duration_seconds") or 0.0), 3),
                  "stdout_path": str(csv_path), "stderr_path": str(text_path)}
        status = answer.get("status")
        if status in ("start_failed", "refused"):
            return WorkloadResult(status="failed", error=str(answer.get("error") or "could not start"), **common)
        if status == "stopped":
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if status == "timed_out":
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s (a PassMark "
                                  "message waiting for an answer?)", **common)
        score, why = parse_mark(self.mark, str(answer.get("csv") or ""), str(answer.get("txt") or ""))
        if score is None:
            return WorkloadResult(status="invalid", error=why + (f"; exit code {exit_code}" if exit_code else ""),
                                  **common)
        note = f"PassMark PerformanceTest; test: {self.mark} ({self.group}); PassMark's default test length"
        return WorkloadResult(status="completed", score=round(score, 1), unit=UNIT, metrics={}, note=note,
                              **common)

    def _run_linux(self, ctx: RunContext) -> WorkloadResult:
        r, key, name = LINUX_MODES[self.id]
        folder = Path(ctx.output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        results = folder / name                     # -r writes into the working directory: the run folder
        out, err = folder / "passmark_stdout.txt", folder / "passmark_stderr.txt"
        command = [str(self.exe), "-r", r]
        env = dict(os.environ)
        env.setdefault("TERM", "xterm")             # its screen uses ncurses, which needs a terminal type
        started = time.time()
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(folder), env=env)
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3), "stdout_path": str(out),
                  "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        text = ""
        try:
            if results.is_file() and results.stat().st_mtime >= started - 2:     # written by this run
                with open(results, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(16 * 1024 * 1024)
        except OSError:
            pass
        score, why = parse_linux_results(key, text)
        if score is None:
            code = outcome.exit_code
            return WorkloadResult(status="invalid", error=why + (f"; exit code {code}" if code else ""), **common)
        note = (f"PassMark PerformanceTest Linux; test: {self.mark} (-r {r}); PassMark's default test length; "
                "autorun writes the results to a file and does not upload them")
        return WorkloadResult(status="completed", score=round(score, 1), unit=UNIT, metrics={}, note=note,
                              **common)


def passmark_workloads() -> list:
    return [PassMarkWorkload(t) for t in MODES]

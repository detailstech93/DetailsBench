"""DiskSpd and fio as storage Workloads: four standard tests on one test file in the run folder.

Tests (like CrystalDiskMark's defaults): sequential 1 MiB, queue depth 8, 1 thread (read, write) and random
4 KiB, queue depth 32, 1 thread (read, write). OS caching is off (DiskSpd -Sh, fio --direct=1), so the drive
is measured, not RAM. Score = sequential read (MiB/s); every test's MiB/s, IOPS and mean latency are metrics,
and storage_report.md is written to the run folder.

The test file (1 GiB by default) is created by the tool in the run folder, so the drive that holds the app's
database is the drive measured. The app deletes only that file, which it created, after the run.

Commands use only documented options: DiskSpd -c -d -W -b -o -t -w [-r] -Sh -L -Rxml <file> (sequential is
DiskSpd's default; -si is ignored with one thread, DiskSpd warns); fio --name --filename --size --rw --bs
--iodepth --numjobs --ioengine=windowsaio --direct=1 --thread --ramp_time --runtime --time_based
--group_reporting --output-format=json --output. Parsers read DiskSpd's XML result (TimeSpan/TestTimeSeconds,
TimeSpan/Thread/Target ReadBytes, ReadCount, WriteBytes, WriteCount, TimeSpan/Latency/Average*Milliseconds) and
fio's JSON (jobs[0].read/write: bw_bytes, iops, lat_ns.mean). Verified with raw output of DiskSpd 2.3 and
fio 3.43 (x64) on the development computer, 2026-10-18 (tests/data). Without administrator rights DiskSpd
warns that it cannot use SeManageVolumePrivilege and prepares the test file the slower way; the measurement
itself is not affected.

Rest between disk runs (overnight run, 2026-10-20): DiskSpd's two runs and fio's warm-up wrote back to back and
fio's measured sequential write fell to 1,513 MiB/s (its warm-up a minute before: 5,765; DiskSpd: 5,856): the
drive's fast write cache was full. A disk run that starts less than REST_SECONDS after the previous one ended waits
for the rest of that time first, so each run starts with the cache emptied; the wait is in the run's note.
"""

import json
import math
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

MIB = 1024 * 1024
TEST_FILE = "pcbm_storage_test.dat"
REPORT_FILE = "storage_report.md"
REST_SECONDS = 120                  # between disk runs: the drive empties its write cache (see above)
_last_end: List[Optional[float]] = [None]      # time.monotonic() when the last disk run of this app ended


@dataclass(frozen=True)
class DiskTest:
    id: str
    label: str
    block: str          # "1M" or "4K"
    queue: int
    threads: int
    write: bool
    random: bool


TESTS = (DiskTest("seq1m_q8t1_read", "SEQ1M Q8T1 read", "1M", 8, 1, False, False),
         DiskTest("seq1m_q8t1_write", "SEQ1M Q8T1 write", "1M", 8, 1, True, False),
         DiskTest("rnd4k_q32t1_read", "RND4K Q32T1 read", "4K", 32, 1, False, True),
         DiskTest("rnd4k_q32t1_write", "RND4K Q32T1 write", "4K", 32, 1, True, True))


def _positive(value) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


# ---- DiskSpd ---------------------------------------------------------------------------------------
def diskspd_command(exe: str, test: DiskTest, size_mib: int, seconds: int, warmup: int) -> List[str]:
    return [exe, f"-c{size_mib}M", f"-d{seconds}", f"-W{warmup}", f"-b{test.block}", f"-o{test.queue}",
            f"-t{test.threads}", "-w100" if test.write else "-w0", *(["-r"] if test.random else []),
            "-Sh", "-L", "-Rxml", TEST_FILE]


def parse_diskspd(text: str, write: bool) -> Tuple[Dict[str, float], Optional[str]]:
    """{mib_s, iops, latency_ms?} for the read or write side of one test."""
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError as e:
        return {}, f"not DiskSpd XML: {e}"
    span = root.find("TimeSpan")
    seconds = _positive(span.findtext("TestTimeSeconds")) if span is not None else None
    if seconds is None:
        return {}, "no TestTimeSeconds in the result"
    side = "Write" if write else "Read"
    total_bytes = total_ios = 0.0
    for target in span.iter("Target"):
        total_bytes += float(target.findtext(f"{side}Bytes") or 0)
        total_ios += float(target.findtext(f"{side}Count") or 0)
    if total_bytes <= 0:
        return {}, f"no {side.lower()} bytes in the result"
    out = {"mib_s": round(total_bytes / MIB / seconds, 2), "iops": round(total_ios / seconds, 1)}
    latency = _positive(span.findtext(f"Latency/Average{side}Milliseconds"))
    if latency is not None:
        out["latency_ms"] = round(latency, 4)
    return out, None


# ---- fio -----------------------------------------------------------------------------------------
def fio_command(exe: str, test: DiskTest, size_mib: int, seconds: int, warmup: int, output: str) -> List[str]:
    rw = ("randwrite" if test.random else "write") if test.write else ("randread" if test.random else "read")
    # relative file name: fio treats ':' in a path as a separator, so the run folder is the working directory
    return [exe, f"--name={test.id}", f"--filename={TEST_FILE}", f"--size={size_mib}M", f"--rw={rw}",
            f"--bs={test.block.lower()}", f"--iodepth={test.queue}", f"--numjobs={test.threads}",
            "--ioengine=windowsaio", "--direct=1", "--thread", f"--ramp_time={warmup}", f"--runtime={seconds}",
            "--time_based", "--group_reporting", "--output-format=json", f"--output={output}"]


def parse_fio(text: str, write: bool) -> Tuple[Dict[str, float], Optional[str]]:
    try:
        data = json.loads(text[text.find("{"):] if "{" in text else text)
    except ValueError as e:
        return {}, f"not fio JSON: {e}"
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not jobs:
        return {}, "no jobs in the result"
    side = jobs[0].get("write" if write else "read") or {}
    bw = _positive(side.get("bw_bytes"))
    if bw is None:
        return {}, f"no {'write' if write else 'read'} bandwidth in the result"
    out = {"mib_s": round(bw / MIB, 2)}
    iops = _positive(side.get("iops"))
    if iops is not None:
        out["iops"] = round(iops, 1)
    latency = _positive((side.get("lat_ns") or {}).get("mean"))
    if latency is not None:
        out["latency_ms"] = round(latency / 1e6, 4)
    return out, None


@dataclass(frozen=True)
class DiskTool:
    id: str
    display_name: str
    exe_names: Tuple[str, ...]
    env_var: str
    candidates: Tuple[str, ...]
    license: str


DISKSPD = DiskTool("diskspd", "DiskSpd (storage, 4 tests)", ("diskspd.exe", "diskspd"), "PCBM_DISKSPD",
                   ("%USERPROFILE%\\Tools\\DiskSpd\\amd64\\diskspd.exe",
                    "%LOCALAPPDATA%\\Microsoft\\WinGet\\Links\\diskspd.exe"),     # the Install button (winget)
                   "MIT (Microsoft)")
FIO = DiskTool("fio", "fio (storage, 4 tests)", ("fio.exe", "fio"), "PCBM_FIO",
               ("%ProgramFiles%\\fio\\fio.exe", "%USERPROFILE%\\Tools\\fio\\fio\\fio.exe"), "GPL-2.0")


def find_tool(tool: DiskTool, env: Optional[Mapping[str, str]] = None,
              which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    env = os.environ if env is None else env
    paths = [env.get(tool.env_var)] + [os.path.expandvars(c) if env is os.environ else
                                       _expand(c, env) for c in tool.candidates]
    for p in paths:
        if p and "%" not in p and Path(p).is_file():
            return p
    for name in tool.exe_names:
        found = which(name)
        if found:
            return found
    return None


def _expand(text: str, env: Mapping[str, str]) -> str:
    for key, value in env.items():
        text = text.replace(f"%{key}%", value)
    return text.replace("\\", os.sep)          # the candidates are written with \ (no change on Windows)


def report_markdown(tool: DiskTool, rows: List[Tuple[DiskTest, Dict[str, float], Optional[str]]],
                    size_mib: int, seconds: int, folder: str) -> str:
    lines = [f"# Storage report: {tool.display_name}", "",
             f"- Test file: {size_mib} MiB in the run folder ({folder}); OS cache off",
             f"- Each test: {seconds} s measured after a warm-up", "",
             "| Test | MiB/s | IOPS | Mean latency (ms) | Note |", "|---|---|---|---|---|"]
    for test, values, error in rows:
        cells = [f"{values[k]:,}" if k in values else "-" for k in ("mib_s", "iops", "latency_ms")]
        lines.append(f"| {test.label} | " + " | ".join(cells) + f" | {error or ''} |")
    lines += ["", "'-' = not reported."]
    return "\n".join(lines) + "\n"


class StorageWorkload(Workload):
    benchmark_type = "storage"
    timeout_seconds = 900
    verified = True                   # DiskSpd 2.3 and fio 3.43, real runs on the development computer
    warning = ("Writes a test file to the drive that holds the app's data and keeps it busy: close other "
               "programs that use that drive. The file is deleted after the run.")
    clock = staticmethod(time.monotonic)

    def __init__(self, tool: DiskTool, size_mib: int = 1024, seconds: int = 5, warmup: int = 2,
                 exe: Optional[str] = None, tests=TESTS):
        self.tool, self.size_mib, self.seconds, self.warmup, self.tests = tool, size_mib, seconds, warmup, tests
        self.id, self.display_name = tool.id, tool.display_name
        self.exe = exe if exe is not None else find_tool(tool)

    def availability(self) -> Tuple[bool, str]:
        return (True, self.exe) if self.exe else (False, f"not found ({self.tool.env_var} or PATH)")

    def executable(self) -> Tuple[Optional[str], str]:
        return self.exe, f"{self.tool.env_var}, known folders or PATH"

    def version(self) -> Optional[str]:
        return None    # read from file metadata later; never by running the tool

    def _command(self, test: DiskTest, output: str) -> List[str]:
        if self.tool is DISKSPD:
            return diskspd_command(self.exe, test, self.size_mib, self.seconds, self.warmup)
        return fio_command(self.exe, test, self.size_mib, self.seconds, self.warmup, output)

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        return f"{len(self.tests)} tests, first: " + subprocess.list2cmdline(self._command(self.tests[0], "<out>"))

    def run(self, ctx: RunContext) -> WorkloadResult:
        if not self.exe:
            return WorkloadResult(status="skipped", error=f"{self.tool.display_name}: not found")
        rest = 0.0
        if _last_end[0] is not None:
            rest = max(0.0, REST_SECONDS - (self.clock() - _last_end[0]))
            if rest > 0 and ctx.stop_event.wait(rest):
                return WorkloadResult(status="cancelled", error="stopped by user")
        folder = Path(ctx.output_dir)
        rows, metrics, commands = [], {}, []
        total = 0.0
        out = err = None
        try:
            for test in self.tests:
                result_file = folder / f"{test.id}.{'xml' if self.tool is DISKSPD else 'json'}"
                command = self._command(test, result_file.name)
                commands.append(command)
                out, err = folder / f"{test.id}.stdout.txt", folder / f"{test.id}.stderr.txt"
                outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(folder))
                total += outcome.duration_seconds
                common = {"command": command, "exit_code": outcome.exit_code, "duration_seconds": round(total, 3),
                          "stdout_path": str(out), "stderr_path": str(err)}
                if outcome.start_error:
                    return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
                if outcome.cancelled:
                    return WorkloadResult(status="cancelled", error="stopped by user", **common)
                if outcome.timed_out:
                    return WorkloadResult(status="timed_out", error=f"{test.label}: timeout", **common)
                if outcome.exit_code != 0:
                    return WorkloadResult(status="failed", error=f"{test.label}: exit code {outcome.exit_code}",
                                          **common)
                source = out if self.tool is DISKSPD else result_file
                try:
                    text = source.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    text, values, error = "", {}, f"no result: {e}"
                else:
                    values, error = (parse_diskspd if self.tool is DISKSPD else parse_fio)(text, test.write)
                rows.append((test, values, error))
                for key, unit in (("mib_s", "MiB/s"), ("iops", "IOPS"), ("latency_ms", "ms")):
                    if key in values:
                        metrics[f"{test.id}_{key}"] = (values[key], unit)
        finally:
            _last_end[0] = self.clock()
            try:
                (folder / TEST_FILE).unlink()          # the test file this run created, nothing else
            except OSError:
                pass
        (folder / REPORT_FILE).write_text(report_markdown(self.tool, rows, self.size_mib, self.seconds,
                                                          str(folder)), encoding="utf-8")
        common = {"command": commands[0] if commands else None, "exit_code": 0, "duration_seconds": round(total, 3),
                  "stdout_path": str(out) if out else None, "stderr_path": str(err) if err else None}
        first = rows[0][1].get("mib_s") if rows else None
        if first is None:
            return WorkloadResult(status="invalid", error=f"output not usable: {rows[0][2] if rows else 'no tests'}",
                                  metrics=metrics, **common)
        metrics["tests_with_errors"] = ", ".join(t.label for t, _v, e in rows if e)
        note = (f"the drive rested {rest:.0f} s before this run (its write cache empties after the previous disk "
                "run)") if rest >= 1 else None
        return WorkloadResult(status="completed", score=first, unit="MiB/s", metrics=metrics, note=note, **common)


def storage_workloads() -> List[StorageWorkload]:
    return [StorageWorkload(DISKSPD), StorageWorkload(FIO)]

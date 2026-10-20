"""Geekbench 7 (Primate Labs) as Workloads: the CPU benchmark and the GPU benchmark (OpenCL, Vulkan, CUDA), from its
command line.

The free Geekbench 7 command line (7.1.0, `geekbench7 --help`, read on the owner's PC 2026-10-20) has --cpu,
--gpu [API] (CUDA, OpenCL (default), Vulkan), --gpu-list, --gpu-platform-id, --gpu-device-id, --sysinfo and --unlock;
no option keeps a result offline: "Geekbench 7 requires an active internet connection and automatically uploads
benchmark results to the Geekbench Browser" (its own words). The owner allowed the uploads on 2026-10-20 ("yes, I
allow it to upload, no problem, if it runs automated"); every run says so in its warning and its note.

It prints its scores in the console at the end (real runs on the owner's PC, 2026-10-20, no admin, no window):
    --cpu (about 6 min):        "Single-Core Score             2545" / "Multi-Core Score             22129"
    --gpu OpenCL (about 2 min): "GPU Score                   248425"
    --gpu Vulkan, through this workload: 200121 points in 142 s, device picked: the RTX 5090 (0, 0)
and the result's page "https://browser.geekbench.com/v7/cpu/675555" (kept in the note; the "claim" link with its key
stays only in the run folder's output). A run with no score line (no internet: the upload is part of the run) is
invalid, never a number.

--gpu-list prints one table per API ("| Platform ID | Device ID | Description |"). The app picks the GPU with the most
dedicated memory (app/telemetry/gpu_memory.py, any vendor) when it is in the API's table, else Geekbench's default
(0, 0), and names the device in the note. Scores compare only with the same Geekbench major version, test and API.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

MAJOR = 7
UNIT = "points"
# test id: (title, command-line options without the device, expected seconds for the progress bar)
MODES = {
    "geekbench7_cpu": ("CPU", ["--cpu"], 360),
    "geekbench7_gpu_opencl": ("GPU, OpenCL", ["--gpu", "OpenCL"], 150),
    "geekbench7_gpu_vulkan": ("GPU, Vulkan", ["--gpu", "Vulkan"], 150),
    "geekbench7_gpu_cuda": ("GPU, CUDA", ["--gpu", "CUDA"], 150),
}
SINGLE = re.compile(r"^\s*Single-Core Score\s+(\d+)\s*$", re.M)
MULTI = re.compile(r"^\s*Multi-Core Score\s+(\d+)\s*$", re.M)
GPU = re.compile(r"^\s*GPU Score\s+(\d+)\s*$", re.M)
RESULT_URL = re.compile(r"https://browser\.geekbench\.com/v\d+/(?:cpu|gpu)/\d+(?![\w/?])")
DEVICE = re.compile(r"^\s*Device Name\s+(.+?)\s*$", re.M)
ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*$")
WARNING = ("Geekbench 7's free version uploads every result to the Geekbench Browser (browser.geekbench.com) and needs "
           "the internet; you allowed this on 2026-10-20.")


def program_path(platform: Optional[str] = None, env=None, isfile=os.path.isfile, which=shutil.which,
                 home: Optional[str] = None) -> Optional[str]:
    """geekbench7(.exe): Primate Labs' Windows installer puts it in Program Files\\Geekbench 7; the Linux download is
    a folder Geekbench-7.x.y-Linux(Arm) unpacked anywhere (the PATH, the home folder or /opt)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform == "win32":
        for base in (env.get("ProgramFiles"), env.get("ProgramW6432"), env.get("ProgramFiles(x86)")):
            if base:
                exe = os.path.join(base, "Geekbench 7", "geekbench7.exe")
                if isfile(exe):
                    return exe
        return None
    found = which("geekbench7")
    if found:
        return found
    for root in (home or os.path.expanduser("~"), "/opt"):
        try:
            names = sorted(os.listdir(root), reverse=True)
        except OSError:
            continue
        for name in names:
            if name.startswith("Geekbench-7"):
                exe = os.path.join(root, name, "geekbench7")
                if isfile(exe):
                    return exe
    return None


def parse_gpu_list(text: str) -> dict:
    """{"CUDA": [(platform, device, name)], "OpenCL": [...], ...} from --gpu-list."""
    out, api = {}, None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("CUDA", "OpenCL", "Vulkan", "Metal"):
            api = stripped
            out.setdefault(api, [])
            continue
        m = ROW.match(stripped)
        if m and api:
            out[api].append((int(m.group(1)), int(m.group(2)), m.group(3)))
    return out


def pick_device(devices: List[tuple], preferred: Optional[str]) -> Optional[tuple]:
    """The row of the preferred GPU (its name), else the first row (Geekbench's default); None for an empty table."""
    if not devices:
        return None
    if preferred:
        for row in devices:
            if row[2].strip().lower() == preferred.strip().lower():
                return row
    return devices[0]


def biggest_gpu_name() -> Optional[str]:
    """The name of the GPU with the most dedicated memory (Windows, DXGI), or None."""
    if sys.platform != "win32":
        return None
    try:
        from app.telemetry.gpu_memory import MICROSOFT_VENDOR, dxgi_adapters
        adapters = [a for a in dxgi_adapters() if a.vendor_id != MICROSOFT_VENDOR]
    except Exception:
        return None
    return max(adapters, key=lambda a: a.dedicated_total).name if adapters else None


def parse_scores(text: str, gpu: bool) -> Tuple[Optional[float], dict, Optional[str]]:
    """(score, metrics, why): the CPU test's score is Multi-Core (Single-Core is a metric); the GPU test's GPU Score."""
    if gpu:
        found = GPU.findall(text)
        if not found:
            return None, {}, _why(text, "GPU Score")
        score = float(found[-1])
        metrics = {"gpu_score": (score, UNIT)}
    else:
        single, multi = SINGLE.findall(text), MULTI.findall(text)
        if not multi:
            return None, {}, _why(text, "Multi-Core Score")
        score = float(multi[-1])
        metrics = {"multi_core_score": (score, UNIT)}
        if single:
            metrics["single_core_score"] = (float(single[-1]), UNIT)
    if score <= 0:
        return None, {}, f"the score {score:g} is not a result"
    return score, metrics, None


def _why(text: str, wanted: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("Running ")]
    hint = " (it needs the internet: the free version uploads every result)" if "upload" in text.lower() else ""
    return f"no '{wanted}' line in Geekbench's output{hint}" + (f"; it ended with: {lines[-1][:200]}" if lines else "")


class GeekbenchWorkload(Workload):
    benchmark_type = "cpu"
    warning = WARNING
    fixed_counts = (0, 1)           # one run, no warm-up: every run is one more upload

    def __init__(self, test_id: str = "geekbench7_cpu", exe: Optional[str] = None,
                 finder: Callable[[], Optional[str]] = program_path,
                 gpu_name: Callable[[], Optional[str]] = biggest_gpu_name, runner: Callable = subprocess.run):
        self.id = test_id
        self.title, self.options, self.expected = MODES[test_id]
        self.gpu = self.options[0] == "--gpu"
        self.benchmark_type = "gpu" if self.gpu else "cpu"
        self.timeout_seconds = 1200 if self.gpu else 1800
        self.display_name = f"Geekbench {MAJOR} ({self.title})"
        self.verified = test_id != "geekbench7_gpu_cuda"       # real runs here, 2026-10-20 (CUDA not run yet)
        self.exe = exe if exe is not None else finder()
        self._gpu_name, self._run = gpu_name, runner
        self._started: Optional[float] = None

    @property
    def progress(self):
        if self._started is None:
            return None
        gone = time.monotonic() - self._started
        return min(0.97, gone / self.expected), max(0.0, self.expected - gone)

    def availability(self) -> Tuple[bool, str]:
        if not self.exe:
            return False, "Geekbench 7 is not installed (geekbench7 not found)"
        return True, self.exe

    def executable(self) -> Tuple[Optional[str], str]:
        return self.exe, "Geekbench 7's folder"

    def version(self) -> Optional[str]:
        return str(MAJOR)

    def describe_command(self) -> Optional[str]:
        return " ".join([f'"{self.exe}"', *self.options]) if self.exe else None

    def _device(self) -> Tuple[Optional[tuple], str]:
        """(platform, device, name) for this API, or (None, why)."""
        try:
            res = self._run([self.exe, "--gpu-list"], capture_output=True, text=True, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"--gpu-list failed: {e}"
        rows = parse_gpu_list(res.stdout or "").get(self.options[1], [])
        row = pick_device(rows, self._gpu_name())
        return (row, "") if row else (None, f"Geekbench lists no {self.options[1]} device on this PC")

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=why)
        folder = Path(ctx.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        command, device = [self.exe, *self.options], None
        if self.gpu:
            device, why = self._device()
            if device is None:
                return WorkloadResult(status="skipped", error=why)
            command += ["--gpu-platform-id", str(device[0]), "--gpu-device-id", str(device[1])]
        out, err = folder / "stdout_geekbench.txt", folder / "stderr_geekbench.txt"
        self._started = time.monotonic()
        try:
            outcome = run_process(command, out, err, ctx.timeout_seconds or self.timeout_seconds, ctx.stop_event,
                                  cwd=str(Path(self.exe).parent))
        finally:
            self._started = None
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3), "stdout_path": str(out),
                  "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        text = out.read_text(encoding="utf-8", errors="replace")
        score, metrics, why = parse_scores(text, self.gpu)
        if score is None:
            return WorkloadResult(status="invalid", error=why + (f"; exit code {outcome.exit_code}"
                                                                 if outcome.exit_code else ""), **common)
        if outcome.exit_code != 0:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}", **common)
        parts = [f"Geekbench {MAJOR}; test: {self.title}"]
        if self.gpu:
            names = DEVICE.findall(text)
            parts.append(f"Device: {names[-1] if names else device[2]}")
        else:
            single = metrics.get("single_core_score")
            parts.append(f"Single-Core Score: {single[0]:g}" if single else "Single-Core Score: not printed")
            parts.append(f"Multi-Core Score: {score:g}")
        url = RESULT_URL.findall(text)
        parts.append(f"Result online: {url[-1]}" if url else "Result online: no link printed")
        parts.append("uploaded to the Geekbench Browser (the free version always uploads; allowed by the owner)")
        return WorkloadResult(status="completed", score=score, unit=UNIT, metrics=metrics, note="; ".join(parts),
                              **common)


def geekbench_workloads() -> list:
    return [GeekbenchWorkload(t) for t in MODES]

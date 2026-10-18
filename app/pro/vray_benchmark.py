"""V-Ray 6 Benchmark (Chaos) as Workloads: CPU (V-Ray), GPU CUDA and GPU RTX (V-Ray GPU).

The program is one file from Chaos's own download server, installed by the Install button into the app's tools
folder (data/installers.py, "single"): vray-benchmark-<v>-cli.exe on Windows (the command-line build), the one
Linux x64 program on Linux. Its command line is the one in Chaos's documentation ("Using Benchmark",
documentation.chaos.com/space/VRAYBENCH/113119644, 2025-11-20):

    vray-benchmark-cli --mode vray|vray-gpu [--rtx] [--with-devices 0,1] --output <absolute file.json>
    vray-benchmark-cli --mode vray-gpu --list-devices

License: the program asks to accept Chaos's License Agreement on its first run; the documentation answers it
with `echo y |`. The app gives that answer only after the owner accepted the agreement in the app
(app/ui/run_tests.py asks once, settings.json keeps it); without it a run is skipped. Nothing is submitted:
the CLI never uploads unless asked through the GUI with an account.

Score: the program's own, in vsamples (V-Ray CPU), vpaths (V-Ray GPU CUDA) or vrays (V-Ray GPU RTX); the three
are not comparable with each other (documentation, "Performance Score"). Each test renders for 1 minute.
GPU: NVIDIA only (CUDA Maxwell or later, RTX; driver 520+); the CPU is also a CUDA device, so the GPU tests
pick the GPU devices only (--with-devices) and say which.
"""

import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

VRAY_VERSION = "6.00.02"
EULA_URL = "https://documentation.chaos.com/space/VRAYBENCH/113119826"
EULA_KEY = "vray_benchmark_license_accepted"
# test id: (title, --mode, RTX, unit)
MODES = {
    "vray_benchmark": ("CPU", "vray", False, "vsamples"),
    "vray_benchmark_gpu": ("GPU, CUDA", "vray-gpu", False, "vpaths"),
    "vray_benchmark_rtx": ("GPU, RTX", "vray-gpu", True, "vrays"),
}


def _os_key() -> str:
    return "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def program_plan() -> Optional[dict]:
    from data.installers import INSTALLERS
    return ((INSTALLERS.get("vray_benchmark") or {}).get(_os_key()) or {}).get("zip")


def program_path(data_dir: Optional[Path] = None) -> Optional[Path]:
    from app.install import direct
    plan = program_plan()
    if not plan:
        return None
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def license_accepted(data_dir: Optional[Path] = None) -> bool:
    from app.paths import get_data_dir, load_settings
    value = load_settings(Path(data_dir or get_data_dir()))[0].get(EULA_KEY)
    return isinstance(value, dict) and value.get("url") == EULA_URL


def accept_license(data_dir: Optional[Path] = None) -> None:
    from app.paths import get_data_dir, save_setting
    save_setting(Path(data_dir or get_data_dir()), EULA_KEY, {"url": EULA_URL, "date": date.today().isoformat()})


def license_question() -> str:
    return ("V-Ray Benchmark is made by Chaos. Before it runs the first time you must accept Chaos's "
            f"License and Services Agreement (for consumers: its Consumer End User License Agreement):\n\n{EULA_URL}"
            "\n\nOpen the link and read it. Do you accept it? The app then answers \"y\" to the program's license "
            "question each time it runs this benchmark. Nothing is uploaded to Chaos.")


def build_command(exe: str, mode: str, rtx: bool, output: str, devices: Optional[List[int]] = None) -> List[str]:
    command = [exe, "--mode", mode]
    if rtx:
        command.append("--rtx")
    if devices:
        command += ["--with-devices", ",".join(str(d) for d in devices)]
    return command + ["--output", output]


def parse_devices(text: str) -> List[Tuple[int, str]]:
    """(index, name) from --list-devices: lines like "[0] NVIDIA GeForce RTX 5090 Laptop GPU" or "0: name"."""
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*\[?(\d+)\]?[:.)]?\s+(.+?)\s*$", line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


def gpu_devices(devices: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """The NVIDIA GPUs (V-Ray GPU renders on NVIDIA only; it also lists the CPU as a CUDA device, and other
    numbered lines, such as the license text, are not devices)."""
    nvidia = re.compile(r"\b(nvidia|geforce|quadro|tesla|titan|rtx)\b", re.I)
    cpu = re.compile(r"\b(cpu|processor|core\(tm\)|ryzen|xeon|epyc|threadripper|core ultra)\b", re.I)
    return [(i, n) for i, n in devices if nvidia.search(n) and not cpu.search(n)]


def _number(value) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    return float(value) if ok else None


def _find(data, names) -> Optional[object]:
    """The first value under one of names (case-insensitive key), searched depth-first."""
    if isinstance(data, dict):
        for k, v in data.items():
            if str(k).lower() in names:
                return v
        for v in data.values():
            found = _find(v, names)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find(v, names)
            if found is not None:
                return found
    return None


def parse_result(text: str, unit: str) -> Tuple[Optional[float], Dict[str, object], Optional[str]]:
    """(score, details, error) from the --output JSON file."""
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, {}, f"the result file is not JSON ({e})"
    score = _number(_find(data, {"score", unit}))
    if score is None:
        return None, {}, "no score in the result file"
    details = {}
    for key, names in (("version", {"version", "benchmark_version"}), ("devices", {"devices", "device", "configuration"})):
        value = _find(data, names)
        if isinstance(value, list):
            value = ", ".join(str(v.get("name", v)) if isinstance(v, dict) else str(v) for v in value)
        if isinstance(value, (str, int, float)) and str(value).strip():
            details[key] = str(value)
    return score, details, None


class VRayBenchmarkWorkload(Workload):
    timeout_seconds = 1800
    verified = False                  # set True only after a real run on this computer was checked

    def __init__(self, test_id: str = "vray_benchmark", exe: Optional[Path] = None, data_dir: Optional[Path] = None):
        self.id = test_id
        self.title, self.mode, self.rtx, self.unit = MODES[test_id]
        self.exe = exe if exe is not None else program_path()
        self.data_dir = data_dir
        self.display_name = f"V-Ray Benchmark {VRAY_VERSION} ({self.title})"
        self.benchmark_type = "cpu" if self.mode == "vray" else "gpu"
        self.license = (EULA_KEY, "V-Ray Benchmark", license_question)   # asked once by app/ui/run_tests.py

    def license_ok(self) -> bool:
        return license_accepted(self.data_dir)

    def accept_license(self) -> None:
        accept_license(self.data_dir)

    def availability(self) -> Tuple[bool, str]:
        if not self.exe:
            return False, "V-Ray Benchmark is not installed (Install button)"
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "the app's tools folder"

    def version(self) -> Optional[str]:
        return VRAY_VERSION

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        return subprocess.list2cmdline(build_command(str(self.exe), self.mode, self.rtx, "<run folder>/vray_result.json",
                                                     [0] if self.mode != "vray" else None))

    def _answer(self, folder: Path) -> Path:
        answer = folder / "license_answer.txt"
        answer.write_text("y\n", encoding="ascii")     # the owner accepted the agreement in the app
        return answer

    def _run(self, command, folder: Path, name: str, timeout, ctx: RunContext):
        out, err = folder / f"stdout_{name}.txt", folder / f"stderr_{name}.txt"
        outcome = run_process(command, out, err, timeout, ctx.stop_event, cwd=str(self.exe.parent),
                              stdin_path=self._answer(folder))
        return outcome, out, err

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"V-Ray Benchmark: {why}")
        if not self.license_ok():
            return WorkloadResult(status="skipped", error="V-Ray Benchmark: Chaos's license agreement was not "
                                                          "accepted in the app, so it was not run")
        folder = Path(ctx.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        devices, used = None, []
        if self.mode != "vray":
            command = [str(self.exe), "--mode", self.mode, "--list-devices"]
            outcome, out, err = self._run(command, folder, "devices", 300, ctx)
            common = {"command": command, "exit_code": outcome.exit_code, "stdout_path": str(out),
                      "stderr_path": str(err)}
            if outcome.cancelled:
                return WorkloadResult(status="cancelled", error="stopped by user", **common)
            if outcome.start_error or outcome.timed_out or outcome.exit_code != 0:
                why = outcome.start_error or ("no answer" if outcome.timed_out else f"exit code {outcome.exit_code}")
                return WorkloadResult(status="failed", error=f"listing the devices failed: {why}", **common)
            listed = parse_devices(out.read_text(encoding="utf-8", errors="replace"))
            used = gpu_devices(listed)
            if not used:
                return WorkloadResult(status="skipped", error="V-Ray GPU found no NVIDIA GPU to render on ("
                                      + (", ".join(n for _, n in listed) or "no device listed") + ")", **common)
            devices = [i for i, _ in used]
        result_file = folder / "vray_result.json"
        command = build_command(str(self.exe), self.mode, self.rtx, str(result_file.resolve()), devices)
        outcome, out, err = self._run(command, folder, "benchmark", ctx.timeout_seconds, ctx)
        common = {"command": command, "exit_code": outcome.exit_code, "duration_seconds": round(outcome.duration_seconds, 3),
                  "stdout_path": str(out), "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        if outcome.exit_code != 0:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}", **common)
        if not result_file.is_file():
            return WorkloadResult(status="invalid", error="the benchmark wrote no result file", **common)
        score, details, error = parse_result(result_file.read_text(encoding="utf-8", errors="replace"), self.unit)
        if error:
            return WorkloadResult(status="invalid", error=error, **common)
        parts = [("V-Ray Benchmark", details.get("version") or VRAY_VERSION), ("Engine", self.title),
                 ("Devices", details.get("devices") or ", ".join(n for _, n in used) or None)]
        note = "; ".join(f"{k}: {v}" for k, v in parts if v)
        return WorkloadResult(status="completed", score=round(score, 2), unit=self.unit, metrics={}, note=note,
                              **common)


def vray_workloads() -> list:
    return [VRayBenchmarkWorkload(t) for t in MODES]

"""Blender Benchmark (Blender Open Data) as a Workload: CPU and GPU.

The launcher is benchmark-launcher-cli (Windows x64 zip, Linux x64 tar.gz from download.blender.org), installed by
the Install button into the app's tools folder (data/installers.py, app/install/direct.py). Its non-interactive
commands are the ones in its own README.txt:

    benchmark-launcher-cli blender download <v>
    benchmark-launcher-cli scenes download --blender-version <v> <scene>...
    benchmark-launcher-cli devices --blender-version <v>
    benchmark-launcher-cli benchmark --blender-version <v> --device-type <CPU|OPTIX|...> --json <scene>

The same Install button then downloads Blender and the three scenes (download_content); the test shows as not
installed until both are there. One run renders the three current scenes (monster, junkshop, classroom), one
command per scene. --submit is never passed: nothing is uploaded.

GPU: the device type is chosen on the PC that runs it, from `devices` (OPTIX on NVIDIA first, then CUDA, HIP for
AMD, ONEAPI for Intel, METAL).

Score = samples per minute summed over the scenes, as Blender Open Data defines its score ("the estimated number
of samples per minute, summed for all benchmark scenes", opendata.blender.org/about, 2026-10-18). Each scene's
samples per minute and render time are metrics; Blender, launcher and device go into the run's note for the
report (app/report.py, Blender section). A scene without a result fails the run; there is never a partial score.
"""

import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from app.automation.process import run_process, terminate_tree
from app.automation.workload import RunContext, Workload, WorkloadResult

BLENDER_VERSION = "5.2.0"          # newest in `blender list` on 2026-10-18
SCENES = ("monster", "junkshop", "classroom")
CACHE_NAME = "blender-benchmark-launcher"
GPU_TYPES = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")      # preferred first
DOWNLOAD_TIMEOUT = 3 * 3600.0
DOWNLOAD_TRIES = 3                 # a dropped connection is tried again (seen 2026-10-18: a read timeout at 48 %)
CONTENT_NOTE = ("Then Blender {v} and its three test scenes (monster, junkshop, classroom), about 1.5 GB on disk, are "
                "downloaded from Blender's own server by the launcher itself, which checks them.")


def _os_key() -> str:
    return "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def cache_parent() -> Path:
    """The folder that holds the launcher's cache (Blender and the scenes, in blender-benchmark-launcher/).

    The launcher uses Go's os.UserCacheDir: %LOCALAPPDATA% on Windows, $XDG_CACHE_HOME or ~/.cache on Linux.
    Windows: the app passes %USERPROFILE%/PCBenchmarkManager/blender as LOCALAPPDATA instead. On the owner's PC
    (Windows 11 26300, 2026-10-18) blender.exe refused to start from anywhere under AppData/Local (error 14001,
    side-by-side configuration) while the same files ran from other folders. Linux keeps the launcher's own.
    """
    if _os_key() == "windows":
        return Path(os.environ.get("USERPROFILE") or Path.home()) / "PCBenchmarkManager" / "blender"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def launcher_env(parent: Optional[Path] = None) -> Dict[str, str]:
    env = dict(os.environ)
    if parent is not None or _os_key() == "windows":
        env["LOCALAPPDATA" if _os_key() == "windows" else "XDG_CACHE_HOME"] = str(parent or cache_parent())
    return env


def _downloaded(folder: Path, prefix: str) -> bool:
    """A launcher download is complete when its folder (name starting with prefix) sits next to valid.launcher."""
    if not folder.is_dir():
        return False
    return any(d.is_dir() and (d / "valid.launcher").is_file()
               and any(c.is_dir() and (c.name == prefix or c.name.startswith(prefix + "-")) for c in d.iterdir())
               for d in folder.iterdir())


def missing_downloads(version: str = BLENDER_VERSION, scenes=SCENES, parent: Optional[Path] = None) -> List[str]:
    cache = (parent or cache_parent()) / CACHE_NAME
    missing = [] if _downloaded(cache / "blender-versions", f"blender-{version}") else [f"Blender {version}"]
    return missing + [s for s in scenes if not _downloaded(cache / "scenes", s)]


def launcher_plan() -> Optional[dict]:
    from data.installers import INSTALLERS
    return ((INSTALLERS.get("blender_benchmark") or {}).get(_os_key()) or {}).get("zip")


def launcher_path(data_dir: Optional[Path] = None) -> Optional[Path]:
    from app.install import direct
    plan = launcher_plan()
    if not plan:
        return None
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def build_command(exe: str, device_type: str, scene: str, version: str = BLENDER_VERSION) -> List[str]:
    return [exe, "benchmark", "--blender-version", version, "--device-type", device_type, "--json", scene]


def download_commands(exe: str, version: str = BLENDER_VERSION, scenes=SCENES) -> List[List[str]]:
    return [[exe, "blender", "download", version],
            [exe, "scenes", "download", "--blender-version", version, *scenes]]


def _last_percent(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    found = re.findall(r"(\d+(?:\.\d+)?)%", tail)
    return f"{min(100.0, float(found[-1])):.0f} %" if found else None


def _errors(log: Path) -> str:
    """The launcher's own ERROR: lines from its output."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = re.findall(r"ERROR: ([^\r\n]+)", text)
    return " ".join(found[-2:])


def _run_logged(command: List[str], log: Path, env: dict, cwd: str, timeout: float,
                progress: Callable[[Optional[str]], None]) -> Tuple[Optional[int], str]:
    """Run one launcher command, its output in log; (exit code or None, why)."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with open(log, "wb") as out:
        try:
            proc = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=cwd, env=env, shell=False, creationflags=flags)
        except OSError as e:
            return None, f"could not start the launcher: {e}"
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if time.monotonic() > deadline:
                terminate_tree(proc.pid)
                return None, f"no end after {timeout / 60:.0f} min"
            progress(_last_percent(log))
            time.sleep(1.0)
    return proc.returncode, ""


def download_content(exe: Path, version: str = BLENDER_VERSION, scenes=SCENES, parent: Optional[Path] = None,
                     progress: Callable[[str], None] = lambda text: None, timeout: float = DOWNLOAD_TIMEOUT):
    """Blender and the scenes through the launcher's own download commands. Returns winget.Outcome."""
    from app.install.winget import Outcome
    parent = parent or cache_parent()
    if not missing_downloads(version, scenes, parent):
        return Outcome(True, f"Blender {version} and the scenes are already in {parent / CACHE_NAME}.")
    parent.mkdir(parents=True, exist_ok=True)
    env = launcher_env(parent)
    steps = (f"Downloading Blender {version}", "Downloading the test scenes (" + ", ".join(scenes) + ")")
    for command, step, name in zip(download_commands(str(exe), version, scenes), steps, ("blender", "scenes")):
        log = parent / f"download_{name}.txt"
        for attempt in range(1, DOWNLOAD_TRIES + 1):
            label = step + (f" (try {attempt} of {DOWNLOAD_TRIES})" if attempt > 1 else "")
            progress(label + "...")
            code, why = _run_logged(command, log, env, str(exe.parent), timeout,
                                    lambda pct, s=label: progress(f"{s}... {pct}" if pct else f"{s}..."))
            if not why and code == 0:
                break
            why = why or _errors(log) or f"exit code {code}"
        else:
            return Outcome(False, f"{step} failed after {DOWNLOAD_TRIES} tries: {why}. Check the internet "
                                  f"connection and press Install again. The launcher's output is in {log}.")
    left = missing_downloads(version, scenes, parent)
    if left:
        return Outcome(False, "The launcher finished but these are still missing: " + ", ".join(left)
                       + f". See {parent}.")
    return Outcome(True, f"Blender {version} and the scenes are ready in {parent / CACHE_NAME}.")


def parse_devices(text: str) -> List[Tuple[str, str]]:
    """`devices` prints one "name<TAB>TYPE" line per device."""
    out = []
    for line in text.splitlines():
        name, sep, kind = line.rpartition("\t")
        if sep and name.strip() and kind.strip():
            out.append((name.strip(), kind.strip().upper()))
    return out


def pick_gpu(devices: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """(device type, device name) of the GPU to use, by GPU_TYPES order."""
    for kind in GPU_TYPES:
        for name, k in devices:
            if k == kind:
                return kind, name
    return None


def _number(value) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    return float(value) if ok else None


def parse_scene(text: str, scene: str) -> Tuple[Optional[float], Dict[str, object], Dict[str, object], Optional[str]]:
    """(samples per minute, metrics, record, error) from the launcher's --json output for one scene."""
    match = re.search(r"\[\s*\{", text)       # the progress bar before it prints "[----"
    try:
        data = json.JSONDecoder().raw_decode(text, match.start())[0] if match else None
    except ValueError as e:
        return None, {}, {}, f"{scene}: the output is not JSON ({e})"
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None, {}, {}, f"{scene}: no result in the output"
    result = data[0]
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    device = result.get("device_info") if isinstance(result.get("device_info"), dict) else {}
    devices = [d.get("name") for d in device.get("compute_devices") or [] if isinstance(d, dict)]
    record = {"scene": (result.get("scene") or {}).get("label"),
              "blender": (result.get("blender_version") or {}).get("version"),
              "launcher": (result.get("benchmark_launcher") or {}).get("label"),
              "device_type": device.get("device_type"), "devices": ", ".join(str(d) for d in devices if d),
              "cpu_threads": device.get("num_cpu_threads")}
    record = {k: v for k, v in record.items() if v}
    spm = _number(stats.get("samples_per_minute"))
    if spm is None:
        return None, {}, record, f"{scene}: no samples per minute in the result"
    metrics: Dict[str, object] = {f"samples_per_minute@{scene}": (round(spm, 3), "samples/min")}
    for key, name in (("total_render_time", "render_time_s"), ("time_for_samples", "time_for_samples_s")):
        if _number(stats.get(key)) is not None:
            metrics[f"{name}@{scene}"] = (round(_number(stats.get(key)), 3), "s")
    if _number(stats.get("device_peak_memory")) is not None:
        metrics[f"peak_memory_mb@{scene}"] = (round(_number(stats.get("device_peak_memory")), 1), "MB")
    return spm, metrics, record, None


def run_note(records: List[dict], scenes) -> str:
    """"Blender: ...; Launcher: ...; Device: ...; Scenes: ..." (read back by app/report.py)."""
    first = records[0] if records else {}
    parts = [("Blender", first.get("blender")), ("Launcher", first.get("launcher")),
             ("Device", f"{first.get('devices')} ({first.get('device_type')})" if first.get("devices") else None),
             ("CPU threads", first.get("cpu_threads") if first.get("device_type") == "CPU" else None),
             ("Scenes", ", ".join(scenes))]
    return "; ".join(f"{k}: {v}" for k, v in parts if v)


class BlenderBenchmarkWorkload(Workload):
    benchmark_type = "cpu"
    timeout_seconds = 3600
    verified = True                   # GPU (OPTIX, 8,472) and CPU (465) runs checked by the owner, 2026-10-18

    def __init__(self, device_type: str = "CPU", exe: Optional[Path] = None, version: str = BLENDER_VERSION,
                 scenes=SCENES, cache: Optional[Path] = None):
        self.cache = cache
        self.device_type = device_type            # "CPU" or "GPU" (the type is chosen when it runs)
        self.exe = exe if exe is not None else launcher_path()
        self.blender_version, self.scenes = version, tuple(scenes)
        gpu = device_type != "CPU"
        self.id = "blender_benchmark_gpu" if gpu else "blender_benchmark"
        self.display_name = f"Blender Benchmark {version} ({'GPU' if gpu else 'CPU'})"
        self.benchmark_type = "gpu" if gpu else "cpu"

    def availability(self) -> Tuple[bool, str]:
        if not self.exe:
            return False, "the Blender Benchmark launcher is not installed (Install button)"
        missing = missing_downloads(self.blender_version, self.scenes, self.cache)
        if missing:
            return False, ("not installed yet: " + ", ".join(missing)
                           + " (the Install button downloads them)")
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "the app's tools folder"

    def version(self) -> Optional[str]:
        return self.blender_version

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        kind = "CPU" if self.device_type == "CPU" else "<GPU type>"
        return "\n".join(subprocess.list2cmdline(build_command(str(self.exe), kind, s, self.blender_version))
                         for s in self.scenes)

    def _gpu_type(self, folder: Path, ctx: RunContext) -> Tuple[Optional[str], Optional[WorkloadResult]]:
        command = [str(self.exe), "devices", "--blender-version", self.blender_version]
        out, err = folder / "stdout_devices.txt", folder / "stderr_devices.txt"
        outcome = run_process(command, out, err, 300, ctx.stop_event, cwd=str(self.exe.parent),
                              env=launcher_env(self.cache))
        common = {"command": command, "exit_code": outcome.exit_code, "stdout_path": str(out), "stderr_path": str(err)}
        if outcome.cancelled:
            return None, WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.start_error or outcome.timed_out or outcome.exit_code != 0:
            why = outcome.start_error or ("no answer" if outcome.timed_out else f"exit code {outcome.exit_code}")
            return None, WorkloadResult(status="failed", error=f"listing the devices failed: {why}", **common)
        devices = parse_devices(out.read_text(encoding="utf-8", errors="replace"))
        chosen = pick_gpu(devices)
        if not chosen:
            return None, WorkloadResult(status="skipped", error="Blender found no GPU it can render on ("
                                        + (", ".join(f"{n} {k}" for n, k in devices) or "no device") + ")", **common)
        return chosen[0], None

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"Blender Benchmark: {why}")
        folder = Path(ctx.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        device_type = self.device_type
        if device_type != "CPU":
            device_type, failed = self._gpu_type(folder, ctx)
            if failed:
                return failed
        total, metrics, records, commands, seconds = 0.0, {}, [], [], 0.0
        for scene in self.scenes:
            command = build_command(str(self.exe), device_type, scene, self.blender_version)
            commands.append(command)
            out, err = folder / f"stdout_{scene}.txt", folder / f"stderr_{scene}.txt"
            left = None if ctx.timeout_seconds is None else max(1.0, ctx.timeout_seconds - seconds)
            outcome = run_process(command, out, err, left, ctx.stop_event, cwd=str(self.exe.parent),
                                  env=launcher_env(self.cache))
            seconds += outcome.duration_seconds
            common = {"command": command, "exit_code": outcome.exit_code, "duration_seconds": round(seconds, 3),
                      "stdout_path": str(out), "stderr_path": str(err)}
            if outcome.start_error:
                return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
            if outcome.cancelled:
                return WorkloadResult(status="cancelled", error="stopped by user", **common)
            if outcome.timed_out:
                return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
            if outcome.exit_code != 0:
                return WorkloadResult(status="failed", error=f"{scene}: exit code {outcome.exit_code}", **common)
            spm, scene_metrics, record, error = parse_scene(out.read_text(encoding="utf-8", errors="replace"), scene)
            if error:
                return WorkloadResult(status="invalid", error=error, **common)
            total += spm
            metrics.update(scene_metrics)
            records.append(record)
        (folder / "blender_run.json").write_text(json.dumps({"device_type": device_type, "scenes": records,
                                                             "commands": commands}, indent=2), encoding="utf-8")
        return WorkloadResult(status="completed", score=round(total, 3), unit="samples/min", metrics=metrics,
                              command=commands[-1], exit_code=0, duration_seconds=round(seconds, 3),
                              stdout_path=str(folder / f"stdout_{self.scenes[-1]}.txt"),
                              stderr_path=str(folder / f"stderr_{self.scenes[-1]}.txt"),
                              note=run_note(records, self.scenes))


def blender_workloads() -> list:
    return [BlenderBenchmarkWorkload("CPU"), BlenderBenchmarkWorkload("GPU")]

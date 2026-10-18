"""Blender Benchmark (Blender Open Data) as a Workload: CPU and GPU (OptiX).

The launcher is benchmark-launcher-cli.exe, installed by the Install button into the app's tools folder
(data/installers.py, app/install/direct.py). Its non-interactive commands are the ones in its own README.txt:

    benchmark-launcher-cli benchmark --blender-version <v> --device-type <CPU|OPTIX|...> --json <scene>

One run renders the three current scenes (monster, junkshop, classroom), one command per scene. --submit is never
passed: nothing is uploaded. Blender and the scenes must already be downloaded (about 1.5 GB, into cache_parent());
a run never downloads them.

Score = samples per minute summed over the scenes, as Blender Open Data defines its score ("the estimated number
of samples per minute, summed for all benchmark scenes", opendata.blender.org/about, 2026-10-18). Each scene's
samples per minute and render time are metrics. A scene without a result fails the run; there is never a
partial score.
"""

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

BLENDER_VERSION = "5.2.0"          # newest in `blender list` on 2026-10-18
SCENES = ("monster", "junkshop", "classroom")
EXE_NAME = "benchmark-launcher-cli.exe"
CACHE_NAME = "blender-benchmark-launcher"


def cache_parent() -> Path:
    """Where the launcher keeps Blender and the scenes: %USERPROFILE%/PCBenchmarkManager/blender.

    The launcher keeps them in its user cache folder (Go's os.UserCacheDir, which is %LOCALAPPDATA%); the app
    passes it this folder as LOCALAPPDATA instead. On this computer (Windows 11 26300, 2026-10-18) blender.exe
    refused to start from anywhere under AppData/Local (error 14001, side-by-side configuration), while the
    same files ran from other folders.
    """
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "PCBenchmarkManager" / "blender"


def launcher_env(parent: Optional[Path] = None) -> Dict[str, str]:
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(parent or cache_parent())
    return env


def _downloaded(folder: Path, name: str) -> bool:
    """A launcher download is complete when its folder sits next to valid.launcher."""
    return any((d / name).is_dir() and (d / "valid.launcher").is_file()
               for d in folder.glob("*") if d.is_dir()) if folder.is_dir() else False


def missing_downloads(version: str = BLENDER_VERSION, scenes=SCENES, parent: Optional[Path] = None) -> List[str]:
    cache = (parent or cache_parent()) / CACHE_NAME
    missing = [] if _downloaded(cache / "blender-versions", f"blender-{version}-windows-x64") else [f"Blender {version}"]
    return missing + [s for s in scenes if not _downloaded(cache / "scenes", s)]


def launcher_path(data_dir: Optional[Path] = None) -> Optional[Path]:
    from app.install import direct
    from data.installers import INSTALLERS
    plan = INSTALLERS["blender_benchmark"]["windows"]["zip"]
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def build_command(exe: str, device_type: str, scene: str, version: str = BLENDER_VERSION) -> List[str]:
    return [exe, "benchmark", "--blender-version", version, "--device-type", device_type, "--json", scene]


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
              "device_type": device.get("device_type"), "devices": ", ".join(str(d) for d in devices if d)}
    record = {k: v for k, v in record.items() if v}
    spm = _number(stats.get("samples_per_minute"))
    if spm is None:
        return None, {}, record, f"{scene}: no samples per minute in the result"
    metrics: Dict[str, object] = {f"samples_per_minute@{scene}": (round(spm, 3), "samples/min")}
    for key, name in (("total_render_time", "render_time_s"), ("time_for_samples", "time_for_samples_s")):
        if _number(stats.get(key)) is not None:
            metrics[f"{name}@{scene}"] = (round(_number(stats.get(key)), 3), "s")
    return spm, metrics, record, None


class BlenderBenchmarkWorkload(Workload):
    benchmark_type = "cpu"
    timeout_seconds = 3600
    verified = False                  # set True only after a real run on this computer was checked

    def __init__(self, device_type: str = "CPU", exe: Optional[Path] = None, version: str = BLENDER_VERSION,
                 scenes=SCENES, cache: Optional[Path] = None):
        self.cache = cache
        self.device_type = device_type
        self.exe = exe if exe is not None else launcher_path()
        self.blender_version, self.scenes = version, tuple(scenes)
        gpu = device_type != "CPU"
        self.id = "blender_benchmark_gpu" if gpu else "blender_benchmark"
        self.display_name = f"Blender Benchmark {version} ({'GPU, ' + device_type if gpu else 'CPU'})"
        self.benchmark_type = "gpu" if gpu else "cpu"

    def availability(self) -> Tuple[bool, str]:
        if not self.exe:
            return False, "the Blender Benchmark launcher is not installed (Install button)"
        missing = missing_downloads(self.blender_version, self.scenes, self.cache)
        if missing:
            return False, (f"not downloaded yet into {(self.cache or cache_parent()) / CACHE_NAME}: "
                           + ", ".join(missing))
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "the app's tools folder"

    def version(self) -> Optional[str]:
        return self.blender_version

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        return "\n".join(subprocess.list2cmdline(build_command(str(self.exe), self.device_type, s, self.blender_version))
                         for s in self.scenes)

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"Blender Benchmark: {why}")
        folder = Path(ctx.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        total, metrics, records, commands, seconds = 0.0, {}, [], [], 0.0
        for scene in self.scenes:
            command = build_command(str(self.exe), self.device_type, scene, self.blender_version)
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
        (folder / "blender_run.json").write_text(json.dumps({"device_type": self.device_type, "scenes": records,
                                                             "commands": commands}, indent=2), encoding="utf-8")
        if records:
            metrics.update({k: v for k, v in records[0].items() if k in ("blender", "launcher", "devices")})
        return WorkloadResult(status="completed", score=round(total, 3), unit="samples/min", metrics=metrics,
                              command=commands[-1], exit_code=0, duration_seconds=round(seconds, 3),
                              stdout_path=str(folder / f"stdout_{self.scenes[-1]}.txt"),
                              stderr_path=str(folder / f"stderr_{self.scenes[-1]}.txt"))


def blender_workloads() -> list:
    return [BlenderBenchmarkWorkload("CPU"), BlenderBenchmarkWorkload("OPTIX")]

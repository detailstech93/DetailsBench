"""MLPerf Client 2.0.1 (MLCommons, Apache-2.0) as a Workload.

One run: `mlperf-windows.exe -c <config> -d <data folder> -p false -o <run folder>/mlperf_out
-x <run folder>/mlperf_results.csv`, started in the tool's folder (configs are relative to it). The first run
of a config downloads its model and files into the data folder (phi4mini, llama.cpp CUDA: a 2.33 GB model).

Score = overall geomean 2nd+ token generation rate (tok/s) from mlperf_out/results.json; time to first token
and per-category rates are metrics. Shape read from a real run on this computer (2026-10-18). "Benchmark
Success" false or an "Error Message" is a failed run, never a score. "Results Verified" is recorded as MLPerf
Client reports it. ai_run.json records the model, quantization, execution provider, device and tool version.

Scenarios (one test each, the LLM folders shipped with 2.0.1): Phi-4 mini (llm/phi4mini, the first test),
Llama 3.1 8B (llm/Llama3.1), Phi-4 reasoning 14B (llm/extended/phi4reason) and Qwen3 8B (llm/extended/qwen3), all
Q4_0. The config is picked by the graphics card's maker: NVIDIA llama.cpp CUDA, AMD Windows ML DirectML, Intel
OpenVINO GPU (a maker with no config for a scenario is named, never run on another device). NPU configs, the agentic
scenarios (they run Python code the model writes) and the experimental image generation are not offered. Only
Phi-4 mini on NVIDIA has been seen on a real run here: the other scenarios and makers are not marked verified.
"""

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

VERSION = "2.0.1"
DEFAULT_CONFIG = "llm/phi4mini/NVIDIA_llamacpp-CUDA_GPU.json"
QUANT = re.compile(r"-(Q\d\w*|INT\d+|FP\d+)$", re.IGNORECASE)
# test id: (title, scenario folder)
SCENARIOS = {
    "mlperf_client": ("Phi-4 mini", "llm/phi4mini"),
    "mlperf_client_llama31": ("Llama 3.1 8B", "llm/Llama3.1"),
    "mlperf_client_phi4reason": ("Phi-4 reasoning", "llm/extended/phi4reason"),
    "mlperf_client_qwen3": ("Qwen3 8B", "llm/extended/qwen3"),
}
# graphics card maker -> config file names to try, in order (as shipped in each scenario folder)
VENDOR_CONFIGS = (("nvidia", ("NVIDIA_llamacpp-CUDA_GPU.json",)),
                  ("amd", ("AMD_WindowsML-DML_GPU.json",)),
                  ("intel", ("Intel_NativeOpenVINO_GPU_Default.json", "Intel_WindowsML-OpenVINO_GPU.json")))
VENDOR_NAMES = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}
_GPUS: List[Optional[List[str]]] = []
WARNING = ("The first run of a config downloads its model (several GB) into the data folder. Do not use the GPU "
           "for anything else during the run (for example LM Studio or a local coding agent).")


def mlperf_path(env: Optional[Mapping[str, str]] = None, data_dir: Optional[Path] = None) -> Optional[Path]:
    """PCBM_MLPERF_CLIENT (the exe), else ~/Tools/MLPerfClient/mlperf-windows.exe, else the one Install put in the
    app's tools folder (data/installers.py)."""
    env = os.environ if env is None else env
    home = env.get("USERPROFILE") or env.get("HOME")
    for p in (env.get("PCBM_MLPERF_CLIENT"), home and str(Path(home) / "Tools" / "MLPerfClient" / "mlperf-windows.exe")):
        if p and Path(p).is_file():
            return Path(p)
    from app.install import direct
    from data.installers import INSTALLERS
    plan = INSTALLERS["mlperf_client"]["windows"]["zip"]
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def gpu_names() -> List[str]:
    """This PC's display adapters, read once (Windows)."""
    if not _GPUS:
        from app.system_info import get_gpu_names
        _GPUS.append(get_gpu_names())
    return list(_GPUS[0] or [])


def vendors(names: List[str]) -> List[str]:
    """Makers in the order VENDOR_CONFIGS tries them (an NVIDIA card before an integrated AMD or Intel one)."""
    text = " ".join(names).lower()
    marks = {"nvidia": ("nvidia", "geforce", "quadro"), "amd": ("amd", "radeon"), "intel": ("intel",)}
    return [v for v, _ in VENDOR_CONFIGS if any(m in text for m in marks[v])]


def pick_config(tool_dir: Path, folder: str, names: List[str]) -> Tuple[Optional[str], str]:
    """(config path relative to the tool, "") for the first maker with a config here, else (None, why)."""
    found = vendors(names)
    if not found:
        return None, "no NVIDIA, AMD or Intel graphics card found"
    for vendor in found:
        for name in dict(VENDOR_CONFIGS)[vendor]:
            if (tool_dir / folder / name).is_file():
                return f"{folder}/{name}", ""
    return None, (f"MLPerf Client has no GPU config for {', '.join(VENDOR_NAMES[v] for v in found)} "
                  f"in {folder}")


def build_command(exe: str, config: str, data_dir: str, out_dir: str, csv_path: str) -> List[str]:
    return [exe, "-c", config, "-d", data_dir, "-p", "false", "-o", out_dir, "-x", csv_path]


def _number(value) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    return float(value) if ok else None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def parse_results(text: str) -> Tuple[Optional[float], Dict[str, object], Dict[str, object], Optional[str]]:
    """(score tok/s, metrics, model record, error) from results.json (one object, or a list: the first)."""
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, {}, {}, f"not JSON: {e}"
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {}
    if not isinstance(data, dict):
        return None, {}, {}, "not a results object"
    ep = data.get("EP Configuration") if isinstance(data.get("EP Configuration"), dict) else {}
    model = str(data.get("Model Name") or "")
    quant = QUANT.search(model)
    record = {"tool": "MLPerf Client", "tool_version": str(data.get("BuildVer") or VERSION),
              "config": data.get("ConfigFile"), "scenario": data.get("Scenario Name"), "model": model or None,
              "quantization": quant.group(1) if quant else None,
              "backend": " ".join(str(x) for x in (data.get("Execution Provider Name"), ep.get("backend")) if x) or None,
              "device": ep.get("device_name"), "device_type": data.get("Device Type"),
              "iterations": data.get("Iterations"), "warmup": data.get("WarmUp"),
              "results_verified": data.get("Results Verified"), "tested_by_mlcommons":
                  data.get("Config") == "Configuration tested by MLCommons"}
    record = {k: v for k, v in record.items() if v is not None}
    if data.get("Benchmark Success") is not True or data.get("Error Message"):
        return None, {}, record, f"benchmark not successful: {data.get('Error Message') or 'Benchmark Success is not true'}"
    overall = data.get("overall_results") if isinstance(data.get("overall_results"), dict) else {}
    score = _number(overall.get("Geomean 2nd+ Token Generation Rate"))
    if score is None:
        return None, {}, record, "no token generation rate in the results"
    metrics: Dict[str, object] = {}
    ttft = _number(overall.get("Geomean Time to First Token"))
    if ttft is not None:
        metrics["ttft_s"] = (ttft, "s")
    for key, name in (("Avg Input Tokens", "input_tokens"), ("Avg Generated Tokens", "generated_tokens")):
        if _number(overall.get(key)) is not None:
            metrics[name] = (_number(overall.get(key)), "tokens")
    for category, values in (data.get("category_results") or {}).items():
        if not isinstance(values, dict):
            continue
        for key, short, unit in (("Avg 2nd+ Token Generation Rate", "tps", "tok/s"),
                                 ("Avg Time to First Token", "ttft_s", "s")):
            value = _number(values.get(key))
            if value is not None:
                metrics[f"{short}@{_slug(category)}"] = (value, unit)
    metrics.update({k: v for k, v in record.items() if isinstance(v, str)})
    metrics["results_verified"] = str(record.get("results_verified"))
    return round(score, 3), metrics, record, None


class MlperfClientWorkload(Workload):
    benchmark_type = "ai"
    timeout_seconds = 3600
    warning = WARNING

    def __init__(self, config: Optional[str] = None, exe: Optional[Path] = None, data_dir: Optional[Path] = None,
                 test_id: str = "mlperf_client", gpus: Optional[Callable[[], List[str]]] = None):
        self.id = test_id
        self.title, self.folder = SCENARIOS[test_id]
        self._config, self._gpus = config, gpus or gpu_names
        self.exe = exe if exe is not None else mlperf_path()
        self.data_dir = data_dir or (self.exe.parent / "data" if self.exe else None)
        self.display_name = f"MLPerf Client ({self.title})"
        if test_id == "mlperf_client_phi4reason":
            self.timeout_seconds = 2 * 3600      # a reasoning model writes long answers

    @property
    def config(self) -> Optional[str]:
        """The config given, else the one for this PC's graphics card (picked when first needed)."""
        if self._config is None and self.exe:
            self._config = pick_config(self.exe.parent, self.folder, self._gpus())[0] or ""
        return self._config or None

    @property
    def verified(self) -> bool:
        return self.config == DEFAULT_CONFIG      # Phi-4 mini on NVIDIA: a real run on this computer (2026-10-18)

    def availability(self) -> Tuple[bool, str]:
        if not self.exe:
            return False, "not found (~/Tools/MLPerfClient or PCBM_MLPERF_CLIENT)"
        if not self.config:
            return False, pick_config(self.exe.parent, self.folder, self._gpus())[1]
        if not (self.exe.parent / self.config).is_file():
            return False, f"config not found: {self.config}"
        return True, str(self.exe)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "~/Tools/MLPerfClient or PCBM_MLPERF_CLIENT"

    def version(self) -> Optional[str]:
        return VERSION

    def _command(self, folder: str) -> List[str]:
        return build_command(str(self.exe), self.config, str(self.data_dir), str(Path(folder) / "mlperf_out"),
                             str(Path(folder) / "mlperf_results.csv"))

    def describe_command(self) -> Optional[str]:
        return subprocess.list2cmdline(self._command("<run folder>")) if self.exe else None

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"MLPerf Client: {why}")
        folder = Path(ctx.output_dir)
        (folder / "mlperf_out").mkdir(parents=True, exist_ok=True)     # the tool refuses missing folders
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        command = self._command(str(folder))
        out, err = folder / "stdout.txt", folder / "stderr.txt"
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(self.exe.parent))
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3),
                  "stdout_path": str(out), "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        if outcome.exit_code != 0:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}", **common)
        try:
            text = (folder / "mlperf_out" / "results.json").read_text(encoding="utf-8")
        except OSError as e:
            return WorkloadResult(status="invalid", error=f"no results.json: {e}", **common)
        score, metrics, record, error = parse_results(text)
        if record:
            (folder / "ai_run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        if error:
            status = "failed" if error.startswith("benchmark not successful") else "invalid"
            return WorkloadResult(status=status, error=error, **common)
        parts = [("Model", record.get("model")), ("Backend", record.get("backend")), ("Device", record.get("device")),
                 ("Config", self.config)]
        note = "; ".join(f"{k}: {v}" for k, v in parts if v)
        return WorkloadResult(status="completed", score=score, unit="tok/s", metrics=metrics, note=note, **common)


def mlperf_workloads() -> list:
    return [MlperfClientWorkload(test_id=t) for t in SCENARIOS]

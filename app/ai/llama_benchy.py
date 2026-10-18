"""llama-benchy 0.4.0 (MIT) against LM Studio's OpenAI-compatible server, as a Workload.

One run: `llama-benchy --base-url <server>/v1 --model <id> --pp N --tg N --depth D... --runs R
--format json --save-result <run folder>/llama_benchy.json`. Score = token generation (tok/s) of the first
measured depth; prompt processing (tok/s) and time to first response (ms) are metrics per depth.
JSON shape read from a real 0.4.0 run on this computer (2026-10-18).

Recorded with every run (ai_run.json in the run folder): LM Studio's model record (model, quantization,
arch, format, backend, context), the llama-benchy version and the tokenizer it used. When the model's
Hugging Face tokenizer cannot be loaded, llama-benchy falls back to the gpt2 tokenizer: token counts
are then an approximation, and that is recorded, never hidden.

Do not run while LM Studio is used for something else on this computer (for example a local coding
agent): the numbers would measure both. Status mapping is the same as CliWorkload.
"""

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.ai import lmstudio
from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

VERSION = "0.4.0"
RESULT_FILE = "llama_benchy.json"
FALLBACK = "Falling back to 'gpt2' tokenizer"
WARNING = ("Do not use LM Studio for anything else during this run (for example a local coding agent on this "
           "computer): the numbers would measure both.")
METRICS = (("pp_throughput", "pp", "tok/s"), ("tg_throughput", "tg", "tok/s"),
           ("ttfr", "ttfr", "ms"), ("e2e_ttft", "e2e_ttft", "ms"))


def benchy_path(python: str = sys.executable) -> Optional[Path]:
    """llama-benchy's script next to the app's Python (installed into .venv with pip)."""
    folder = Path(python).parent
    for name in ("llama-benchy.exe", "llama-benchy"):
        if (folder / name).is_file():
            return folder / name
    return None


def build_command(exe: str, base_url: str, model: str, pp: int, tg: int, depths: Sequence[int],
                  runs: int, result_path: str) -> List[str]:
    return [exe, "--base-url", base_url.rstrip("/") + "/v1", "--model", model,
            "--pp", str(pp), "--tg", str(tg), "--depth", *[str(d) for d in depths],
            "--runs", str(runs), "--format", "json", "--save-result", result_path]


def _mean(entry) -> Optional[float]:
    value = entry.get("mean") if isinstance(entry, dict) else None
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    return float(value) if ok else None


def parse_result(text: str) -> Tuple[Optional[float], Dict[str, object], Optional[str]]:
    """(score tok/s, metrics, error). Metrics are named like tg_tok_s@d0; a missing value is left out."""
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, {}, f"not JSON: {e}"
    benches = [b for b in data.get("benchmarks") or [] if isinstance(b, dict)
               and not b.get("is_context_prefill_phase")] if isinstance(data, dict) else []
    metrics: Dict[str, object] = {}
    score = None
    for b in benches:
        depth = b.get("context_size", 0)
        for key, short, unit in METRICS:
            value = _mean(b.get(key))
            if value is not None:
                metrics[f"{short}_{unit.replace('/', '_')}@d{depth}"] = (round(value, 3), unit)
        if score is None:
            score = _mean(b.get("tg_throughput"))
    if score is None:
        return None, metrics, "no token generation throughput in the result"
    latency = _mean({"mean": data.get("latency_ms")})
    if latency is not None:
        metrics["api_latency_ms"] = (round(latency, 3), "ms")
    metrics["llama_benchy_version"] = str(data.get("version", ""))
    return round(score, 3), metrics, None


def tokenizer_used(log_text: str, model: str) -> str:
    return "gpt2 (fallback; token counts are approximate)" if FALLBACK in log_text else model


class LlamaBenchyWorkload(Workload):
    id = "llama_benchy"
    display_name = "llama-benchy (LM Studio)"
    benchmark_type = "ai"
    timeout_seconds = 1800
    verified = True                   # checked with a real run on this computer (2026-10-18)
    warning = WARNING

    def __init__(self, model: Optional[str], base_url: str = lmstudio.DEFAULT_URL, pp: int = 512, tg: int = 128,
                 depths: Sequence[int] = (0,), runs: int = 1, exe: Optional[Path] = None,
                 read_models: Callable = lmstudio.read_models,
                 read_runtimes: Callable[[], Dict[str, str]] = lambda: lmstudio.read_runtimes(lmstudio.lms_path())):
        self.model, self.base_url, self.pp, self.tg = model, base_url, pp, tg
        self.depths, self.runs = list(depths), runs
        self.exe = exe if exe is not None else benchy_path()
        self._read_models, self._read_runtimes = read_models, read_runtimes

    def availability(self) -> Tuple[bool, str]:
        return (True, str(self.exe)) if self.exe else (False, "llama-benchy not installed in the app's Python")

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.exe) if self.exe else None), "app Python scripts folder"

    def version(self) -> Optional[str]:
        return VERSION

    def describe_command(self) -> Optional[str]:
        if not self.exe:
            return None
        return subprocess.list2cmdline(self._command("<run folder>/" + RESULT_FILE))

    def _command(self, result_path: str) -> List[str]:
        return build_command(str(self.exe), self.base_url, self.model or "<model>", self.pp, self.tg, self.depths,
                             self.runs, result_path)

    def _model_record(self) -> Dict[str, object]:
        try:
            models = self._read_models(self.base_url)
        except (OSError, ValueError):
            return {"server": "LM Studio", "model": self.model}
        found = next((m for m in models if m.get("id") == self.model), {"id": self.model})
        return lmstudio.run_record(found, self._read_runtimes())

    def run(self, ctx: RunContext) -> WorkloadResult:
        if not self.exe:
            return WorkloadResult(status="skipped", error="llama-benchy not installed in the app's Python")
        if not self.model:
            return WorkloadResult(status="skipped", error="no model chosen")
        folder = Path(ctx.output_dir)
        result_path = folder / RESULT_FILE
        command = self._command(str(result_path))
        record = self._model_record()     # before the run: a JIT model may only be loaded by it
        out, err = folder / "stdout.txt", folder / "stderr.txt"
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(folder))
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
        log = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (out, err))
        if "context_length" not in record:           # loaded by the run itself: read it again
            after = self._model_record()
            record = after if "context_length" in after else record
        ai_run = {**record, "tool": "llama-benchy", "tool_version": VERSION,
                  "tokenizer": tokenizer_used(log, self.model), "pp": self.pp, "tg": self.tg,
                  "depths": self.depths, "runs": self.runs}
        (folder / "ai_run.json").write_text(json.dumps(ai_run, indent=2), encoding="utf-8")
        try:
            text = result_path.read_text(encoding="utf-8")
        except OSError as e:
            return WorkloadResult(status="invalid", error=f"no result file: {e}", **common)
        score, metrics, error = parse_result(text)
        if error:
            return WorkloadResult(status="invalid", error=f"output not usable: {error}", metrics=metrics, **common)
        metrics.update({k: v for k, v in ai_run.items() if isinstance(v, str)})
        return WorkloadResult(status="completed", score=score, unit="tok/s", metrics=metrics, **common)


def load_workloads():
    """Automated runs: the tool specs in data/tools, llama-benchy (its model is chosen before each plan) and
    MLPerf Client (phi4mini, llama.cpp CUDA)."""
    from app.ai.mlperf_client import MlperfClientWorkload
    from app.automation.cli_workload import load_workloads as load_cli
    workloads, errors = load_cli()
    return workloads + [LlamaBenchyWorkload(None), MlperfClientWorkload()], errors

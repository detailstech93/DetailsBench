"""llama-benchy 0.4.0 (MIT) against LM Studio's OpenAI-compatible server, as a Workload.

One run: `llama-benchy --base-url <server>/v1 --model <id> --pp N --tg N --depth D... --concurrency C...
[--enable-prefix-caching] --runs R --format json --save-result <run folder>/llama_benchy.json`.
JSON shape read from real 0.4.0 runs on this computer (2026-10-18), including several depths and
concurrency levels with prefix caching.

The result has one row per concurrency level and context depth: prompt processing (pp, tok/s), token
generation (tg, tok/s; total and per request), time to first response (TTFR, ms) and end-to-end time to
first token (E2E TTFT, ms). With prefix caching, each depth above 0 also has a "context prefill" row: the
first request that loads the context, measured separately from the follow-up that reuses the cache.
Score = tg (total tok/s) of the lowest concurrency at the lowest depth, measured phase. Every row is a
metric (pp_tok_s@d2048_c2, ctx_pp_tok_s@d2048_c1, ...) and llama_benchy_report.md is written to the
run folder.

Recorded with every run (ai_run.json in the run folder): LM Studio's model record (model, quantization,
arch, format, backend, context), the llama-benchy version, the tokenizer it used, depths, concurrency
levels, prefix caching and latency mode. When the model's Hugging Face tokenizer cannot be loaded,
llama-benchy falls back to the gpt2 tokenizer: token counts are then an approximation, and that is
recorded, never hidden.

Do not run while LM Studio is used for something else on this computer (for example a local coding
agent): the numbers would measure both. Status mapping is the same as CliWorkload.
"""

import json
import os
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.ai import lmstudio
from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

VERSION = "0.4.0"
RESULT_FILE = "llama_benchy.json"
REPORT_FILE = "llama_benchy_report.md"
FALLBACK = "Falling back to 'gpt2' tokenizer"
WARNING = ("Do not use LM Studio for anything else during this run (for example a local coding agent on this "
           "computer): the numbers would measure both.")
# (JSON key, metric name, unit, report column)
COLUMNS = (("pp_throughput", "pp_tok_s", "tok/s", "pp (tok/s)"),
           ("tg_throughput", "tg_tok_s", "tok/s", "tg total (tok/s)"),
           ("tg_req_throughput", "tg_req_tok_s", "tok/s", "tg per request (tok/s)"),
           ("peak_throughput", "peak_tok_s", "tok/s", "tg peak (tok/s)"),
           ("ttfr", "ttfr_ms", "ms", "TTFR (ms)"),
           ("e2e_ttft", "e2e_ttft_ms", "ms", "E2E TTFT (ms)"))


def benchy_path(python: str = sys.executable, env=None, frozen: Optional[bool] = None) -> Optional[Path]:
    """llama-benchy's script: PCBM_LLAMA_BENCHY, else next to the app's Python (installed into .venv with pip).
    The packaged app has no Python folder, so there only PCBM_LLAMA_BENCHY is used."""
    env = os.environ if env is None else env
    override = env.get("PCBM_LLAMA_BENCHY")
    if override and Path(override).is_file():
        return Path(override)
    if getattr(sys, "frozen", False) if frozen is None else frozen:
        return None
    folder = Path(python).parent
    for name in ("llama-benchy.exe", "llama-benchy"):
        if (folder / name).is_file():
            return folder / name
    return None


def build_command(exe: str, base_url: str, model: str, pp: int, tg: int, depths: Sequence[int],
                  runs: int, result_path: str, concurrency: Sequence[int] = (1,),
                  prefix_caching: bool = False) -> List[str]:
    return [exe, "--base-url", base_url.rstrip("/") + "/v1", "--model", model,
            "--pp", str(pp), "--tg", str(tg), "--depth", *[str(d) for d in depths],
            "--concurrency", *[str(c) for c in concurrency],
            *(["--enable-prefix-caching"] if prefix_caching else []),
            "--runs", str(runs), "--format", "json", "--save-result", result_path]


def _mean(entry) -> Optional[float]:
    value = entry.get("mean") if isinstance(entry, dict) else entry
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    return float(value) if ok else None


def result_rows(data: Mapping) -> List[Dict[str, object]]:
    """One row per benchmark entry: concurrency, depth, phase and the COLUMNS means (missing ones left out)."""
    rows = []
    for b in data.get("benchmarks") or []:
        if not isinstance(b, dict):
            continue
        row = {"concurrency": b.get("concurrency", 1), "depth": b.get("context_size", 0),
               "phase": "context prefill" if b.get("is_context_prefill_phase") else "measured"}
        for key, name, _unit, _col in COLUMNS:
            value = _mean(b.get(key))
            if value is not None:
                row[name] = round(value, 3)
        rows.append(row)
    return rows


def parse_result(text: str) -> Tuple[Optional[float], Dict[str, object], Optional[str]]:
    """(score tok/s, metrics, error). A missing value is left out, never 0."""
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, {}, f"not JSON: {e}"
    if not isinstance(data, dict):
        return None, {}, "not a result object"
    rows = result_rows(data)
    metrics: Dict[str, object] = {}
    for row in rows:
        prefix = "ctx_" if row["phase"] == "context prefill" else ""
        for _key, name, unit, _col in COLUMNS:
            if name in row:
                metrics[f"{prefix}{name}@d{row['depth']}_c{row['concurrency']}"] = (row[name], unit)
    measured = sorted((r for r in rows if r["phase"] == "measured" and "tg_tok_s" in r),
                      key=lambda r: (r["concurrency"], r["depth"]))
    if not measured:
        return None, metrics, "no token generation throughput in the result"
    latency = _mean(data.get("latency_ms"))
    if latency is not None:
        metrics["api_latency_ms"] = (round(latency, 3), "ms")
    metrics["llama_benchy_version"] = str(data.get("version", ""))
    metrics["prefix_caching"] = str(bool(data.get("prefix_caching_enabled")))
    metrics["latency_mode"] = str(data.get("latency_mode", ""))
    return measured[0]["tg_tok_s"], metrics, None


def report_markdown(data: Mapping, ai_run: Mapping) -> str:
    """The run as a readable report: settings, then one table row per concurrency level and depth."""
    def fmt(v):
        return "-" if v is None else (f"{v:,.1f}" if isinstance(v, float) else str(v))
    lines = [f"# llama-benchy report: {ai_run.get('model', data.get('model', '?'))}", ""]
    for label, key in (("Server", "server"), ("Quantization", "quantization"), ("Architecture", "arch"),
                       ("Backend", "backend"), ("Loaded context", "context_length"), ("Tokenizer", "tokenizer"),
                       ("llama-benchy", "tool_version")):
        if ai_run.get(key) is not None:
            lines.append(f"- {label}: {ai_run[key]}")
    lines += [f"- Prompt / generated tokens: {ai_run.get('pp')} / {ai_run.get('tg')}",
              f"- Context depths: {', '.join(str(d) for d in ai_run.get('depths', []))}",
              f"- Concurrency levels: {', '.join(str(c) for c in ai_run.get('concurrency', []))}",
              f"- Prefix caching: {'on' if data.get('prefix_caching_enabled') else 'off'}",
              f"- Runs per test: {ai_run.get('runs')}",
              f"- Latency mode: {data.get('latency_mode', '-')}, API latency: {fmt(_mean(data.get('latency_ms')))} ms",
              "", "| Concurrency | Depth | Phase | " + " | ".join(c[3] for c in COLUMNS) + " |",
              "|" + "---|" * (3 + len(COLUMNS))]
    for row in sorted(result_rows(data), key=lambda r: (r["concurrency"], r["depth"], r["phase"] == "measured")):
        lines.append(f"| {row['concurrency']} | {row['depth']} | {row['phase']} | "
                     + " | ".join(fmt(row.get(c[1])) for c in COLUMNS) + " |")
    lines += ["", "pp = prompt processing, tg = token generation, TTFR = time to first response, "
              "E2E TTFT = end-to-end time to first token. A context prefill row is the request that loads the "
              "context (prefix caching on); the measured row after it reuses the cache. '-' = not reported."]
    if str(ai_run.get("tokenizer", "")).startswith("gpt2"):
        lines.append("Token counts use the gpt2 tokenizer (the model's own was not available): approximate.")
    return "\n".join(lines) + "\n"


def tokenizer_used(log_text: str, model: str) -> str:
    return "gpt2 (fallback; token counts are approximate)" if FALLBACK in log_text else model


NOT_ENOUGH_MEMORY = "not enough memory"
TERMINATED = re.compile(r'"error"\s*:\s*"terminated"')
SERVER_ERROR = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')
NEEDS_GB = re.compile(r"requires approximately ([0-9.]+ ?[GM]B)")


def server_error(log: str, model: str) -> Optional[str]:
    """Why LM Studio answered with an error (llama-benchy prints its JSON), or None. Radxa Dragon Q8B, 2026-10-22: the
    27B model needs about 19.24 GB on a 15 GB board; LM Studio refused to load it and the report said "exit code 1"."""
    if TERMINATED.search(log or ""):       # Windows ARM board, 2026-10-23: the engine died at "Prompt processing 0%"
        from app.automation.runner import CRASH_ERRORS
        return (f"{CRASH_ERRORS[0]} while running {model} (its server answered \"terminated\"); LM Studio's engine "
                "does not work with this model on this computer, so it is not run again")
    found = SERVER_ERROR.search(log or "")
    if not found:
        return None
    message = found.group(1).replace('\\"', '"')
    if "insufficient system resources" in message:
        needs = NEEDS_GB.search(message)
        return (f"{NOT_ENOUGH_MEMORY}: LM Studio would not load {model}"
                + (f" (it needs about {needs.group(1)})" if needs else "")
                + " so the computer does not freeze; choose a smaller model")
    return "LM Studio: " + message[:300]


def parse_ints(text: str, minimum: int) -> Optional[List[int]]:
    """'0 4096, 8192' -> [0, 4096, 8192] (sorted, no duplicates); None when any value is not a whole number."""
    try:
        values = sorted({int(v) for v in text.replace(",", " ").split()})
    except ValueError:
        return None
    return values if values and values[0] >= minimum else None


def unload_model(model: Optional[str]) -> None:
    """`lms unload <model>`, or `lms unload --all` for None (LM Studio's own command); nothing happens when lms is
    missing or it fails."""
    lms = lmstudio.lms_path()
    if lms is None:
        return
    try:
        subprocess.run([str(lms), "unload"] + ([model] if model else ["--all"]), capture_output=True, timeout=60,
                       stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def unload_all_models() -> None:
    unload_model(None)


class LlamaBenchyWorkload(Workload):
    id = "llama_benchy"
    display_name = "llama-benchy (LM Studio)"
    benchmark_type = "ai"
    timeout_seconds = 1800
    verified = True                   # checked with real runs on this computer (2026-10-18)
    warning = WARNING
    unload_jit = True                 # a model LM Studio loaded for this run is unloaded after it
    unload_all_first = True           # `lms unload --all` before the run

    def __init__(self, model: Optional[str], base_url: str = lmstudio.DEFAULT_URL, pp: int = 512, tg: int = 128,
                 depths: Sequence[int] = (0,), runs: int = 1, exe: Optional[Path] = None,
                 read_models: Callable = lmstudio.read_models,
                 read_runtimes: Callable[[], Dict[str, str]] = lambda: lmstudio.read_runtimes(lmstudio.lms_path()),
                 concurrency: Sequence[int] = (1,), prefix_caching: bool = False, unload: Callable = None,
                 unload_all: Callable = None, start_server: Callable[[], Optional[str]] = None):
        self.model, self.base_url, self.pp, self.tg = model, base_url, pp, tg
        self.depths, self.runs = list(depths), runs
        self.concurrency, self.prefix_caching = list(concurrency), prefix_caching
        self.exe = exe if exe is not None else benchy_path()
        self._read_models, self._read_runtimes = read_models, read_runtimes
        self._unload = unload or unload_model
        self._unload_all = unload_all or unload_all_models
        self._start_server = start_server or lmstudio.start_server

    def configure(self, settings: Mapping):
        """Settings from the panel's dialog: model, pp, tg, depths, concurrency, prefix_caching."""
        for key in ("model", "pp", "tg", "depths", "concurrency", "prefix_caching"):
            if key in settings:
                setattr(self, key, settings[key])

    def availability(self) -> Tuple[bool, str]:
        return (True, str(self.exe)) if self.exe else (False, "llama-benchy not installed in the app's Python (or set PCBM_LLAMA_BENCHY)")

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
                             self.runs, result_path, self.concurrency, self.prefix_caching)

    def _ensure_server(self) -> Optional[str]:
        """None when LM Studio's server answers (started here if needed, like the fixed models'), else why not. The
        settings window starts it too, but a resumed or command-line run has no window (Fedora, 2026-10-23: "Cannot
        connect to host localhost:1234")."""
        try:
            self._read_models(self.base_url)
            return None
        except (OSError, ValueError):
            pass
        why = self._start_server()
        try:
            self._read_models(self.base_url)
            return None
        except (OSError, ValueError):
            return f"LM Studio's server does not answer on {self.base_url} (lms server start: {why or 'no answer'})"

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
        # every loaded model is unloaded first, on every system: nothing else shares the memory with the measured
        # model (owner, 2026-10-23; Fedora: Qwen3.5 4B stayed loaded, Gemma 4 12B came on top, out of memory)
        problem = self._ensure_server()
        if problem:
            return WorkloadResult(status="failed", error=problem)
        if self.unload_all_first:
            self._unload_all()
        record = self._model_record()     # before the run: a JIT model may only be loaded by it
        out, err = folder / "stdout.txt", folder / "stderr.txt"
        try:
            outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(folder))
        finally:
            if self.unload_jit:           # LM Studio loaded it for this run (JIT): unloaded again after it
                self._unload(self.model)
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
            log = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (out, err) if p.is_file())
            why = server_error(log, self.model)
            return WorkloadResult(status="failed", error=why or f"exit code {outcome.exit_code}", **common)
        log = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (out, err))
        if "context_length" not in record:           # loaded by the run itself: read it again
            after = self._model_record()
            record = after if "context_length" in after else record
        ai_run = {**record, "tool": "llama-benchy", "tool_version": VERSION,
                  "tokenizer": tokenizer_used(log, getattr(self, "tokenizer", None) or self.model), "pp": self.pp, "tg": self.tg,
                  "depths": self.depths, "concurrency": self.concurrency, "prefix_caching": self.prefix_caching,
                  "runs": self.runs}
        (folder / "ai_run.json").write_text(json.dumps(ai_run, indent=2), encoding="utf-8")
        try:
            text = result_path.read_text(encoding="utf-8")
        except OSError as e:
            return WorkloadResult(status="invalid", error=f"no result file: {e}", **common)
        score, metrics, error = parse_result(text)
        if error:
            return WorkloadResult(status="invalid", error=f"output not usable: {error}", metrics=metrics, **common)
        (folder / REPORT_FILE).write_text(report_markdown(json.loads(text), ai_run), encoding="utf-8")
        metrics.update({k: v for k, v in ai_run.items() if isinstance(v, str)})
        parts = (("Model", ai_run.get("model")), ("Quantization", ai_run.get("quantization")),
                 ("Backend", ai_run.get("backend")), ("Context", ai_run.get("context_length")),
                 ("pp/tg", f"{self.pp}/{self.tg}"))
        note = "; ".join(f"{k}: {v}" for k, v in parts if v)       # the report's tile names what was measured
        return WorkloadResult(status="completed", score=score, unit="tok/s", metrics=metrics, note=note, **common)


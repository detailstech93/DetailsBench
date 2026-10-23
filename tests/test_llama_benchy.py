import json
import sys
import threading
from pathlib import Path

from app.ai import llama_benchy
from app.automation.workload import RunContext

M = lambda v: {"mean": v, "std": 0.0, "values": [v]}
RESULT = {"version": "0.4.0", "latency_mode": "api", "latency_ms": 7.06, "model": "google/gemma-4-12b-qat",
          "benchmarks": [
              {"context_size": 4096, "is_context_prefill_phase": True, "pp_throughput": M(999.0)},
              {"context_size": 0, "prompt_size": 512, "response_size": 32, "is_context_prefill_phase": False,
               "pp_throughput": M(339.153), "tg_throughput": M(14.0984), "ttfr": M(1395.8), "e2e_ttft": M(1395.8)},
              {"context_size": 4096, "is_context_prefill_phase": False,
               "pp_throughput": M(300.0), "tg_throughput": M(12.5), "ttfr": M(0.0)}]}
MODELS = [{"id": "google/gemma-4-12b-qat", "compatibility_type": "gguf", "quantization": "Q4_0",
           "loaded_context_length": 4096}]


def test_build_command():
    assert llama_benchy.build_command("b.exe", "http://localhost:1234/", "m", 512, 32, [0, 4096], 1, "r.json") == [
        "b.exe", "--base-url", "http://localhost:1234/v1", "--model", "m", "--pp", "512", "--tg", "32",
        "--depth", "0", "4096", "--concurrency", "1", "--runs", "1", "--format", "json", "--save-result", "r.json"]
    cmd = llama_benchy.build_command("b", "u", "m", 1, 1, [0], 1, "r", concurrency=[1, 2], prefix_caching=True)
    assert cmd[cmd.index("--concurrency"):cmd.index("--runs")] == ["--concurrency", "1", "2", "--enable-prefix-caching"]


# shape of a real 0.4.0 run with depths 0 2048, concurrency 1 2 and prefix caching (2026-10-18), values trimmed
MATRIX = {"version": "0.4.0", "latency_mode": "api", "latency_ms": 3.545, "prefix_caching_enabled": True,
          "max_concurrency": 2, "benchmarks": [
              {"concurrency": 1, "context_size": 0, "is_context_prefill_phase": False, "pp_throughput": M(1276.6),
               "tg_throughput": M(93.1), "tg_req_throughput": M(93.1), "ttfr": M(188.4), "e2e_ttft": M(188.4)},
              {"concurrency": 2, "context_size": 0, "is_context_prefill_phase": False, "pp_throughput": M(1142.8),
               "tg_throughput": M(169.0), "tg_req_throughput": M(84.7), "ttfr": M(419.0), "e2e_ttft": M(419.0)},
              {"concurrency": 1, "context_size": 2048, "is_context_prefill_phase": True, "pp_throughput": M(1891.5),
               "tg_throughput": M(78.6), "ttfr": M(943.5)},
              {"concurrency": 1, "context_size": 2048, "is_context_prefill_phase": False, "pp_throughput": M(922.4),
               "tg_throughput": M(86.7), "ttfr": M(281.1)}]}


def test_parse_result_scores_first_measured_depth_and_skips_missing_values():
    score, metrics, error = llama_benchy.parse_result(json.dumps(RESULT))
    assert error is None and score == 14.098
    assert metrics["pp_tok_s@d0_c1"] == (339.153, "tok/s") and metrics["tg_tok_s@d4096_c1"] == (12.5, "tok/s")
    assert "ttfr_ms@d4096_c1" not in metrics            # 0 is not a reading
    assert metrics["ctx_pp_tok_s@d4096_c1"] == (999.0, "tok/s")
    assert metrics["api_latency_ms"] == (7.06, "ms") and metrics["llama_benchy_version"] == "0.4.0"
    assert llama_benchy.parse_result('{"benchmarks": []}')[0] is None
    assert llama_benchy.parse_result("oops")[2].startswith("not JSON")


def test_concurrency_levels_and_prefix_caching_are_kept_apart():
    score, metrics, error = llama_benchy.parse_result(json.dumps(MATRIX))
    assert error is None and score == 93.1                         # concurrency 1, depth 0
    assert metrics["tg_tok_s@d0_c2"] == (169.0, "tok/s") and metrics["tg_req_tok_s@d0_c2"] == (84.7, "tok/s")
    assert metrics["ctx_ttfr_ms@d2048_c1"] == (943.5, "ms") and metrics["ttfr_ms@d2048_c1"] == (281.1, "ms")
    assert metrics["prefix_caching"] == "True"
    report = llama_benchy.report_markdown(MATRIX, {"model": "m", "pp": 256, "tg": 32, "depths": [0, 2048],
                                                   "concurrency": [1, 2], "runs": 1, "tokenizer": "gpt2 (fallback)"})
    assert "- Prefix caching: on" in report and "- Concurrency levels: 1, 2" in report
    assert "| 1 | 2048 | context prefill | 1,891.5 | 78.6 | - |" in report
    assert "| 2 | 0 | measured | 1,142.8 | 169.0 | 84.7 |" in report and "gpt2 tokenizer" in report
    assert report.index("context prefill") < report.index("| 1 | 2048 | measured")


def test_parse_ints():
    assert llama_benchy.parse_ints("4096, 0 4096", 0) == [0, 4096]
    assert llama_benchy.parse_ints("0", 1) is None and llama_benchy.parse_ints("a", 0) is None
    assert llama_benchy.parse_ints("", 0) is None


def test_tokenizer_fallback_is_recorded():
    assert llama_benchy.tokenizer_used("Falling back to 'gpt2' tokenizer as approximation.", "m").startswith("gpt2")
    assert llama_benchy.tokenizer_used("", "m") == "m"


def _fake(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_benchy.py"
    script.write_text("import sys, json\nargs = sys.argv[1:]\nout = args[args.index('--save-result') + 1]\n"
                      + body, encoding="utf-8")
    return script


class PyBenchy(llama_benchy.LlamaBenchyWorkload):
    def __init__(self, script, **kw):
        super().__init__("google/gemma-4-12b-qat", exe=Path(sys.executable), read_models=lambda url: MODELS,
                         read_runtimes=lambda: {"GGUF": "llama.cpp-cuda@2.60.0"}, **kw)
        self.script = script

    def _command(self, result_path):
        return [sys.executable, str(self.script)] + super()._command(result_path)[1:]


def test_workload_completed_records_model_and_tokenizer(tmp_path):
    script = _fake(tmp_path, f"open(out, 'w').write({json.dumps(json.dumps(RESULT))})\n"
                             "print(\"Falling back to 'gpt2' tokenizer as approximation.\", file=sys.stderr)\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = PyBenchy(script).run(RunContext(output_dir=run_dir, timeout_seconds=60))
    assert result.status == "completed" and result.score == 14.098 and result.unit == "tok/s"
    ai_run = json.loads((run_dir / "ai_run.json").read_text(encoding="utf-8"))
    assert ai_run["quantization"] == "Q4_0" and ai_run["backend"] == "llama.cpp-cuda@2.60.0"
    assert ai_run["context_length"] == 4096 and ai_run["tokenizer"].startswith("gpt2")
    assert ai_run["concurrency"] == [1] and ai_run["prefix_caching"] is False
    assert "| 1 | 0 | measured | 339.2 | 14.1 |" in (run_dir / llama_benchy.REPORT_FILE).read_text(encoding="utf-8")
    assert result.metrics["tokenizer"].startswith("gpt2") and result.metrics["quantization"] == "Q4_0"


def test_workload_failures_never_score(tmp_path):
    for body, status in (("sys.exit(2)\n", "failed"), ("pass\n", "invalid"),
                         ("open(out, 'w').write('{}')\n", "invalid")):
        run_dir = tmp_path / status / str(len(body))
        run_dir.mkdir(parents=True)
        result = PyBenchy(_fake(tmp_path, body)).run(RunContext(output_dir=run_dir, timeout_seconds=60))
        assert result.status == status and result.score is None
    stop = threading.Event()
    stop.set()
    run_dir = tmp_path / "stopped"
    run_dir.mkdir()
    slow = _fake(tmp_path, "import time; time.sleep(30)\n")
    assert PyBenchy(slow).run(RunContext(output_dir=run_dir, stop_event=stop)).status == "cancelled"
    missing = llama_benchy.LlamaBenchyWorkload("m", exe=None)
    missing.exe = None
    assert missing.run(RunContext(output_dir=tmp_path)).status == "skipped"
    assert missing.availability()[0] is False


def test_benchy_path_override_and_packaged_app(tmp_path):
    from app.ai.llama_benchy import benchy_path
    exe = tmp_path / "llama-benchy.exe"
    exe.write_bytes(b"")
    assert benchy_path("C:/nowhere/python.exe", env={"PCBM_LLAMA_BENCHY": str(exe)}) == exe
    assert benchy_path(str(tmp_path / "python.exe"), env={}, frozen=False) == exe
    assert benchy_path(str(tmp_path / "python.exe"), env={}, frozen=True) is None    # packaged: no Python folder


def test_lm_studio_refusing_the_model_says_why(tmp_path):
    # Radxa Dragon Q8B, 2026-10-22: the report said only "exit code 1"
    log = ('Warming up...\nWarmup failed: HTTP 400: {\n    "error": {\n        "message": "Failed to load model '
           '\\"qwen/qwen3.8-27b\\". Error: Model loading was stopped due to insufficient system resources. Under the '
           'current settings, this model requires approximately 19.24 GB of memory, and continuing to load it would '
           'likely overload your system and cause it to freeze.",\n        "type": "invalid_request_error"\n    }\n}\n')
    why = llama_benchy.server_error(log, "qwen/qwen3.8-27b")
    assert why == ("not enough memory: LM Studio would not load qwen/qwen3.8-27b (it needs about 19.24 GB) so the "
                   "computer does not freeze; choose a smaller model")
    assert llama_benchy.server_error('{"error": {"message": "Model \\"x\\" not found"}}', "x") == \
        'LM Studio: Model "x" not found'
    assert llama_benchy.server_error("Traceback ...", "x") is None
    run_dir = tmp_path / "refused"
    run_dir.mkdir()
    body = f"print({log!r})\nsys.exit(1)\n"
    result = PyBenchy(_fake(tmp_path, body)).run(RunContext(output_dir=run_dir, timeout_seconds=60))
    assert result.status == "failed" and result.error.startswith("not enough memory") and result.score is None


def test_a_model_lm_studio_loaded_for_the_run_is_unloaded_after_it(tmp_path):
    """Fedora, 2026-10-23: the free test's model stayed loaded and the next AI test ran the computer out of memory."""
    script = _fake(tmp_path, f"open(out, 'w').write({json.dumps(json.dumps(RESULT))})\n")
    unloaded = []
    for state, expected in (("not-loaded", ["google/gemma-4-12b-qat"]), ("loaded", [])):
        unloaded.clear()
        models = [dict(m, state=state) for m in MODELS]
        w = PyBenchy(script, unload=unloaded.append)
        w._read_models = lambda url: models
        run_dir = tmp_path / state
        run_dir.mkdir()
        assert w.run(RunContext(output_dir=run_dir, timeout_seconds=60)).status == "completed"
        assert unloaded == expected         # one the owner loaded before stays


def test_an_engine_that_dies_is_a_crash_and_not_run_again():
    """Windows ARM board, 2026-10-23: LM Studio's engine died at the first prompt every time."""
    from app.automation.runner import crashed, should_retry
    from app.automation.workload import WorkloadResult
    why = llama_benchy.server_error('Warmup failed: HTTP 400: {"error":"terminated"}', "qwen/qwen3.5-4b")
    assert why.startswith("LM Studio's engine crashed while running qwen/qwen3.5-4b")
    r = WorkloadResult(status="failed", error=why, exit_code=1)
    assert crashed(r) and not should_retry(r)
    assert not crashed(WorkloadResult(status="failed", error="exit code 1", exit_code=1))

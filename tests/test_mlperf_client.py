import json
import sys
from pathlib import Path

from app.ai import mlperf_client
from app.automation.workload import RunContext

# trimmed from a real MLPerf Client 2.0.1 results.json (phi4mini, llama.cpp CUDA, 2026-10-18)
REAL = {"BuildVer": "2.0.1.f0dbab3 MLCommons Standard Build", "Config": "Configuration tested by MLCommons",
        "ConfigFile": "NVIDIA_llamacpp-CUDA_GPU.json", "Scenario Name": "phi4mini",
        "Model Name": "Phi-4-mini-instruct-Q4_0", "Execution Provider Name": "llama-cpp",
        "EP Configuration": {"backend": "CUDA", "device_id": 0, "device_name": "NVIDIA GeForce RTX 5090 Laptop GPU",
                             "device_type": "GPU"},
        "Iterations": 3, "WarmUp": 1, "Benchmark Success": True, "Results Verified": False, "Error Message": "",
        "Device Type": "GPU",
        "overall_results": {"Avg Generated Tokens": 195, "Avg Input Tokens": 1292,
                            "Geomean 2nd+ Token Generation Rate": 219.876, "Geomean Time to First Token": 0.069},
        "category_results": {"Summarization, Intermediate": {"Avg 2nd+ Token Generation Rate": 196.876,
                                                             "Avg Time to First Token": 0.269, "Is Agentic": False},
                             "Code Analysis": {"Avg 2nd+ Token Generation Rate": 213.498,
                                               "Avg Time to First Token": 0}}}


def test_build_command():
    assert mlperf_client.build_command("m.exe", "llm/x.json", "D", "O", "r.csv") == [
        "m.exe", "-c", "llm/x.json", "-d", "D", "-p", "false", "-o", "O", "-x", "r.csv"]


def test_parse_real_results():
    score, metrics, record, error = mlperf_client.parse_results(json.dumps(REAL))
    assert error is None and score == 219.876
    assert metrics["ttft_s"] == (0.069, "s") and metrics["tps@summarization_intermediate"] == (196.876, "tok/s")
    assert "ttft_s@code_analysis" not in metrics          # 0 is not a reading
    assert record["quantization"] == "Q4_0" and record["backend"] == "llama-cpp CUDA"
    assert record["results_verified"] is False and record["tested_by_mlcommons"] is True
    assert metrics["results_verified"] == "False" and metrics["model"] == "Phi-4-mini-instruct-Q4_0"
    assert mlperf_client.parse_results(json.dumps([REAL]))[0] == 219.876


def test_parse_failures_never_score():
    failed = dict(REAL, **{"Benchmark Success": False, "Error Message": "model load failed"})
    score, _, record, error = mlperf_client.parse_results(json.dumps(failed))
    assert score is None and "model load failed" in error and record["model"]
    assert mlperf_client.parse_results(json.dumps(dict(REAL, overall_results={})))[0] is None
    assert mlperf_client.parse_results("x")[3].startswith("not JSON")


def test_mlperf_path(tmp_path):
    assert mlperf_client.mlperf_path({"USERPROFILE": str(tmp_path)}) is None
    exe = tmp_path / "Tools" / "MLPerfClient" / "mlperf-windows.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert mlperf_client.mlperf_path({"USERPROFILE": str(tmp_path)}) == exe
    other = tmp_path / "m.exe"
    other.write_bytes(b"")
    assert mlperf_client.mlperf_path({"USERPROFILE": str(tmp_path), "PCBM_MLPERF_CLIENT": str(other)}) == other


class PyMlperf(mlperf_client.MlperfClientWorkload):
    """The fake tool is a Python script; the tool folder holds the config like the real one."""
    def __init__(self, tool_dir: Path, body: str):
        (tool_dir / "llm" / "phi4mini").mkdir(parents=True, exist_ok=True)
        (tool_dir / mlperf_client.DEFAULT_CONFIG).write_text("{}", encoding="utf-8")
        exe = tool_dir / "mlperf-windows.exe"
        exe.write_bytes(b"")
        super().__init__(exe=exe)
        self.script = tool_dir / "fake.py"
        self.script.write_text("import sys, os\nargs = sys.argv[1:]\nout = args[args.index('-o') + 1]\n" + body,
                               encoding="utf-8")

    def _command(self, folder):
        return [sys.executable, str(self.script)] + super()._command(folder)[1:]


def test_workload_completed_and_failures(tmp_path):
    ok = PyMlperf(tmp_path / "tool", f"open(os.path.join(out, 'results.json'), 'w').write({json.dumps(json.dumps(REAL))})\n")
    run = tmp_path / "run"
    run.mkdir()
    result = ok.run(RunContext(output_dir=run, timeout_seconds=60))
    assert result.status == "completed" and result.score == 219.876 and result.unit == "tok/s"
    assert json.loads((run / "ai_run.json").read_text(encoding="utf-8"))["device"].startswith("NVIDIA")
    assert (tmp_path / "tool" / "data").is_dir()
    failed = json.dumps(json.dumps(dict(REAL, **{"Benchmark Success": False})))
    for name, body, status in (("none", "pass\n", "invalid"),       # exit 0 without results (seen for real)
                               ("bad", f"open(os.path.join(out, 'results.json'), 'w').write({failed})\n", "failed"),
                               ("exit", "sys.exit(3)\n", "failed")):
        run = tmp_path / name
        run.mkdir()
        result = PyMlperf(tmp_path / ("tool_" + name), body).run(RunContext(output_dir=run, timeout_seconds=60))
        assert result.status == status and result.score is None
    missing = mlperf_client.MlperfClientWorkload(exe=None)
    missing.exe = None
    assert missing.availability()[0] is False
    assert missing.run(RunContext(output_dir=tmp_path)).status == "skipped"

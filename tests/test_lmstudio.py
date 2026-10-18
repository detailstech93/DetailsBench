import json
import subprocess

import pytest

from app.ai import lmstudio

MODELS = json.dumps({"object": "list", "data": [
    {"id": "qwen/qwen3.8-27b", "object": "model", "type": "vlm", "arch": "qwen35", "compatibility_type": "gguf",
     "quantization": "Q4_K_M", "state": "loaded", "max_context_length": 262144, "loaded_context_length": 32768,
     "capabilities": ["tool_use"]},
    {"id": "google/gemma-4-12b-qat", "compatibility_type": "gguf", "quantization": "Q4_0", "state": "not-loaded"},
    {"object": "model"}]})
TABLE = ("\x1b[38;5;166mLLM ENGINE                                             SELECTED    MODEL FORMAT\x1b[0m\n"
         "llama.cpp-win-x86_64-avx2@2.55.0                                       GGUF    \n"
         "llama.cpp-win-x86_64-nvidia-cuda12-avx2@2.60.0            ✓            GGUF    \n"
         "executorch-asr-win-x86_64-nvidia-cuda128-avx2@0.0.8       ✓            PTE     \n")


def test_parse_models_keeps_known_fields_only():
    models = lmstudio.parse_models(MODELS)
    assert [m["id"] for m in models] == ["qwen/qwen3.8-27b", "google/gemma-4-12b-qat"]
    assert "capabilities" not in models[0] and models[0]["loaded_context_length"] == 32768
    assert lmstudio.parse_models('{"data": 3}') == []
    with pytest.raises(ValueError):
        lmstudio.parse_models("not json")


def test_parse_runtimes_takes_the_selected_engine_per_format():
    assert lmstudio.parse_runtimes(TABLE) == {"GGUF": "llama.cpp-win-x86_64-nvidia-cuda12-avx2@2.60.0",
                                              "PTE": "executorch-asr-win-x86_64-nvidia-cuda128-avx2@0.0.8"}
    assert lmstudio.parse_runtimes("") == {}


def test_run_record_leaves_out_what_is_not_reported():
    loaded, other = lmstudio.parse_models(MODELS)
    runtimes = lmstudio.parse_runtimes(TABLE)
    assert lmstudio.run_record(loaded, runtimes) == {
        "server": "LM Studio", "model": "qwen/qwen3.8-27b", "quantization": "Q4_K_M", "arch": "qwen35",
        "format": "GGUF", "backend": "llama.cpp-win-x86_64-nvidia-cuda12-avx2@2.60.0", "context_length": 32768,
        "max_context_length": 262144}
    assert "context_length" not in lmstudio.run_record(other, runtimes)
    assert "backend" not in lmstudio.run_record(other, {})


def test_read_models_and_runtimes_with_fakes(tmp_path):
    seen = []
    assert len(lmstudio.read_models("http://x:1/", fetch=lambda u: (seen.append(u), MODELS)[1])) == 2
    assert seen == ["http://x:1/api/v0/models"]
    ok = lambda args, **kw: subprocess.CompletedProcess(args, 0, TABLE.encode("utf-8"), b"")
    bad = lambda args, **kw: subprocess.CompletedProcess(args, 1, b"", b"error")

    def gone(args, **kw):
        raise subprocess.TimeoutExpired(args, 20)
    assert "GGUF" in lmstudio.read_runtimes(tmp_path / "lms.exe", run=ok)
    assert lmstudio.read_runtimes(tmp_path / "lms.exe", run=bad) == {}
    assert lmstudio.read_runtimes(tmp_path / "lms.exe", run=gone) == {}
    assert lmstudio.read_runtimes(None) == {}


def test_lms_path(tmp_path):
    assert lmstudio.lms_path({"USERPROFILE": str(tmp_path)}) is None
    (tmp_path / ".lmstudio" / "bin").mkdir(parents=True)
    (tmp_path / ".lmstudio" / "bin" / "lms.exe").write_bytes(b"")
    assert lmstudio.lms_path({"USERPROFILE": str(tmp_path)}).name == "lms.exe"

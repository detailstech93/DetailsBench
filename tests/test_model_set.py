"""Fixed LM Studio models through llama-benchy: installed check, lms get / load / unload, a faked run, install plan
and the report (no LM Studio, no download)."""

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import llama_benchy, model_set as ms
from app.automation.workload import RunContext
from app.install import plans

M = lambda v: {"mean": v, "std": 0.0, "values": [v]}
RESULT = {"version": "0.4.0", "latency_mode": "api", "latency_ms": 5.0, "benchmarks": [
    {"concurrency": 1, "context_size": 0, "is_context_prefill_phase": False, "pp_throughput": M(2400.5),
     "tg_throughput": M(61.25), "ttfr": M(220.0)}]}
# shape of `lms ls --json` on this computer (2026-10-19), trimmed
LS = [{"type": "llm", "modelKey": "google/gemma-4-12b-qat", "variants": ["google/gemma-4-12b-qat@q4_0"],
       "selectedVariant": "google/gemma-4-12b-qat@q4_0"},
      {"type": "llm", "modelKey": "qwen/qwen3.8-27b", "variants": ["qwen/qwen3.8-27b@q4_k_m", "qwen/qwen3.8-27b@q8_0"],
       "selectedVariant": "qwen/qwen3.8-27b@q8_0"}]


class FakeLms:
    def __init__(self, ls=LS, load_code=0):
        self.calls, self.ls, self.load_code = [], ls, load_code

    def __call__(self, argv, timeout):
        self.calls.append(argv[1:])
        if argv[1] == "ls":
            return 0, json.dumps(self.ls)
        if argv[1] == "load":
            return self.load_code, "" if self.load_code == 0 else "Error: not enough GPU memory"
        return 0, ""


def setup_function(_):
    ms._LS.clear()


def test_installed_needs_the_fixed_quantization_selected():
    assert ms.installed(LS, "google/gemma-4-12b-qat", "Q4_0") == (True, "")
    ok, why = ms.installed(LS, "qwen/qwen3.5-4b", "Q4_K_M")
    assert not ok and "not installed" in why
    ok, why = ms.installed(LS, "qwen/qwen3.8-27b", "Q4_K_M")
    assert not ok and "qwen/qwen3.8-27b@q8_0 selected" in why
    ok, why = ms.installed(LS, "google/gemma-4-12b-qat", "Q8_0")
    assert not ok and "not installed" in why


def test_the_lms_commands():
    assert ms.get_command("lms", "qwen/qwen3.5-4b", "Q4_K_M") == ["lms", "get", "qwen/qwen3.5-4b@q4_k_m", "--gguf", "-y"]
    assert ms.load_command("lms", "qwen/qwen3.5-4b", "pcbm-x") == [
        "lms", "load", "qwen/qwen3.5-4b", "--identifier", "pcbm-x", "-c", "4096", "--gpu", "max", "--parallel", "1",
        "-y"]
    assert ms.unload_command("lms", "pcbm-x") == ["lms", "unload", "pcbm-x"]
    assert "lms get google/gemma-4-12b-qat@q4_0 --gguf -y" in ms.install_text("llama_benchy_gemma4_12b")


def test_install_runs_lms_get_then_checks_the_list(tmp_path):
    fake = FakeLms(ls=[])
    out = ms.install("llama_benchy_qwen35_4b", lms=tmp_path / "lms.exe", run=fake)
    assert not out.ok and "not installed" in out.message and fake.calls[0] == ["get", "qwen/qwen3.5-4b@q4_k_m", "--gguf", "-y"]
    ms._LS.clear()
    out = ms.install("llama_benchy_gemma4_12b", lms=tmp_path / "lms.exe", run=FakeLms())
    assert out.ok and "Gemma 4 12B QAT Q4_0 is ready" in out.message


def _workload(tmp_path, fake, test_id="llama_benchy_gemma4_12b"):
    exe = tmp_path / "llama-benchy.exe"
    exe.write_bytes(b"")
    return ms.FixedModelWorkload(test_id, exe=exe, lms=tmp_path / "lms.exe", run=fake,
                                 read_models=lambda url: [], read_runtimes=lambda: {})


def test_a_run_loads_its_own_copy_and_always_unloads_it(tmp_path, monkeypatch):
    seen = {}

    def run_process(command, out, err, timeout, stop, cwd=None, **k):
        seen["command"] = command
        Path(out).write_text("", encoding="utf-8")
        Path(err).write_text("", encoding="utf-8")
        Path(command[command.index("--save-result") + 1]).write_text(json.dumps(RESULT), encoding="utf-8")
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False, duration_seconds=40.0, start_error=None)
    monkeypatch.setattr(llama_benchy, "run_process", run_process)
    fake = FakeLms()
    w = _workload(tmp_path, fake)
    assert w.availability() == (True, "") and not w.verified and not w.asks_settings
    r = w.run(RunContext(output_dir=tmp_path / "run", timeout_seconds=60, stop_event=threading.Event()))
    assert r.status == "completed" and r.score == 61.25 and r.unit == "tok/s"
    assert seen["command"][seen["command"].index("--model") + 1] == "pcbm-llama_benchy_gemma4_12b"
    assert ["--runs", "3"] == seen["command"][seen["command"].index("--runs"):][:2]
    assert [c[0] for c in fake.calls if c[0] != "ls"] == ["unload", "load", "unload"]
    assert "Context: 4096" in r.note and "google/gemma-4-12b-qat Q4_0" in r.note
    assert (tmp_path / "run" / "lms_load.txt").is_file()


def test_a_model_that_does_not_fit_stores_no_score_and_is_unloaded(tmp_path):
    fake = FakeLms(load_code=1)
    r = _workload(tmp_path, fake).run(RunContext(output_dir=tmp_path / "run", timeout_seconds=60))
    assert r.status == "failed" and r.score is None and "not enough GPU memory" in r.error
    assert fake.calls[-1] == ["unload", "pcbm-llama_benchy_gemma4_12b"]
    w = _workload(tmp_path, FakeLms(), "llama_benchy_qwen38_27b")
    assert w.run(RunContext(output_dir=tmp_path / "b")).status == "skipped"


def test_install_plan_and_report_sections():
    plan = plans.plan_for("llama_benchy_qwen35_4b", host=plans.Host("linux", "arm64", "arch"))
    assert plan == {"kind": "lmstudio", "test_id": "llama_benchy_qwen35_4b"}
    assert plans.plan_for("llama_benchy_qwen35_4b", host=plans.Host("darwin", "arm64")) is None
    from app.report import _model_sections
    export = {"session": {}, "runs": [
        {"benchmark_name": "llama_benchy_qwen38_27b", "status": "completed", "is_warmup": False, "unit": "tok/s",
         "note": "Model: qwen/qwen3.8-27b Q4_K_M"}],
        "summaries": {"llama_benchy_qwen38_27b": {"stats": {"mean": 41.27}}}}
    html = _model_sections(export)
    assert "<h2>Qwen3.8 27B (llama-benchy)</h2>" in html and "41.3 <span>tok/s</span>" in html
    assert "Gemma" not in html and _model_sections({"runs": []}) == ""


def test_the_owner_adds_and_removes_own_models(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    test_id = ms.add_model("qwen/qwen3.5-9b", "q4_k_m", "Qwen3.5 9B", "7.0 GB")
    assert test_id == "llama_benchy_my_qwen_qwen3_5_9b_q4_k_m"
    assert ms.all_models()[test_id] == ("Qwen3.5 9B", "qwen/qwen3.5-9b", "Q4_K_M", "7.0 GB")
    assert len(ms.all_models()) == 4 and [w.id for w in ms.model_workloads()][-1] == test_id
    assert plans.plan_for(test_id, host=plans.Host("windows", "x64")) == {"kind": "lmstudio", "test_id": test_id}
    assert "lms get qwen/qwen3.5-9b@q4_k_m" in ms.install_text(test_id)
    for key, quant in (("not a key", "Q4_0"), ("a/b", "Q4 0; rm"), ("google/gemma-4-12b-qat", "Q4_0")):
        try:
            ms.add_model(key, quant)
            assert False, key
        except ValueError:
            pass
    assert not ms.remove_model("llama_benchy_gemma4_12b") and ms.remove_model(test_id)
    assert len(ms.all_models()) == 3


def test_models_on_disk_for_the_choice():
    assert ms.on_disk(LS + [{"type": "embedding", "modelKey": "x/y"}]) == [
        ("google/gemma-4-12b-qat", "Q4_0", "google/gemma-4-12b-qat", ""),
        ("qwen/qwen3.8-27b", "Q4_K_M", "qwen/qwen3.8-27b", ""), ("qwen/qwen3.8-27b", "Q8_0", "qwen/qwen3.8-27b", "")]


def test_a_removed_models_results_keep_their_section():
    from app.report import _model_sections
    export = {"session": {}, "runs": [
        {"benchmark_name": "llama_benchy_my_old_model_q4_0", "status": "completed", "is_warmup": False,
         "unit": "tok/s", "note": "Model: old/model Q4_0"}],
        "summaries": {"llama_benchy_my_old_model_q4_0": {"stats": {"mean": 20.0}}}}
    html = _model_sections(export)
    assert "llama_benchy_my_old_model_q4_0 (llama-benchy)" in html and "20.0 <span>tok/s</span>" in html


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_ai_models_dialog(tmp_path, qapp):
    from app.ui.ai_models_dialog import AiModelsDialog
    told = []
    d = AiModelsDialog(data_dir=tmp_path, on_disk=lambda: [("qwen/qwen3.8-27b", "Q8_0", "Qwen3.8 27B", "28.6 GB")],
                       tell=told.append)
    assert d.list.count() == 3 and not d.remove() and "built-in" in told[-1]
    d.choice.setCurrentIndex(0)
    assert d.add() and d.changed and d.list.count() == 4
    d.choice.setEditText("mistral/ministral-8b@Q4_K_M")
    assert d.add() and d.list.count() == 5
    d.list.setCurrentRow(4)
    assert d.remove() and d.list.count() == 4
    assert "Qwen3.8 27B" in ms.custom_models(tmp_path)[ms.custom_id("qwen/qwen3.8-27b", "Q8_0")][0]


def test_run_all_lists_a_model_with_its_size():
    from app.ui.run_all import Item
    item = Item(SimpleNamespace(name="llama-benchy: Qwen3.5 4B (Q4_K_M)"),
                {"kind": "lmstudio", "test_id": "llama_benchy_qwen35_4b"})
    assert item.line() == ("- llama-benchy: Qwen3.5 4B (Q4_K_M): Download Qwen3.5 4B (Q4_K_M, about 3.75 GB as "
                           "LM Studio lists it) with LM Studio's own command:")

"""Geekbench 7's free command line, automated (owner allowed its uploads, 2026-10-20). Fakes only: nothing runs."""

import subprocess
import threading

from app.automation.process import ProcessOutcome
from app.automation.workload import RunContext
from app.pro import geekbench as gb

# trimmed from real runs on the owner's PC (2026-10-20)
CPU_OUT = """Geekbench 7.1.0 : https://www.geekbench.com/

Geekbench 7 requires an active internet connection and automatically uploads
benchmark results to the Geekbench Browser.

Single-Core
  Running File Compression

Uploading results to the Geekbench Browser. This could take a minute or two
depending on the speed of your internet connection.

Upload succeeded. Visit the following link and view your results online:

  https://browser.geekbench.com/v7/cpu/675555

Visit the following link and add this result to your profile:

  https://browser.geekbench.com/v7/cpu/675555/claim?key=161012

Benchmark Summary
  Single-Core Score             2545
  Multi-Core Score             22129
"""
GPU_OUT = """OpenCL Information
  Platform Vendor               NVIDIA Corporation
  Device Name                   NVIDIA GeForce RTX 5090 Laptop GPU

Upload succeeded. Visit the following link and view your results online:

  https://browser.geekbench.com/v7/gpu/307520

Visit the following link and add this result to your profile:

  https://browser.geekbench.com/v7/gpu/307520/claim?key=111076

Benchmark Summary
  GPU Score                   248425
"""
GPU_LIST = """CUDA
+-------------+-----------+------------------------------------+
| Platform ID | Device ID | Description                        |
+-------------+-----------+------------------------------------+
| 0           | 0         | NVIDIA GeForce RTX 5090 Laptop GPU |
+-------------+-----------+------------------------------------+

OpenCL
+-------------+-----------+------------------------------------+
| Platform ID | Device ID | Description                        |
+-------------+-----------+------------------------------------+
| 0           | 0         | Intel(R) Graphics                  |
+-------------+-----------+------------------------------------+
| 1           | 0         | NVIDIA GeForce RTX 5090 Laptop GPU |
+-------------+-----------+------------------------------------+

Vulkan
+-------------+-----------+------------------------------------+
| Platform ID | Device ID | Description                        |
+-------------+-----------+------------------------------------+
| 0           | 0         | NVIDIA GeForce RTX 5090 Laptop GPU |
+-------------+-----------+------------------------------------+
| 0           | 1         | Intel(R) Graphics                  |
+-------------+-----------+------------------------------------+
"""


def test_cpu_scores():
    score, metrics, why = gb.parse_scores(CPU_OUT, gpu=False)
    assert score == 22129 and why is None
    assert metrics == {"multi_core_score": (22129.0, "points"), "single_core_score": (2545.0, "points")}


def test_gpu_score():
    assert gb.parse_scores(GPU_OUT, gpu=True)[:2] == (248425.0, {"gpu_score": (248425.0, "points")})


def test_no_upload_is_no_score():
    text = "Uploading results to the Geekbench Browser.\nUpload failed: could not connect\n"
    score, _, why = gb.parse_scores(text, gpu=False)
    assert score is None and "internet" in why and "Upload failed" in why


def test_only_the_result_link_not_the_claim_key():
    assert gb.RESULT_URL.findall(CPU_OUT) == ["https://browser.geekbench.com/v7/cpu/675555"]


def test_gpu_list_and_the_biggest_gpu_is_picked():
    apis = gb.parse_gpu_list(GPU_LIST)
    assert apis["CUDA"] == [(0, 0, "NVIDIA GeForce RTX 5090 Laptop GPU")]
    assert gb.pick_device(apis["OpenCL"], "NVIDIA GeForce RTX 5090 Laptop GPU") == (1, 0, "NVIDIA GeForce RTX 5090 Laptop GPU")
    assert gb.pick_device(apis["Vulkan"], "Radeon RX 9070") == (0, 0, "NVIDIA GeForce RTX 5090 Laptop GPU")
    assert gb.pick_device([], "x") is None


def test_program_found_per_system(tmp_path):
    exe = r"C:\PF\Geekbench 7\geekbench7.exe"
    assert gb.program_path("win32", {"ProgramFiles": r"C:\PF"}, isfile=lambda p: p == exe) == exe
    assert gb.program_path("win32", {}, isfile=lambda p: True) is None
    (tmp_path / "Geekbench-7.1.0-Linux").mkdir()
    (tmp_path / "Geekbench-7.1.0-Linux" / "geekbench7").write_text("")
    found = gb.program_path("linux", which=lambda n: None, home=str(tmp_path))
    assert found.endswith("geekbench7")


def _workload(test_id, out_text, monkeypatch, listed=GPU_LIST, exit_code=0):
    seen = {}

    def fake_run_process(command, out, err, timeout, stop, cwd=None):
        seen["command"] = command
        out.write_text(out_text, encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return ProcessOutcome(exit_code, False, False, 360.0)
    monkeypatch.setattr(gb, "run_process", fake_run_process)

    def runner(command, **kw):
        assert command[1:] == ["--gpu-list"]
        return subprocess.CompletedProcess(command, 0, stdout=listed, stderr="")
    wl = gb.GeekbenchWorkload(test_id, exe=r"C:\GB\geekbench7.exe",
                              gpu_name=lambda: "NVIDIA GeForce RTX 5090 Laptop GPU", runner=runner)
    return wl, seen


def test_cpu_run(tmp_path, monkeypatch):
    wl, seen = _workload("geekbench7_cpu", CPU_OUT, monkeypatch)
    r = wl.run(RunContext(output_dir=tmp_path, timeout_seconds=1800, stop_event=threading.Event()))
    assert seen["command"] == [r"C:\GB\geekbench7.exe", "--cpu"]
    assert r.status == "completed" and r.score == 22129 and r.unit == "points"
    assert "Single-Core Score: 2545" in r.note and "Result online: https://browser.geekbench.com/v7/cpu/675555" in r.note
    assert "claim" not in r.note and "uploaded" in r.note
    assert wl.verified and wl.benchmark_type == "cpu" and wl.no_warmup and not hasattr(wl, "fixed_counts")


def test_gpu_run_uses_the_biggest_gpu(tmp_path, monkeypatch):
    wl, seen = _workload("geekbench7_gpu_opencl", GPU_OUT, monkeypatch)
    r = wl.run(RunContext(output_dir=tmp_path, timeout_seconds=1200, stop_event=threading.Event()))
    assert seen["command"][1:] == ["--gpu", "OpenCL", "--gpu-platform-id", "1", "--gpu-device-id", "0"]
    assert r.status == "completed" and r.score == 248425
    assert "Device: NVIDIA GeForce RTX 5090 Laptop GPU" in r.note and wl.benchmark_type == "gpu"


def test_an_api_with_no_device_is_skipped(tmp_path, monkeypatch):
    wl, _ = _workload("geekbench7_gpu_cuda", GPU_OUT, monkeypatch, listed="OpenCL\n| 0 | 0 | Intel(R) Graphics |\n")
    r = wl.run(RunContext(output_dir=tmp_path, stop_event=threading.Event()))
    assert r.status == "skipped" and "no CUDA device" in r.error


def test_a_failed_upload_is_invalid_not_a_score(tmp_path, monkeypatch):
    wl, _ = _workload("geekbench7_cpu", "Upload failed.\n", monkeypatch, exit_code=1)
    r = wl.run(RunContext(output_dir=tmp_path, stop_event=threading.Event()))
    assert r.status == "invalid" and r.score is None and "exit code 1" in r.error


def test_not_installed_is_skipped(tmp_path):
    wl = gb.GeekbenchWorkload("geekbench7_cpu", finder=lambda: None)
    assert not wl.availability()[0] and wl.run(RunContext(output_dir=tmp_path)).status == "skipped"


def test_the_warning_says_it_uploads():
    assert "uploads every result" in gb.WARNING


def test_the_report_has_its_own_sections():
    from app import report
    assert [t[0] for t in report.GEEKBENCH_TESTS + report.GEEKBENCH6_TESTS] == list(gb.MODES)
    assert [t[0] for t in report.WEB_TESTS] == ["speedometer", "jetstream"]


def test_the_chosen_runs_and_no_warm_up():
    """Owner, 2026-10-20: one run is not enough (22129 then 19963 on the same PC the same day); no warm-up."""
    from types import SimpleNamespace as NS
    from app.ui.automation_panel import AutomationPanel
    wl = gb.GeekbenchWorkload("geekbench7_cpu", exe=r"C:\GB\geekbench7.exe")
    other = NS(id="sevenzip_benchmark", timeout_seconds=600)
    plan = AutomationPanel.build_plan(None, [wl, other], NS(warmup_runs=1, repetitions=3), extra_runs=2)
    assert [(p.warmup_runs, p.repetitions, p.max_extra_runs) for p in plan] == [(0, 3, 2), (1, 3, 2)]


def test_the_report_shows_single_core_next_to_multi_core(tmp_path):
    """Owner, 2026-10-20: the report showed only the Multi-Core tile."""
    from app.database import Database
    from app.export import build_session_export
    from app.report import render_session_report
    db = Database(str(tmp_path / "r.db"))
    sid = db.create_session("Automated")
    for i, (multi, single) in enumerate(((19963.0, 2033.0), (20500.0, 2051.0)), 1):
        rid = db.create_result(sid, "geekbench7_cpu", "cpu", multi, unit="points", run_index=i)
        db.save_result_metrics(rid, {"multi_core_score": (multi, "points"), "single_core_score": (single, "points")})
    html = render_session_report(build_session_export(db, sid))
    assert "CPU, Multi-Core</div><div class=\"value\">20,232 <span>points" in html
    assert "CPU, Single-Core</div><div class=\"value\">2,042 <span>points" in html


def test_geekbench_6_runs_the_same_way(tmp_path, monkeypatch):
    """Owner, 2026-10-20: "yes, I allow it" (Geekbench 6 automated, its uploads too). Not verified before a real run."""
    exe = r"C:\PF\Geekbench 6\geekbench6.exe"
    assert gb.program_path("win32", {"ProgramFiles": r"C:\PF"}, isfile=lambda p: p == exe, major=6) == exe
    assert gb.program_path("linux", which=lambda n: n == "geekbench6" and "/usr/bin/geekbench6" or None,
                           major=6) == "/usr/bin/geekbench6"
    wl, seen = _workload("geekbench6_cpu", CPU_OUT.replace("7.1.0", "6.4.0"), monkeypatch)
    r = wl.run(RunContext(output_dir=tmp_path, timeout_seconds=1800, stop_event=threading.Event()))
    assert seen["command"][1:] == ["--cpu"] and r.status == "completed" and r.score == 22129
    assert r.note.startswith("Geekbench 6; test: CPU") and "single_core_score" in r.metrics
    assert wl.display_name == "Geekbench 6 (CPU)" and not wl.verified and "Geekbench 6's free version" in wl.warning
    assert gb.GeekbenchWorkload("geekbench6_cpu", finder=lambda: None).availability()[1].startswith("Geekbench 6 is")
    assert "geekbench6_gpu_cuda" not in gb.MODES      # Geekbench 6 has no CUDA test
    from data.installers import INSTALLERS
    assert INSTALLERS["geekbench6_cpu"] is INSTALLERS["geekbench_6"]

"""Geekbench 7's free command line, automated (owner allowed its uploads, 2026-10-20). Fakes only: nothing runs."""

import subprocess
import threading
import sys
from types import SimpleNamespace

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

  https://browser.geekbench.com/v7/cpu/675555/claim?key=000000

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

  https://browser.geekbench.com/v7/gpu/307520/claim?key=000000

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
    monkeypatch.setattr(wl, "system_blocks", lambda: None)  # a driver is there; not this computer's (Windows ARM failed)
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


def test_linux_install_is_the_pinned_tar_gz_and_the_program_is_found_there(tmp_path):
    """Owner, 2026-10-20: Geekbench 6 and 7 had no Install on Linux."""
    from app.install import direct
    from app.install.plans import Host, plan_for
    linux = Host("linux", "x64", "debian")
    for test_id, major, version in (("geekbench7_cpu", 7, "7.1.0"), ("geekbench7_gpu_cuda", 7, "7.1.0"),
                                    ("geekbench6_cpu", 6, "6.7.1"), ("geekbench_6", 6, "6.7.1")):
        plan = plan_for(test_id, host=linux)
        assert plan["kind"] == "zip" and plan["version"] == version and len(plan["sha256"]) == 64
        assert plan["hosts"] == ["geekbench-cdn.b-cdn.net"] and plan["url"].endswith("-Linux.tar.gz")
        assert direct.problem(plan) is None
        assert plan["exe"] == f"Geekbench-{version}-Linux/geekbench{major}"
    arm = Host("linux", "arm64", "debian")
    for test_id, major, version in (("geekbench7_cpu", 7, "7.1.0"), ("geekbench6_cpu", 6, "6.7.1")):
        plan = plan_for(test_id, host=arm)              # Primate Labs' ARM preview build (Radxa board, 2026-10-21)
        assert plan["kind"] == "zip" and plan["version"] == version and direct.problem(plan) is None
        assert plan["url"] == f"https://cdn.geekbench.com/Geekbench-{version}-LinuxARMPreview.tar.gz"
        assert plan["exe"] == f"Geekbench-{version}-LinuxARMPreview/geekbench{major}"
    assert plan_for("geekbench7_cpu", host=Host("linux", "x86", "debian")) is None
    assert plan_for("geekbench6_cpu", host=Host("windows", "x64"))["kind"] == "winget"         # Windows unchanged
    exe = tmp_path / "tools" / "geekbench-7.1.0-linux" / "Geekbench-7.1.0-Linux" / "geekbench7"
    assert gb.installed_path(7, data_dir=tmp_path, arch="x64") is None
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert gb.installed_path(7, data_dir=tmp_path, arch="x64") == str(exe)
    assert gb.installed_path(7, data_dir=tmp_path, arch="arm64") is None     # an x86-64 build is not run on ARM
    arm = tmp_path / "tools" / "geekbench-7.1.0-linux-arm" / "Geekbench-7.1.0-LinuxARMPreview" / "geekbench7"
    arm.parent.mkdir(parents=True)
    arm.write_text("")
    assert gb.installed_path(7, data_dir=tmp_path, arch="arm64") == str(arm)


def test_the_linux_archives_may_unpack_to_their_real_size():
    from data.installers import INSTALLERS
    # their tar listings: Geekbench 7.1.0 unpacks 1,009,518,416 bytes (owner hit the 500 MB limit), 6.7.1 500,109,413
    assert INSTALLERS["geekbench7_cpu"]["linux"]["zip_by_arch"]["x64"]["max_unpacked"] > 1_009_518_416
    assert INSTALLERS["geekbench_6"]["linux"]["zip_by_arch"]["x64"]["max_unpacked"] > 500_109_413
    assert INSTALLERS["geekbench7_cpu"]["linux"]["zip_by_arch"]["arm64"]["max_unpacked"] > 826_968_228
    assert INSTALLERS["geekbench_6"]["linux"]["zip_by_arch"]["arm64"]["max_unpacked"] > 396_014_581


GB6_LINUX_OUT = ("Single-Core\n  Running Clang\n\nMulti-Core\n  Running Clang\n\n"
                 "Upload succeeded. Visit the following link and view your results online:\n\n"
                 "  https://browser.geekbench.com/v6/cpu/19370277\n\n"
                 "Visit the following link and add this result to your profile:\n\n"
                 "  https://browser.geekbench.com/v6/cpu/19370277/claim?key=244591\n")


def test_geekbench6_on_linux_prints_no_score_and_the_claim_key_is_not_kept(tmp_path, monkeypatch):
    # Geekbench 6.7.1, full run on Linux (WSL) 2026-10-21: only the result links, no score lines
    wl, _ = _workload("geekbench6_cpu", GB6_LINUX_OUT, monkeypatch)
    r = wl.run(RunContext(output_dir=tmp_path, stop_event=threading.Event()))
    assert r.status == "invalid" and r.score is None
    assert "https://browser.geekbench.com/v6/cpu/19370277" in r.error and "claim" not in r.error
    assert gb.RESULT_URL.findall("https://browser.geekbench.com/v6/compute/6945159\n") == [
        "https://browser.geekbench.com/v6/compute/6945159"]


def test_a_software_renderer_is_never_a_gpu_score(tmp_path, monkeypatch):
    # WSL, 2026-10-21: Vulkan listed only Mesa's llvmpipe, and its "GPU Score" was the CPU's
    listed = "Vulkan\n| Platform ID | Device ID | Description |\n| 0 | 0 | llvmpipe (LLVM 20.1.2, 256 bits) |\n"
    wl, seen = _workload("geekbench7_gpu_vulkan", GPU_OUT, monkeypatch, listed=listed)
    r = wl.run(RunContext(output_dir=tmp_path, stop_event=threading.Event()))
    assert r.status == "skipped" and "llvmpipe" in r.error and "software renderer" in r.error and not seen
    out = GPU_OUT + "  Device Name                   llvmpipe (LLVM 20.1.2, 256 bits)\n"
    wl, _ = _workload("geekbench7_gpu_opencl", out, monkeypatch)
    r = wl.run(RunContext(output_dir=tmp_path, stop_event=threading.Event()))
    assert r.status == "invalid" and r.score is None and "llvmpipe" in r.error


def test_geekbench_6_on_linux_waits_for_a_typed_score_and_runs_alone(monkeypatch):
    """Owner, 2026-10-21: Geekbench 6 on Linux prints no score, even in a terminal; its page sits behind a browser
    check. It joins the tests whose score is typed (each runs alone)."""
    from app.catalog import TestEntry
    from app.ui.run_tests import leave_out_typed
    monkeypatch.setattr(sys, "platform", "linux")
    gb6, gb7 = gb.GeekbenchWorkload("geekbench6_cpu", exe="gb6"), gb.GeekbenchWorkload("geekbench7_cpu", exe="gb7")
    assert gb6.typed_score and not gb7.typed_score
    assert gb6.typed_metric == ("single_core_score", "Single-Core")     # its page shows Single- and Multi-Core
    assert gb.GeekbenchWorkload("geekbench6_gpu_vulkan", exe="gb6").typed_metric is None
    monkeypatch.setattr(sys, "platform", "win32")
    assert not gb.GeekbenchWorkload("geekbench6_cpu", exe="gb6").typed_score

    def entry(w):
        return TestEntry(id=w.id, name=w.display_name, group="cpu", mode="automatic", state="installed",
                         state_reason="", compatible=True, compatibility_reason="", verified=False, source="workload",
                         ref=w)
    assert entry(gb6).waits_for_typed_score() and not entry(gb7).waits_for_typed_score()
    assert leave_out_typed([entry(gb6), entry(gb7)]) == ([entry(gb7)], [entry(gb6)])


def test_after_the_runs_their_pages_open_and_one_typed_score_per_run_is_stored(tmp_path, monkeypatch):
    """Owner, 2026-10-21: runs chosen first; one field per run; the report averages them."""
    from app.catalog import TestEntry
    from app.ui import reports_folder, run_tests
    urls = ["https://browser.geekbench.com/v6/cpu/19371314", "https://browser.geekbench.com/v6/cpu/19371320"]
    stored, opened, offered, reports, asked = [], [], [], [], []

    class Db:
        def get_runs(self, sid, name=None, include_test=False):
            return [{"id": n, "benchmark_name": name, "status": "invalid", "is_warmup": 0,
                     "note": f"Geekbench finished and uploaded, but printed no 'Multi-Core Score' line: {u}"}
                    for n, u in enumerate(urls)]

        def create_result(self, *a, **k):
            stored.append((a, k))
            return 41

        def save_result_metrics(self, result_id, metrics):
            stored.append((result_id, metrics))
    label = SimpleNamespace(value="", text=lambda: label.value)
    label.setText = lambda t: setattr(label, "value", t)
    page = SimpleNamespace(open_url=opened.append, offer_record=lambda text, fn: offered.append((text, fn)),
                           offer_report=lambda fn: None)
    window = SimpleNamespace(db=Db(), status_label=label, tests_page=page)
    monkeypatch.setattr(sys, "platform", "linux")
    w = gb.GeekbenchWorkload("geekbench6_cpu", exe="gb6")
    monkeypatch.undo()
    e = TestEntry(id=w.id, name=w.display_name, group="cpu", mode="automatic", state="installed", state_reason="",
                  compatible=True, compatibility_reason="", verified=False, source="workload", ref=w)
    monkeypatch.setattr(reports_folder, "folder", lambda win: tmp_path)
    monkeypatch.setattr(run_tests, "write_report", lambda db, sid, folder, only=None, at=None:
                        reports.append(only) or tmp_path / "r.html")

    def ask(window, name, label, count, links, metric=None):
        asked.append((count, list(links), metric))
        return {"scores": [21500.0], "runs": [(1, 21500.0)], "unit": "points", "note": None,
                "metrics": {1: {"single_core_score": 2900.0}}}
    assert run_tests.typed_followup(window, e, 7, opener=lambda p: None, ask=ask) == urls
    assert opened == urls and "2 page(s)" in offered[0][0]
    offered[0][1]()                                     # "Enter the scores..."
    assert asked == [(2, urls, ("single_core_score", "Single-Core"))]   # Multi-Core and Single-Core per run
    (args, kw), single = stored                         # the first run's fields were left empty
    assert single == (41, {"single_core_score": (2900.0, "points")})
    assert args[:5] == (7, "geekbench6_cpu", "cpu", 21500.0, "points") and kw["is_manual"] and kw["run_index"] == 2
    assert kw["note"] == f"typed from {urls[1]}" and not kw["is_warmup"] and reports == ["geekbench6_cpu"]
    stored.clear()
    run_tests.typed_followup(window, e, 7, ask=lambda *a, **k: None)
    offered[-1][1]()
    assert stored == []                                 # cancelled: nothing stored


def test_cuda_is_hidden_without_an_nvidia_driver():
    """Radxa Dragon Q8B (Adreno 690), 2026-10-21: Geekbench 7 CUDA was listed with no NVIDIA GPU."""
    cuda = gb.GeekbenchWorkload("geekbench7_gpu_cuda", exe="gb7")
    none = dict(which=lambda name: None, exists=lambda path: False)
    assert "NVIDIA" in cuda.system_blocks(**none)
    assert cuda.system_blocks(which=lambda name: "/usr/bin/nvidia-smi", exists=lambda path: False) is None
    assert cuda.system_blocks(which=lambda name: None, exists=lambda p: p == "/usr/lib/wsl/lib/libcuda.so.1") is None
    assert gb.GeekbenchWorkload("geekbench7_gpu_vulkan", exe="gb7").system_blocks(**none) is None

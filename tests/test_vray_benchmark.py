"""V-Ray Benchmark: install plan, license asked in the app, commands, result parsing and the report (no real program)."""

import json
from pathlib import Path
from types import SimpleNamespace

from app.automation.process import ProcessOutcome
from app.automation.workload import RunContext
from app.install import direct
from app.install.plans import Host, plan_for
from app.pro import vray_benchmark as vb
from app.report import render_session_report


def _exe(tmp_path):
    exe = tmp_path / "tool" / "vray-benchmark-6.00.02-cli.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    return exe


def test_pinned_on_chaos_server_for_windows_and_linux_x64_only():
    for test_id in ("vray_benchmark", "vray_benchmark_gpu", "vray_benchmark_rtx"):
        win = plan_for(test_id, host=Host("windows", "x64"))
        linux = plan_for(test_id, host=Host("linux", "x64", "debian"))
        assert win["kind"] == linux["kind"] == "zip" and win["single"] and linux["single"]
        assert direct.problem(win) is None and direct.problem(linux) is None
        assert win["url"].startswith("https://download.chaos.com/") and win["size"] == 653056928
        assert win["exe"] == "vray-benchmark-6.00.02-cli.exe" and linux["exe"] == "vray-benchmark-6.00.02"
        assert plan_for(test_id, host=Host("windows", "arm64")) is None
        assert plan_for(test_id, host=Host("linux", "arm64", "debian")) is None
    text = direct.confirm_text("V-Ray Benchmark", plan_for("vray_benchmark", host=Host("windows", "x64")))
    assert "622.8 MB" in text and "license agreement" in text and "saved to" in text


def test_a_single_program_file_is_kept_as_is(tmp_path):
    import hashlib
    data = b"MZ program"
    plan = {"product": "Tool", "version": "1", "url": "https://download.example.org/latest?x=1",
            "hosts": ["download.example.org"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "folder": "tool-1", "exe": "tool-cli.exe", "single": True}

    class Response:
        def __init__(self):
            self.data = [data, b""]

        def read(self, n):
            return self.data.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    out = direct.install("Tool", plan, tmp_path, opener=lambda req, timeout=None: Response())
    assert out.ok and (tmp_path / "tools" / "tool-1" / "tool-cli.exe").read_bytes() == data
    assert sorted(p.name for p in (tmp_path / "tools").iterdir()) == ["tool-1"]


def test_license_is_kept_in_settings(tmp_path):
    assert not vb.license_accepted(tmp_path)
    vb.accept_license(tmp_path)
    assert vb.license_accepted(tmp_path)
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))[vb.EULA_KEY]
    assert saved["url"] == vb.EULA_URL and saved["date"]
    assert vb.EULA_URL in vb.license_question()


def test_without_the_license_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "run_process", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    w = vb.VRayBenchmarkWorkload("vray_benchmark", exe=_exe(tmp_path), data_dir=tmp_path)
    result = w.run(RunContext(output_dir=tmp_path / "r"))
    assert result.status == "skipped" and result.score is None and "license" in result.error


def test_commands_are_the_documented_ones():
    assert vb.build_command("v.exe", "vray", False, "C:/r.json") == ["v.exe", "--mode", "vray", "--output", "C:/r.json"]
    assert vb.build_command("v.exe", "vray-gpu", True, "/r.json", [1]) == [
        "v.exe", "--mode", "vray-gpu", "--rtx", "--with-devices", "1", "--output", "/r.json"]


def test_only_nvidia_gpus_are_picked():
    text = ("Do you accept? (y/n)\n1. DEFINITIONS apply\n[0] Intel(R) Core(TM) Ultra 9 275HX\n"
            "[1] NVIDIA GeForce RTX 5090 Laptop GPU\n")
    listed = vb.parse_devices(text)
    assert (1, "NVIDIA GeForce RTX 5090 Laptop GPU") in listed
    assert vb.gpu_devices(listed) == [(1, "NVIDIA GeForce RTX 5090 Laptop GPU")]
    assert vb.gpu_devices([(0, "AMD Radeon RX 7900 XTX"), (1, "AMD Ryzen 9 7950X")]) == []


DATA = Path(__file__).parent / "data"


def test_real_result_files_are_read():
    score, details, error = vb.parse_result((DATA / "vray_cpu_result.json").read_text(encoding="utf-8"))
    assert error is None and score == 34347
    assert details == {"version": "6.00.02", "V-Ray": "6.10.3", "CPU": "Intel(R) Core(TM) Ultra 9 275HX (24 threads)",
                       "CPU clock": "4923 MHz", "Render time": "60 s"}
    score, details, error = vb.parse_result((DATA / "vray_gpu_result.json").read_text(encoding="utf-8"))
    assert error is None and score == 5679
    assert details == {"version": "6.00.02", "V-Ray": "6.10.3", "devices": "NVIDIA GeForce RTX 5090 Laptop GPU 24435MB",
                       "CPU clock": "4754 MHz", "NVIDIA driver": "617.42", "Render time": "60 s"}
    # a CUDA result is not taken for the RTX test
    assert vb.parse_result((DATA / "vray_gpu_result.json").read_text(encoding="utf-8"), rtx=True)[0] is None


def test_result_file_without_a_score_is_an_error():
    for text in ("not json", "{}", "[]", json.dumps({"details": {"score": 0}}),
                 json.dumps({"details": {"score": "fast"}}), json.dumps({"score": 5})):
        score, details, error = vb.parse_result(text)
        assert score is None and error


def test_gpu_run_lists_devices_answers_the_license_and_reads_the_file(tmp_path, monkeypatch):
    exe, calls = _exe(tmp_path), []
    vb.accept_license(tmp_path)

    def fake(command, out, err, timeout, stop, cwd=None, env=None, stdin_path=None):
        calls.append((command, Path(stdin_path).read_text(encoding="ascii")))
        err.write_text("", encoding="utf-8")
        if "--list-devices" in command:
            # real output of --mode vray-gpu --rtx --list-devices: no CPU in RTX mode, so the GPU is [0]
            out.write_text("[0]: NVIDIA GeForce RTX 5090 Laptop GPU\r\n\r\n"
                           "Automatic installation was successful!\r\n", encoding="utf-8")
        else:
            Path(command[command.index("--output") + 1]).write_text(json.dumps({
                "tag": "VGpuScore", "isRtx": True, "details": {"score": 7123.4, "benchmarkVersion": [6, 0, 2]},
                "enabledGpus": [{"name": "NVIDIA GeForce RTX 5090 Laptop GPU"}]}), encoding="utf-8")
            out.write_text("", encoding="utf-8")
        return ProcessOutcome(0, False, False, 60.0)
    monkeypatch.setattr(vb, "run_process", fake)
    w = vb.VRayBenchmarkWorkload("vray_benchmark_rtx", exe=exe, data_dir=tmp_path)
    result = w.run(RunContext(output_dir=tmp_path / "r", timeout_seconds=600))
    assert result.status == "completed" and result.score == 7123.4 and result.unit == "vpaths"
    assert calls[0][0][1:] == ["--mode", "vray-gpu", "--rtx", "--list-devices"]
    assert calls[1][0][1:6] == ["--mode", "vray-gpu", "--rtx", "--with-devices", "0"]
    assert all(answer == "y\n" for _, answer in calls)
    assert result.note == ("V-Ray Benchmark: 6.00.02; Engine: GPU, RTX; "
                           "Devices: NVIDIA GeForce RTX 5090 Laptop GPU")
    assert w.benchmark_type == "gpu" and w.display_name == "V-Ray Benchmark 6.00.02 (GPU, RTX)"
    assert all(vb.VRayBenchmarkWorkload(t).verified for t in vb.MODES)


def test_no_nvidia_gpu_skips_and_no_file_is_invalid(tmp_path, monkeypatch):
    exe = _exe(tmp_path)
    vb.accept_license(tmp_path)

    def fake(command, out, err, timeout, stop, cwd=None, env=None, stdin_path=None):
        out.write_text("[0] AMD Ryzen 9 7950X\n", encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return ProcessOutcome(0, False, False, 1.0)
    monkeypatch.setattr(vb, "run_process", fake)
    gpu = vb.VRayBenchmarkWorkload("vray_benchmark_gpu", exe=exe, data_dir=tmp_path)
    result = gpu.run(RunContext(output_dir=tmp_path / "g"))
    assert result.status == "skipped" and "no NVIDIA GPU" in result.error and result.score is None
    cpu = vb.VRayBenchmarkWorkload("vray_benchmark", exe=exe, data_dir=tmp_path)
    result = cpu.run(RunContext(output_dir=tmp_path / "c"))
    assert result.status == "invalid" and "no result file" in result.error and result.score is None


def test_the_programs_own_error_is_shown(tmp_path, monkeypatch):
    vb.accept_license(tmp_path)

    def fake(command, out, err, timeout, stop, cwd=None, env=None, stdin_path=None):
        out.write_text("Automatic installation was successful!\n", encoding="utf-8")
        err.write_text("V-Ray Benchmark (CLI).exe: Invalid device index: 1\n", encoding="utf-8")
        return ProcessOutcome(0, False, False, 8.0)     # the real program exits 0 here
    monkeypatch.setattr(vb, "run_process", fake)
    w = vb.VRayBenchmarkWorkload("vray_benchmark", exe=_exe(tmp_path), data_dir=tmp_path)
    result = w.run(RunContext(output_dir=tmp_path / "r"))
    assert result.status == "invalid" and result.score is None
    assert result.error == "the benchmark wrote no result file (V-Ray Benchmark (CLI).exe: Invalid device index: 1)"


def test_the_license_is_asked_once_before_the_run():
    from app.ui.run_tests import check_licenses

    class Ref:
        def __init__(self, ok=False):
            self.license = (vb.EULA_KEY, "V-Ray Benchmark", lambda: "Accept? " + vb.EULA_URL)
            self.ok, self.accepted = ok, 0

        def license_ok(self):
            return self.ok

        def accept_license(self):
            self.accepted += 1
    refs = [Ref(), Ref()]
    entries = [SimpleNamespace(ref=refs[0], name="cpu"), SimpleNamespace(ref=refs[1], name="gpu"),
               SimpleNamespace(ref=object(), name="7-Zip")]
    answers, opened = ["open", "yes"], []
    kept = check_licenses(None, entries, ask=lambda w, p, t: answers.pop(0), open_url=opened.append)
    assert kept == entries and opened == [vb.EULA_URL] and refs[0].accepted == 1 and not answers
    kept = check_licenses(None, entries, ask=lambda w, p, t: "no", open_url=opened.append)
    assert [e.name for e in kept] == ["7-Zip"]


def _vray_export():
    run = {"benchmark_name": "vray_benchmark_rtx", "benchmark_type": "gpu", "status": "completed", "score": 7123.4,
           "unit": "vpaths", "run_index": 1, "is_warmup": False, "is_test": False,
           "note": "V-Ray Benchmark: 6.00.02; Engine: GPU, RTX; Devices: NVIDIA GeForce RTX 5090 Laptop GPU"}
    failed = {"benchmark_name": "vray_benchmark", "benchmark_type": "cpu", "status": "invalid", "score": None,
              "run_index": 1, "is_warmup": False, "is_test": False, "note": "no score in the result file"}
    return {"session": {"id": 1, "name": "s"}, "runs": [run, failed],
            "summaries": {"vray_benchmark_rtx": {"included": 1, "stats": {"mean": 7123.4}}}, "metric_summaries": {}}


def test_vray_report_section():
    page = render_session_report(_vray_export())
    part = page[page.index("<h2>V-Ray Benchmark</h2>"):page.index("<h2>Summary")]
    assert "7,123 <span>vpaths</span>" in part and "V-Ray (CPU)" in part and "no score" in part
    assert "<tr><th>Devices</th><td>NVIDIA GeForce RTX 5090 Laptop GPU</td></tr>" in part
    assert "cannot be compared" in part and "Blender" not in part


def test_summary_shows_each_run_then_the_mean():
    export = _vray_export()
    export["runs"] = [{"benchmark_name": "vray_benchmark", "status": "completed", "score": s, "unit": "vsamples",
                       "run_index": i, "is_warmup": i == 0, "is_test": False}
                      for i, s in enumerate((34745, 34347, 34214, 34347))]
    export["summaries"] = {"vray_benchmark": {"included": 3, "stats": {"mean": 34302.67}}}
    page = render_session_report(export)
    part = page[page.index("<h2>Summary</h2>"):]
    part = part[:part.index("</table>")]
    assert "<th>Each run</th><th>Mean</th>" in part
    assert ("Warm-up: 34,745.00 vsamples (not counted)<br>Run 1: 34,347.00 vsamples<br>Run 2: 34,214.00 vsamples<br>"
            "Run 3: 34,347.00 vsamples</td><td>34,302.67") in part


def test_the_unit_is_the_one_the_program_printed():
    out = "Testing V-Ray GPU... 100% 01:00/01:00\r\nV-Ray GPU score: 7902 vpaths\r\nAutomatic installation was successful!\r\n"
    assert vb.printed_unit(out, 7902) == "vpaths"
    assert vb.printed_unit(out, 5679) is None and vb.printed_unit("", 7902) is None

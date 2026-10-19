"""Cinebench 2026 workloads, its per-architecture install and the MD5 check, with fakes: no download, no run."""

import hashlib
import io
import threading
from pathlib import Path
from types import SimpleNamespace

from app.automation.workload import RunContext
from app.install import direct, plans
from app.frames.provision import download_verified
from app.pro import cinebench
from app.report import _cinebench_section


def test_maxons_command_and_the_score_line():
    command = cinebench.build_command(r"C:\tools\cinebench-2026-x64\Cinebench.exe", "g_CinebenchCpuXTest=true")
    assert command == ('cmd.exe /d /c start /b /wait "parentconsole" '
                       '"C:\\tools\\cinebench-2026-x64\\Cinebench.exe" g_CinebenchCpuXTest=true')
    assert cinebench.parse_score("Running...\nCB 2345.67 (0.00)\n") == (2345.67, None)
    assert cinebench.parse_score("CB 100 (x)\nCB 2101 (0.00)\n")[0] == 2101.0
    score, why = cinebench.parse_score("Loading\nLicense agreement\n")
    assert score is None and why == "no 'CB <score>' line in Cinebench's output (it ended with: License agreement)"
    assert cinebench.parse_score("")[1].endswith("(it printed nothing)")


def test_the_end_of_a_real_run():
    """The last lines of Single Thread on the owner's PC (2026-10-19, session 16), copied from its console."""
    tail = ("InitSceneSettings CPU1 (720.000,405.000) 1 128\nRendering...\n"
            "DURINGRENDERING Preparation time 10519.2 ms\nRENDERPROGRESSTYPE::AFTERRENDERING 733600.4 ms\n"
            "renderTime 733600.4 ms\nValues: {517.567} -> Avg/Deviation: 517.567/0.000\nCB 517.57 (0.00)\n")
    assert cinebench.parse_score(tail) == (517.57, None)
    multi = ("Values: {7943.592,7890.146,7859.734,7903.345} -> Avg/Deviation: 7899.204/30.114\n"
             "CB 7899.20 (0.00)\n")                                       # Multiple Threads, session 17
    assert cinebench.parse_score(multi) == (7899.2, None)
    assert all(w.verified for w in cinebench.cinebench_workloads())


def test_one_zip_per_architecture_and_no_linux():
    x64 = plans.plan_for("cinebench_2026", host=plans.Host("windows", "x64"))
    arm = plans.plan_for("cinebench_2026_single", host=plans.Host("windows", "arm64"))
    assert x64["url"].endswith("Cinebench2026_win_x86_64.zip") and x64["exe"] == "Cinebench.exe"
    assert arm["url"].endswith("Cinebench2026_win_arm64.zip") and arm["size"] == 783373346
    assert plans.plan_for("cinebench_2026", host=plans.Host("windows", "x86")) is None
    assert plans.plan_for("cinebench_2026", host=plans.Host("linux", "x64", "debian")) is None
    for plan in (x64, arm):
        assert direct.problem(plan) is None and "MD5" in direct.confirm_text("Cinebench 2026", plan)
    if cinebench.sys.platform == "win32":
        assert cinebench.program_plan("arm64")["folder"] == "cinebench-2026-arm64"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_a_vendor_md5_is_checked(tmp_path):
    data = b"cinebench zip bytes"
    opener = lambda req, timeout: _Resp(data)
    good = hashlib.md5(data).hexdigest()
    assert download_verified("https://x/a.zip", tmp_path / "a.zip", len(data), "", opener, md5=good) is None
    why = download_verified("https://x/b.zip", tmp_path / "b.zip", len(data), "", opener, md5="0" * 32)
    assert why == "download failed: MD5 differs from the published checksum" and not (tmp_path / "b.zip").exists()
    plan = {
        "url": "https://mx-app-blob-prod.maxon.net/a.zip", "hosts": ["mx-app-blob-prod.maxon.net"], "size": 1,
        "folder": "cb"}
    assert direct.problem(plan) == "no SHA-256 (or vendor MD5) pinned"


def _ctx(tmp_path):
    return RunContext(output_dir=str(tmp_path / "run"), timeout_seconds=60, stop_event=threading.Event())


def test_a_run_reads_the_points(tmp_path, monkeypatch):
    exe = tmp_path / "Cinebench.exe"
    exe.write_bytes(b"")
    seen = {}

    def fake_run(command, out, err, timeout, stop, cwd=None, **k):
        seen["command"], seen["cwd"], seen["console"] = command, cwd, k.get("hidden_console")
        Path(out).write_text("Cinebench 2026\nCB 2101.53 (0.00)\n", encoding="utf-8")
        Path(err).write_text("", encoding="utf-8")
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False, duration_seconds=612.0,
                               start_error=None)
    monkeypatch.setattr(cinebench, "run_process", fake_run)
    monkeypatch.setattr(cinebench.sys, "platform", "win32")
    w = cinebench.CinebenchWorkload("cinebench_2026_single", exe=exe)
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 2101.53 and r.unit == "pts"
    assert seen["command"][-2] == "--console-capture" and seen["cwd"] == str(tmp_path)
    assert seen["command"][-1] == cinebench.build_command(str(exe), "g_CinebenchCpu1Test=true")
    assert r.command == [seen["command"][-1]]          # the report shows Maxon's command, not the helper
    assert "Single Thread" in r.note
    assert seen["console"] is True          # Cinebench prints only into a console (none: nothing, 2026-10-19)


def test_the_bar_moves_while_it_runs(tmp_path, monkeypatch):
    """Owner, 2026-10-19: the green bar stood still for the whole 12 minutes."""
    exe = tmp_path / "Cinebench.exe"
    exe.write_bytes(b"")
    w = cinebench.CinebenchWorkload("cinebench_2026_single", exe=exe)
    seen = []

    def fake_run(command, out, err, timeout, stop, cwd=None, **k):
        clock[0] = 360.0
        seen.append(w.progress)
        clock[0] = 5000.0
        seen.append(w.progress)
        Path(out).write_text("CB 120 (0.00)\n", encoding="utf-8")
        Path(err).write_text("", encoding="utf-8")
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False, duration_seconds=1.0, start_error=None)
    clock = [0.0]
    monkeypatch.setattr(cinebench.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cinebench, "run_process", fake_run)
    monkeypatch.setattr(cinebench.sys, "platform", "win32")
    assert w.progress is None
    assert w.run(_ctx(tmp_path)).status == "completed"
    assert seen == [(0.5, 360.0), (0.97, 0.0)] and w.progress is None


def test_no_score_is_never_stored(tmp_path, monkeypatch):
    exe = tmp_path / "Cinebench.exe"
    exe.write_bytes(b"")

    def fake_run(command, out, err, timeout, stop, cwd=None, **k):
        Path(out).write_text("", encoding="utf-8")
        Path(err).write_text("", encoding="utf-8")
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False, duration_seconds=1.0, start_error=None)
    monkeypatch.setattr(cinebench, "run_process", fake_run)
    monkeypatch.setattr(cinebench.sys, "platform", "win32")
    r = cinebench.CinebenchWorkload("cinebench_2026", exe=exe).run(_ctx(tmp_path))
    assert r.status == "invalid" and r.score is None and "it printed nothing" in r.error
    monkeypatch.setattr(cinebench.sys, "platform", "linux")
    w = cinebench.CinebenchWorkload("cinebench_2026", exe=exe)
    assert w.run(_ctx(tmp_path)).status == "skipped" and "no Linux build" in w.availability()[1]


def test_its_own_report_section():
    export = {"session": {}, "runs": [
        {"benchmark_name": "cinebench_2026", "status": "completed", "is_warmup": False, "unit": "pts",
         "note": "Cinebench 2026; test: Multiple Threads"},
        {"benchmark_name": "cinebench_2026_single", "status": "invalid", "is_warmup": False,
         "note": "no 'CB <score>' line"}],
        "summaries": {"cinebench_2026": {"stats": {"mean": 40123.4}}}}
    html = _cinebench_section(export)
    assert "<h2>Cinebench 2026</h2>" in html and "40,123" in html and "no score" in html
    assert _cinebench_section({"runs": []}) == ""


def test_one_run_and_no_warm_up_whatever_the_settings(tmp_path):
    """Owner, 2026-10-19: Cinebench averages its own passes for about 10 min; 3 runs and a warm-up took 46 min."""
    from types import SimpleNamespace as NS
    from app.ui.automation_panel import AutomationPanel
    other = NS(id="sevenzip_benchmark", timeout_seconds=600)
    plan = AutomationPanel.build_plan(None, [cinebench.CinebenchWorkload("cinebench_2026", exe=tmp_path / "x.exe"),
                                             other], NS(warmup_runs=1, repetitions=3), extra_runs=2)
    assert [(p.warmup_runs, p.repetitions, p.max_extra_runs) for p in plan] == [(0, 1, 0), (1, 3, 2)]

"""PassMark PerformanceTest workloads with a fake elevated runner: no UAC prompt, nothing runs."""

import threading
from pathlib import Path

from app.automation.workload import RunContext
from app.frames.elevated import ElevatedOutcome
from app.install.plans import Host, plan_for
from app.pro import passmark
from app.report import _passmark_section


def test_the_script_and_reading_the_mark():
    text = passmark.script_text("CPU_ALL", Path(r"C:\run\r.csv"), Path(r"C:\run\r.txt"))
    assert text.split("\r\n")[1:7] == ["SUPPRESSWARNINGS", "CLEARRESULTS", "RUN CPU_ALL",
                                       'EXPORTCSV "C:\\run\\r.csv"', 'EXPORTTEXT "C:\\run\\r.txt"', "EXIT"]
    assert passmark.parse_mark("CPU Mark", "Test,Result\nCPU Mark,\"31,245.6\"\n") == (31245.6, None)
    assert passmark.parse_mark("CPU Mark", "Name,CPU Mark,Memory Mark\nThis PC,30123,2900\n") == (30123.0, None)
    assert passmark.parse_mark("3D Graphics Mark", "", "3D Graphics Mark: 25,001\n") == (25001.0, None)
    assert passmark.parse_mark("CPU Mark", "CPU Mark,0\n")[1] == "no 'CPU Mark' in PerformanceTest's results"
    assert passmark.parse_mark("CPU Mark", "", "") == (None, "PerformanceTest wrote no results file")


def _ctx(tmp_path):
    return RunContext(output_dir=str(tmp_path / "run"), timeout_seconds=60, stop_event=threading.Event())


def _exe(tmp_path):
    exe = tmp_path / "PerformanceTest64.exe"
    exe.write_bytes(b"")
    return exe


def test_a_run_reads_the_mark_from_its_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(passmark.sys, "platform", "win32")
    seen = {}

    def elevated(exe, args, cwd, timeout, stop, show=None):
        seen.update(exe=exe, args=args, cwd=cwd, show=show)
        script = Path(args[1]).read_text(encoding="ascii")
        assert "RUN ME_ALL" in script
        (Path(args[1]).parent / "passmark_results.csv").write_text("Memory Mark,3456.7\n", encoding="utf-8")
        return ElevatedOutcome("exited", exit_code=0, duration_seconds=120.0)
    w = passmark.PassMarkWorkload("passmark_memory", exe=_exe(tmp_path), elevated=elevated)
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 3456.7 and r.unit == "points" and not w.verified
    assert seen["args"][0] == "/s" and seen["args"][1].endswith("passmark_script.ptscript")
    assert seen["show"] == passmark.SW_SHOWNORMAL and seen["cwd"] == str(tmp_path)
    assert "sends" not in w.warning and "may send the results to PassMark" in w.warning


def test_no_mark_a_declined_prompt_and_linux_are_never_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(passmark.sys, "platform", "win32")
    exe = _exe(tmp_path)
    empty = passmark.PassMarkWorkload("passmark_cpu", exe=exe,
                                      elevated=lambda *a, **k: ElevatedOutcome("exited", exit_code=0))
    r = empty.run(_ctx(tmp_path))
    assert r.status == "invalid" and r.score is None and "wrote no results file" in r.error
    no = passmark.PassMarkWorkload("passmark_3d", exe=exe, elevated=lambda *a, **k: ElevatedOutcome("uac_declined"))
    r = no.run(_ctx(tmp_path))
    assert r.status == "skipped" and r.score is None and "declined" in r.error
    monkeypatch.setattr(passmark.sys, "platform", "linux")
    assert passmark.PassMarkWorkload("passmark_cpu", exe=exe).run(_ctx(tmp_path)).status == "skipped"


def test_install_and_the_report_section():
    for tid in passmark.MODES:
        assert plan_for(tid, host=Host("windows", "x64"))["winget"] == "PassMark.PerformanceTest"
    export = {"session": {}, "runs": [
        {"benchmark_name": "passmark_cpu", "status": "completed", "is_warmup": False, "unit": "points",
         "note": "PassMark PerformanceTest; test: CPU Mark (CPU_ALL)"}],
        "summaries": {"passmark_cpu": {"stats": {"mean": 31245.6}}}}
    html = _passmark_section(export)
    assert "<h2>PassMark PerformanceTest</h2>" in html and "31,246" in html and "may send the results" in html

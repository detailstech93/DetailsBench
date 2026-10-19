"""PassMark PerformanceTest workloads with a fake administrator helper: no UAC prompt, nothing runs."""

import threading
from pathlib import Path

from app.automation.workload import RunContext
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


class FakeHelper:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def passmark(self, group, timeout, stop=None):
        self.asked.append(group)
        return self.answer


def test_a_run_reads_the_mark_the_helper_sends_back(tmp_path, monkeypatch):
    monkeypatch.setattr(passmark.sys, "platform", "win32")
    helper = FakeHelper({"op": "done", "status": "exited", "exit_code": 0, "duration_seconds": 120.0,
                         "csv": "Memory Mark,3456.7\n", "txt": ""})
    w = passmark.PassMarkWorkload("passmark_memory", exe=_exe(tmp_path), helper=helper)
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 3456.7 and r.unit == "points" and not w.verified
    assert helper.asked == ["ME_ALL"] and w.needs_admin
    assert (tmp_path / "run" / "passmark_results.csv").read_text(encoding="utf-8") == "Memory Mark,3456.7\n"
    assert "may send the results to PassMark" in w.warning and "once" in w.warning


def test_no_mark_a_declined_prompt_and_linux_are_never_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(passmark.sys, "platform", "win32")
    exe = _exe(tmp_path)
    empty = passmark.PassMarkWorkload("passmark_cpu", exe=exe, helper=FakeHelper({"status": "exited",
                                                                                  "exit_code": 0}))
    r = empty.run(_ctx(tmp_path))
    assert r.status == "invalid" and r.score is None and "wrote no results file" in r.error
    from app.frames import admin_helper
    monkeypatch.setattr(admin_helper.AdminHelper, "start", lambda self: "the administrator prompt was declined")
    r = passmark.PassMarkWorkload("passmark_3d", exe=exe).run(_ctx(tmp_path))
    assert r.status == "skipped" and r.score is None and "declined" in r.error
    refused = passmark.PassMarkWorkload("passmark_2d", exe=exe, helper=FakeHelper({"status": "refused",
                                                                                   "error": "no"}))
    assert refused.run(_ctx(tmp_path)).status == "failed"
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

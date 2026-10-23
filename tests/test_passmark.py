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
    r = passmark.PassMarkWorkload("passmark_3d", exe=exe).run(_ctx(tmp_path))
    assert r.status == "skipped" and "CPU and Memory tests only" in r.error
    r = passmark.PassMarkWorkload("passmark_cpu", exe=exe, ncurses=lambda: False).run(_ctx(tmp_path))
    assert r.status == "skipped" and "ncurses 5" in r.error
    monkeypatch.setattr(passmark.sys, "platform", "darwin")
    assert passmark.PassMarkWorkload("passmark_cpu", exe=exe).run(_ctx(tmp_path)).status == "skipped"


LINUX_YML = """Version:
  Major: 11
  Minor: 0
  Build: 1004
Results:
  Results Complete: true
  NumTestProcesses: 16
  CPU_INTEGER_MATH: 51234.5
  SUMM_CPU: 30123.4
  SUMM_ME: 2890.1
"""


def test_reading_the_linux_results_file():
    assert passmark.parse_linux_results("SUMM_CPU", LINUX_YML) == (30123.4, None)
    assert passmark.parse_linux_results("SUMM_ME", LINUX_YML) == (2890.1, None)
    assert passmark.parse_linux_results("SUMM_ME", "Results:\n  SUMM_CPU: 1\n")[1] == "no SUMM_ME in " \
                                                                                     "PerformanceTest's results"
    assert passmark.parse_linux_results("SUMM_CPU", "")[1] == "PerformanceTest wrote no results file"
    half = LINUX_YML.replace("Results Complete: true", "Results Complete: false")
    assert passmark.parse_linux_results("SUMM_CPU", half) == (None, "PerformanceTest says its results are not "
                                                                    "complete")
    assert passmark.parse_linux_results("SUMM_CPU", "Results:\n  SUMM_CPU: 0\n")[0] is None


def test_a_linux_run_uses_autorun_in_the_run_folder(tmp_path, monkeypatch):
    from app.automation.process import ProcessOutcome
    monkeypatch.setattr(passmark.sys, "platform", "linux")
    seen = {}

    def fake_run(command, out, err, timeout, stop, cwd=None, env=None, **kw):
        seen.update(command=command, cwd=cwd, env=env)
        Path(out).write_text("", encoding="utf-8")
        Path(err).write_text("", encoding="utf-8")
        (Path(cwd) / "results_memory.yml").write_text(LINUX_YML, encoding="utf-8")
        return ProcessOutcome(0, False, False, 95.0)
    monkeypatch.setattr(passmark, "run_process", fake_run)
    exe = tmp_path / "PerformanceTest" / "PerformanceTest_Linux_x86-64"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    w = passmark.PassMarkWorkload("passmark_memory", exe=exe, ncurses=lambda: True)
    assert not w.needs_admin and "does not send them" in w.warning and w.availability()[0]
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 2890.1 and r.unit == "points" and not w.verified
    assert seen["command"] == [str(exe), "-r", "2"] and Path(seen["cwd"]) == (tmp_path / "run").resolve()
    assert seen["env"]["TERM"] and "does not upload" in r.note
    cpu = passmark.PassMarkWorkload("passmark_cpu", exe=exe, ncurses=lambda: True)
    monkeypatch.setattr(passmark, "run_process", lambda *a, **k: ProcessOutcome(1, False, False, 2.0))
    r = cpu.run(RunContext(output_dir=str(tmp_path / "run2"), timeout_seconds=60, stop_event=threading.Event()))
    assert r.status == "invalid" and r.score is None and "wrote no results file" in r.error


def test_linux_install_unpacks_the_pinned_zip_then_ncurses(tmp_path):
    for arch, exe in (("x64", "PerformanceTest_Linux_x86-64"), ("arm64", "PerformanceTest_Linux_ARM64")):
        for tid in ("passmark_cpu", "passmark_memory"):
            plan = plan_for(tid, host=Host("linux", arch, "debian"))
            assert plan["kind"] == "zip" and plan["exe"] == "PerformanceTest/" + exe and plan["then"] == "ncurses5"
            assert len(plan["sha256"]) == 64 and plan["hosts"] == ["www.passmark.com"]
    assert plan_for("passmark_3d", host=Host("linux", "x64", "debian")) is None
    assert plan_for("passmark_cpu", host=Host("linux", "x86", "debian")) is None
    from app.ui.install_flow import THEN
    assert "ncurses5" in THEN
    lib = tmp_path / "usr" / "lib" / "x86_64-linux-gnu"
    lib.mkdir(parents=True)
    assert not passmark.ncurses5_present([str(tmp_path / "usr" / "lib")])
    (lib / "libncurses.so.5.9").write_bytes(b"")
    assert passmark.ncurses5_present([str(tmp_path / "usr" / "lib"), str(tmp_path / "missing")])


def test_ncurses_is_installed_only_when_missing_and_known():
    ran = []
    done = passmark.install_ncurses5(present=lambda: True, run=lambda argv: ran.append(argv))
    assert done.ok and not ran
    commands = lambda family, pkg: ([["/usr/bin/pkexec", "/usr/bin/dnf", "install", "-y", pkg]], None)
    out = passmark.install_ncurses5(present=lambda: False, family=lambda: "fedora", commands=commands,
                                    run=lambda argv: ran.append(argv) or "done")
    assert out.ok and ran == [[["/usr/bin/pkexec", "/usr/bin/dnf", "install", "-y", "ncurses-compat-libs"]]]
    out = passmark.install_ncurses5(present=lambda: False, family=lambda: "debian", commands=commands,
                                    run=lambda argv: "cancelled (password prompt declined)")
    assert not out.ok and "libncurses5 was not installed" in out.message
    out = passmark.install_ncurses5(present=lambda: False, family=lambda: "arch")
    assert not out.ok and "cannot install" in out.message
    assert "libncurses5" in passmark.ncurses_install_text(present=lambda: False, family=lambda: "debian")
    assert "already installed" in passmark.ncurses_install_text(present=lambda: True)


def test_install_and_the_report_section():
    for tid in passmark.MODES:
        assert plan_for(tid, host=Host("windows", "x64"))["winget"] == "PassMark.PerformanceTest"
    export = {"session": {}, "runs": [
        {"benchmark_name": "passmark_cpu", "status": "completed", "is_warmup": False, "unit": "points",
         "note": "PassMark PerformanceTest; test: CPU Mark (CPU_ALL)"}],
        "summaries": {"passmark_cpu": {"stats": {"mean": 31245.6}}}}
    html = _passmark_section(export)
    assert "<h2>PassMark PerformanceTest</h2>" in html and "31,246" in html and "may send the results" in html


def test_performancetest_11_results_read_this_computer_only():
    """The format PerformanceTest 11 wrote on the owner's PC (2026-10-19, session 12): a header with
    "CPU Mark (Composite average)", seven baseline rows of other computers, then "This Computer" with a percentile."""
    header = "Computer,Integer Math (MOps./Sec.),CPU Mark (Composite average),Memory Mark (Composite average)"
    rows = [header, "Baseline #1,45488.3,13800.2,3300.1", "Baseline #2,91636.8,29850.7,3144.2",
            '"This Computer",155663.9 (92%),61060.5 (98%),N/A']
    text = "\n".join(rows) + "\n"
    assert passmark.parse_mark("CPU Mark", text) == (61060.5, None)          # never a baseline's 13800.2
    assert passmark.parse_mark("Memory Mark", text)[0] is None                 # N/A is not a score
    only_baselines = "\n".join(rows[:3]) + "\n"
    assert passmark.parse_mark("CPU Mark", only_baselines)[0] is None


def test_a_linux_release_without_ncurses_5_hides_passmark(tmp_path, monkeypatch):
    """Ubuntu 24.04 (owner's WSL, 2026-10-20) has no libncurses5: Install cannot help, so the test is hidden."""
    monkeypatch.setattr(passmark.sys, "platform", "linux")
    exe = _exe(tmp_path)
    noble = {"ID": "ubuntu", "VERSION_ID": "24.04"}
    missing = passmark.PassMarkWorkload("passmark_cpu", exe=exe, ncurses=lambda: False)
    assert "no longer ships" in missing.system_blocks(noble)
    assert missing.system_blocks({"ID": "ubuntu", "VERSION_ID": "22.04"}) is None       # Install puts it there
    assert passmark.PassMarkWorkload("passmark_cpu", exe=exe, ncurses=lambda: True).system_blocks(noble) is None
    assert "CPU and Memory tests only" in passmark.PassMarkWorkload("passmark_3d", exe=exe).system_blocks(noble)
    monkeypatch.setattr(passmark.sys, "platform", "win32")
    assert passmark.PassMarkWorkload("passmark_cpu", exe=exe).system_blocks(noble) is None
    assert passmark.ncurses5_unavailable({"ID": "debian", "VERSION_ID": "13"})
    assert not passmark.ncurses5_unavailable({"ID": "debian", "VERSION_ID": "12"})
    assert passmark.ncurses5_unavailable({"ID": "linuxmint", "ID_LIKE": "ubuntu debian", "UBUNTU_CODENAME": "noble"})
    assert not passmark.ncurses5_unavailable({"ID": "linuxmint", "ID_LIKE": "ubuntu debian", "UBUNTU_CODENAME": "jammy"})
    assert not passmark.ncurses5_unavailable({"ID": "fedora", "VERSION_ID": "41"})
    assert passmark.ncurses5_unavailable({"ID": "arch"})


def test_windows_arm_uses_passmarks_arm64_program(tmp_path):
    # Snapdragon, 2026-10-22: PerformanceTest installed but its tests stayed "not installed" (no PerformanceTest64.exe)
    folder = tmp_path / "PerformanceTest"
    folder.mkdir()
    (folder / "PerformanceTestARM64.exe").write_bytes(b"")
    env = {"ProgramFiles": str(tmp_path)}
    assert passmark.program_path("win32", "ARM64", env) == folder / "PerformanceTestARM64.exe"
    assert passmark.program_path("win32", "AMD64", env) is None
    (folder / "PerformanceTest64.exe").write_bytes(b"")
    assert passmark.program_path("win32", "AMD64", env) == folder / "PerformanceTest64.exe"
    assert passmark.program_path("linux", "ARM64", env) is None


def test_passmark_3d_is_hidden_on_windows_arm(monkeypatch, tmp_path):
    """The Snapdragon board: three tries of 15 minutes with no output (owner, 2026-10-23: hide it there)."""
    monkeypatch.setattr(passmark.sys, "platform", "win32")
    exe = tmp_path / "PerformanceTestARM64.exe"
    exe.write_bytes(b"")
    w = passmark.PassMarkWorkload("passmark_3d", exe=exe)
    w._linux = False
    assert "does not start on Windows ARM" in w.system_blocks(arch="arm64")
    assert w.system_blocks(arch="x64") is None
    cpu = passmark.PassMarkWorkload("passmark_cpu", exe=exe)
    cpu._linux = False
    assert cpu.system_blocks(arch="arm64") is None

"""y-cruncher: install plans (zip / tar.xz), license asked in the app, command, result reading and the report
(no download, no real program)."""

import io
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace

from app.automation.workload import RunContext
from app.install import direct, plans
from app.pro import ycruncher as yc
from app.report import _ycruncher_section

# the line Phoronix Test Suite's y-cruncher profile reads (its results-definition.xml)
OUTPUT = ("Benchmark Pi\nTotal Computation Time:     41.917 seconds  ( 0.699 minutes )\n"
          "Start-to-End Wall Time:     43.188 seconds  ( 0.720 minutes )\n")


def test_pinned_on_the_authors_server_for_windows_and_linux_x64():
    win = plans.plan_for("ycruncher_pi_1b", host=plans.Host("windows", "x64"))
    lin = plans.plan_for("ycruncher_pi_2_5b", host=plans.Host("linux", "x64", "debian"))
    assert win["url"].endswith("y-cruncher%20v0.8.7.9547b.zip") and win["exe"].endswith("/y-cruncher.exe")
    assert lin["url"].endswith("-static.tar.xz") and lin["size"] == 35954568
    assert plans.plan_for("ycruncher_pi_1b", host=plans.Host("linux", "arm64", "debian")) is None
    for plan in (win, lin):
        assert direct.problem(plan) is None and "SHA-256" in direct.confirm_text("y-cruncher", plan)
    assert direct.archive_suffix(lin["url"]) == ".tar.xz"


def test_a_tar_xz_is_extracted(tmp_path):
    archive = tmp_path / "y.tar.xz"
    with tarfile.open(archive, "w:xz") as t:
        data = b"#!/bin/sh\n"
        info = tarfile.TarInfo("y-cruncher v1-static/y-cruncher")
        info.size, info.mode = len(data), 0o755
        t.addfile(info, io.BytesIO(data))
    assert direct.extract(archive, tmp_path / "out", "y-cruncher v1-static/y-cruncher") is None
    assert (tmp_path / "out" / "y-cruncher v1-static" / "y-cruncher").read_bytes() == b"#!/bin/sh\n"


def test_license_is_kept_in_settings(tmp_path):
    assert not yc.license_accepted(tmp_path)
    yc.accept_license(tmp_path)
    assert yc.license_accepted(tmp_path) and yc.LICENSE_URL in yc.license_question()
    assert "technology reviews" in yc.license_question()


def test_the_documented_command_and_the_time_line():
    assert yc.build_command("/t/y-cruncher", "1b", "/run") == [
        "/t/y-cruncher", "skip-warnings", "pause:-2", "colors:0", "status:none",
        "logfile:" + str(Path("/run") / "ycruncher_log.txt"), "bench", "1b", "-o", "/run", "-od:0"]
    seconds, details, why = yc.parse_result(OUTPUT)
    assert seconds == 41.917 and why is None and details["Start-to-End Wall Time"] == "43.188 seconds"
    assert yc.parse_result("Not enough memory\n")[2] == ("no 'Total Computation Time' line in y-cruncher's output "
                                                          "(it ended with: Not enough memory)")


def _ctx(tmp_path):
    return RunContext(output_dir=str(tmp_path / "run"), timeout_seconds=60, stop_event=threading.Event())


def _fake(write, exit_code=0):
    seen = {}

    def run(command, out, err, timeout, stop, cwd=None, **k):
        seen["command"], seen["cwd"] = command, cwd
        Path(out).write_text("", encoding="utf-8")
        Path(err).write_text("", encoding="utf-8")
        write(Path(command[command.index("-o") + 1]), Path(out))
        return SimpleNamespace(exit_code=exit_code, timed_out=False, cancelled=False, duration_seconds=50.0,
                               start_error=None)
    return run, seen


def test_a_run_reads_the_seconds_from_any_of_its_files(tmp_path, monkeypatch):
    exe = tmp_path / "y-cruncher.exe"
    exe.write_bytes(b"")
    yc.accept_license(tmp_path)
    for where in ("log", "stdout", "validation"):
        def write(folder, out, where=where):
            target = {"log": folder / "ycruncher_log.txt", "stdout": out,
                      "validation": folder / "Pi - 20261019-101500.txt"}[where]
            target.write_text(OUTPUT, encoding="utf-8")
        run, seen = _fake(write)
        monkeypatch.setattr(yc, "run_process", run)
        ctx = RunContext(output_dir=str(tmp_path / where), timeout_seconds=60, stop_event=threading.Event())
        w = yc.YCruncherWorkload("ycruncher_pi_2_5b", exe=exe, data_dir=tmp_path)
        r = w.run(ctx)
        assert r.status == "completed" and r.score == 41.917 and r.unit == "s" and not w.verified, where
        assert seen["command"][7] == "2.5b" and seen["cwd"] == str(tmp_path)
        assert "Pi, 2.5 billion digits" in r.note


def test_no_time_no_license_or_a_missing_runtime_store_no_score(tmp_path, monkeypatch):
    exe = tmp_path / "y-cruncher.exe"
    exe.write_bytes(b"")
    w = yc.YCruncherWorkload(exe=exe, data_dir=tmp_path)
    r = w.run(_ctx(tmp_path))
    assert r.status == "skipped" and "license" in r.error
    yc.accept_license(tmp_path)
    run, _ = _fake(lambda folder, out: out.write_text("Not enough memory\n", encoding="utf-8"))
    monkeypatch.setattr(yc, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "invalid" and r.score is None and "Not enough memory" in r.error
    run, _ = _fake(lambda folder, out: None, exit_code=-1073741515)
    monkeypatch.setattr(yc, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "failed" and "Visual C++" in r.error
    assert yc.YCruncherWorkload(exe=None).run(_ctx(tmp_path)).status == "skipped"


def test_its_own_report_section_keeps_the_decimals():
    export = {"session": {}, "runs": [
        {"benchmark_name": "ycruncher_pi_1b", "status": "completed", "is_warmup": False, "unit": "s",
         "note": "y-cruncher: 0.8.7.9547; Test: Pi, 1 billion digits, all threads"}],
        "summaries": {"ycruncher_pi_1b": {"stats": {"mean": 41.9173}}}}
    html = _ycruncher_section(export)
    assert "<h2>y-cruncher</h2>" in html and "41.917 <span>s</span>" in html and "Lower is faster" in html
    assert _ycruncher_section({"runs": []}) == ""

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
        seen["command"], seen["cwd"] = command, cwd
        Path(out).write_text("Cinebench 2026\nCB 2101.53 (0.00)\n", encoding="utf-8")
        Path(err).write_text("", encoding="utf-8")
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False, duration_seconds=612.0,
                               start_error=None)
    monkeypatch.setattr(cinebench, "run_process", fake_run)
    monkeypatch.setattr(cinebench.sys, "platform", "win32")
    w = cinebench.CinebenchWorkload("cinebench_2026_single", exe=exe)
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 2101.53 and r.unit == "pts" and not w.verified
    assert seen["command"].endswith("g_CinebenchCpu1Test=true") and seen["cwd"] == str(tmp_path)
    assert "Single Thread" in r.note


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

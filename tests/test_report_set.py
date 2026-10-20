"""One report per test, a folder per run of several tests and a final report (owner, 2026-10-20)."""

from app.database import Database
from app.export import build_session_export
from app import report_set as rs


def _session(tmp_path, tests):
    db = Database(str(tmp_path / "r.db"))
    sid = db.create_session("Automated")
    for name, score, status, seconds in tests:
        db.create_result(sid, name, "cpu", score, unit="pts", status=status, run_index=1,
                         note=None if status == "completed" else "exit code 3; more", duration_seconds=seconds)
    export = build_session_export(db, sid)
    export["session"]["created_at"] = "2026-10-20T09:15:30.123456"
    return db, sid, export


def test_names_and_times():
    assert rs.stamp("2026-10-20T09:15:30.123456") == "2026-10-20_09-15-30"
    assert rs.safe_name("Cinebench 2026 (Single Thread)") == "Cinebench 2026 Single Thread"
    assert rs.safe_name("a/b\\c:d*?") == "a b c d"
    assert rs.duration_text(372) == "6 min 12 s" and rs.duration_text(42) == "42 s"
    assert rs.duration_text(3 * 3600 + 120) == "3 h 2 min" and rs.duration_text(None) == "-"


def test_one_test_is_one_report_named_after_it(tmp_path):
    _, _, export = _session(tmp_path, [("cinebench_2026", 7899.2, "completed", 680.0)])
    path = rs.write_report_set(export, tmp_path / "reports")
    assert path == tmp_path / "reports" / "cinebench_2026_2026-10-20_09-15-30.html"
    assert path.is_file() and [p.name for p in (tmp_path / "reports").iterdir()] == [path.name]


def test_several_tests_get_a_folder_a_report_each_and_a_summary(tmp_path):
    _, _, export = _session(tmp_path, [("cinebench_2026", 7899.2, "completed", 680.0),
                                       ("geekbench7_cpu", None, "failed", 30.0),
                                       ("ycruncher_pi_1b", 21.5, "completed", 40.0)])
    path = rs.write_report_set(export, tmp_path / "reports")
    folder = tmp_path / "reports" / "2026-10-20_09-15-30"
    assert path == folder / "Summary_2026-10-20_09-15-30.html"
    assert sorted(p.name for p in folder.iterdir()) == [
        "Summary_2026-10-20_09-15-30.html", "cinebench_2026_2026-10-20_09-15-30.html",
        "geekbench7_cpu_2026-10-20_09-15-30.html", "ycruncher_pi_1b_2026-10-20_09-15-30.html"]
    own = (folder / "cinebench_2026_2026-10-20_09-15-30.html").read_text(encoding="utf-8")
    assert "cinebench_2026" in own and "ycruncher_pi_1b" not in own and "geekbench7_cpu" not in own
    summary = path.read_text(encoding="utf-8")
    assert "Environment" in summary or "environment" in summary.lower()
    assert '<a href="cinebench_2026_2026-10-20_09-15-30.html">' in summary
    assert "FAILED" in summary and "exit code 3" in summary and "7,899.2 pts" in summary
    assert "11 min 20 s" in summary and "2 <span>of 3</span>" in summary and "12 min 30 s" in summary


def test_outcome_counts_runs_and_warmups(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    sid = db.create_session("x")
    db.create_result(sid, "t", "cpu", 10.0, unit="pts", is_warmup=True, run_index=1, duration_seconds=5)
    db.create_result(sid, "t", "cpu", 12.0, unit="pts", run_index=1, duration_seconds=6)
    db.create_result(sid, "t", "cpu", 14.0, unit="pts", run_index=2, duration_seconds=7)
    o = rs.test_outcome(build_session_export(db, sid), "t")
    assert o["passed"] and o["result"] == "13 pts (average of 2 runs)"
    assert o["runs"] == "2 of 2 measured run(s) completed, 1 warm-up" and o["time"] == "18 s"


def test_the_same_second_twice_does_not_overwrite(tmp_path):
    _, _, export = _session(tmp_path, [("a", 1.0, "completed", 1), ("b", 2.0, "completed", 1)])
    first = rs.write_report_set(export, tmp_path / "reports")
    second = rs.write_report_set(export, tmp_path / "reports")
    assert first.parent.name == "2026-10-20_09-15-30" and second.parent.name == "2026-10-20_09-15-30_2"


def test_the_phone_lists_reports_inside_run_folders():
    from app.ui.remote_control import REPORT_NAME
    assert REPORT_NAME.match("2026-10-20_09-15-30/Summary_2026-10-20_09-15-30.html")
    assert REPORT_NAME.match("Cinebench 2026 Single Thread_2026-10-20_09-15-30.html")
    assert not REPORT_NAME.match("a/b/c.html") and not REPORT_NAME.match("../x.txt")

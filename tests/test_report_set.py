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


def test_each_test_is_saved_when_it_is_over_and_the_end_uses_the_same_folder(tmp_path):
    _, _, export = _session(tmp_path, [("a", 1.0, "completed", 1), ("b", 2.0, "completed", 1)])
    assert rs.finished_tests(export, "b: run 1") == ["a"]          # b is still going on
    assert rs.finished_tests(export, "c: run 1") == ["a", "b"]
    early = rs.write_test_report(export, tmp_path / "reports", "a")
    assert early == tmp_path / "reports" / "2026-10-20_09-15-30" / "a_2026-10-20_09-15-30.html"
    summary = rs.write_report_set(export, tmp_path / "reports")
    assert summary.parent == early.parent                          # no second folder
    assert sorted(p.name for p in early.parent.iterdir()) == [
        "Summary_2026-10-20_09-15-30.html", "a_2026-10-20_09-15-30.html", "b_2026-10-20_09-15-30.html"]


def test_the_phone_lists_reports_inside_run_folders():
    from app.ui.remote_control import REPORT_NAME
    assert REPORT_NAME.match("2026-10-20_09-15-30/Summary_2026-10-20_09-15-30.html")
    assert REPORT_NAME.match("Cinebench 2026 Single Thread_2026-10-20_09-15-30.html")
    assert not REPORT_NAME.match("a/b/c.html") and not REPORT_NAME.match("../x.txt")


class _Label:
    def setText(self, text):
        self.text = text


class _Page:
    def watch(self, *a):
        pass

    def offer_report(self, fn):
        self.offered = fn


class _Panel:
    def __init__(self, db, sid):
        self.step, self.last_session_id, self.db, self.sid = (0, 4, ""), sid, db, sid
        self.on_plan_finished = self.on_step_report = None
        self.lhm = type("L", (), {"active": lambda self: False})()

    def start_plan(self, **kw):
        return True

    def emergency_stop(self):
        pass


def test_a_plan_of_several_tests_saves_each_report_and_opens_only_the_summary(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.ui import reports_folder, run_tests
    db = Database(str(tmp_path / "r.db"))
    sid = db.create_session("Automated")
    panel = _Panel(db, sid)
    window = SimpleNamespace(db=db, automation_panel=panel, status_label=_Label(), tests_page=_Page(),
                             selected_profile=lambda: None)
    monkeypatch.setattr(reports_folder, "folder", lambda w: tmp_path / "reports")
    monkeypatch.setattr(run_tests, "plan_profile", lambda profile, choice: None)
    opened = []
    entries = [SimpleNamespace(ref="a"), SimpleNamespace(ref="b")]
    choice = SimpleNamespace(extra_runs=0)
    assert run_tests.launch(window, entries, choice, opener=opened.append)
    db.create_result(sid, "a", "cpu", 5.0, unit="pts", run_index=1, duration_seconds=3)
    panel.on_step_report(1, 2, "b: run 1")                      # a is over, b starts
    stamp = rs.stamp(build_session_export(db, sid)["session"]["created_at"])
    folder = tmp_path / "reports" / stamp
    assert [p.name for p in folder.iterdir()] == [f"a_{stamp}.html"] and opened == []    # saved, not opened
    db.create_result(sid, "b", "cpu", None, unit="pts", status="failed", run_index=1, note="exit code 2",
                     duration_seconds=4)
    panel.on_plan_finished(sid, "Session done: 1 completed, 1 failed.")
    assert opened == [folder / f"Summary_{stamp}.html"]          # only the final report opens
    assert sorted(p.name for p in folder.iterdir()) == [f"Summary_{stamp}.html", f"a_{stamp}.html", f"b_{stamp}.html"]


def test_a_name_with_a_colon_is_one_test(tmp_path):
    """Stress tests and llama-benchy have ': ' in their names (real run 2026-10-20: their reports waited for the end)."""
    assert rs.test_of_step("Stress test: CPU: run 1") == "Stress test: CPU"
    assert rs.test_of_step("Stress test: CPU + GPU together: warmup 0 (retry)") == "Stress test: CPU + GPU together"
    assert rs.test_of_step("llama-benchy: Gemma 4 12B (Q4_0): run 2 completed (84.7 tok/s)") == \
        "llama-benchy: Gemma 4 12B (Q4_0)"
    assert rs.test_of_step("7-Zip: run 3 failed (could not start: no file)") == "7-Zip"
    assert rs.test_of_step("Pause 30 s before the next run") == "Pause 30 s before the next run"
    db = Database(str(tmp_path / "r.db"))
    sid = db.create_session("x")
    db.create_result(sid, "stress_cpu", "cpu", 97.0, unit="%", run_index=1)
    export = build_session_export(db, sid)
    export["manifest"] = dict(export.get("manifest") or {}, benchmarks=[{"name": "stress_cpu", "display_name": "Stress test: CPU"}])
    assert rs.finished_tests(export, "Stress test: CPU: run 1") == []
    assert rs.finished_tests(export, "Stress test: GPU: run 1") == ["stress_cpu"]

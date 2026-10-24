"""Automation panel with REAL child processes and a REAL temporary database (no confirmation dialogs)."""

import sys
import threading
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.automation.cli_workload import CliWorkload, load_workloads
from app.automation.tool_spec import validate_spec
from app.database import Database
from app.profiles import RunProfile
from app.ui.automation_panel import AutomationPanel
from app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def workload(wid, code, timeout=20):
    spec, errors = validate_spec({"id": wid, "display_name": wid, "executable": {"python": True},
                                  "args": ["-c", code], "timeout_seconds": timeout,
                                  "parser": {"type": "regex", "pattern": r"^SCORE=(?P<score>\S+)$"},
                                  "verified": True})
    assert not errors
    return CliWorkload(spec)


def make_panel(tmp_path, workloads, errors=(), confirm=True, profile=None):
    db = Database(str(tmp_path / "a.db"))
    profile = profile or RunProfile(name="quick", repetitions=2, warmup_runs=1, cooldown_seconds=0)
    panel = AutomationPanel(db, lambda: profile, lambda: {"os": "test"},
                            loader=lambda: (list(workloads), list(errors)), output_root=tmp_path / "runs")
    panel.confirm = lambda text: (panel.__dict__.setdefault("asked", []).append(text), confirm)[1]
    return panel, db


def finish(qapp, panel):
    assert panel.worker.wait(30000)
    qapp.processEvents()


def test_lists_tools_and_spec_errors(qapp, tmp_path):
    missing = CliWorkload(validate_spec({"id": "gone", "display_name": "Gone", "timeout_seconds": 5,
                                         "executable": {"candidates": ["C:\\missing\\x.exe"]},
                                         "parser": {"type": "last_number"}, "verified": False})[0],
                          exists=lambda p: False, which=lambda n: None)
    panel, _ = make_panel(tmp_path, [workload("selftest_a", "print(1)"), missing], ["bad.json: id is required"])
    assert panel.table.rowCount() == 2
    assert panel.table.item(0, 1).text() == "selftest_a [test]"
    assert panel.table.item(1, 2).text() == "not found" and panel.table.item(1, 3).text() == "-"
    assert "bad.json: id is required" in panel.errors_label.text()
    panel.set_checked("gone")     # not checkable: never selected
    assert panel.selected_workloads() == []


def test_shipped_specs_listed(qapp, tmp_path):
    panel, _ = make_panel(tmp_path, *load_workloads())
    assert "selftest_python" in [w.id for w in panel.workloads]
    assert panel.errors_label.text() == ""


def test_nothing_selected_or_declined_runs_nothing(qapp, tmp_path):
    panel, db = make_panel(tmp_path, [workload("selftest_a", "print('SCORE=1')")], confirm=False)
    assert panel.start_plan() is False and "Tick at least one" in panel.status_label.text()
    panel.set_checked("selftest_a")
    assert panel.start_plan() is False and panel.status_label.text() == "Cancelled; nothing was run."
    assert "1 warmup + 2 measured runs" in panel.asked[0] and sys.executable in panel.asked[0]
    assert db.get_sessions(include_test=True) == []


def test_plan_runs_in_new_test_session(qapp, tmp_path):
    panel, db = make_panel(tmp_path, [workload("selftest_a", "print('SCORE=7.5')")])
    panel.set_checked("selftest_a")
    assert panel.start_plan() is True
    assert panel.stop_btn.isEnabled() and not panel.run_btn.isEnabled()
    finish(qapp, panel)
    sid = panel.last_session_id
    runs = db.get_runs(sid, include_test=True)
    assert [(r["run_index"], r["is_warmup"], r["score"], r["status"]) for r in runs] == \
        [(0, 1, 7.5, "completed"), (1, 0, 7.5, "completed"), (2, 0, 7.5, "completed")]
    assert db.get_runs(sid) == []                      # test data stays out of normal statistics
    session = db.get_session(sid)
    assert session["is_test"] and not session["is_manual"]
    manifest = db.get_session_manifest(sid)
    assert manifest["benchmarks"][0]["name"] == "selftest_a"
    assert panel.status_label.text() == f"Session #{sid} done: 3 completed.\nselftest_a: mean 7.5 points over 2 run(s), CV 0.0%"
    assert not (tmp_path / "runs" / "unfinished_plan.json").exists() and not panel.resume_btn.isVisible()
    assert panel.run_btn.isEnabled() and not panel.stop_btn.isEnabled()


def test_emergency_stop(qapp, tmp_path):
    panel, db = make_panel(tmp_path, [workload("tool_slow", "import time; time.sleep(30)")],
                           profile=RunProfile(name="p", repetitions=3, warmup_runs=0))
    panel.set_checked("tool_slow")
    assert panel.start_plan()
    threading.Timer(0.5, panel.emergency_stop).start()
    finish(qapp, panel)
    runs = db.get_runs(panel.last_session_id, include_test=True)
    assert [(r["status"], r["score"]) for r in runs] == [("cancelled", None)]
    assert not db.get_session(panel.last_session_id)["is_test"]
    assert "Stopped; 2 run(s) not started." in panel.status_label.text()


def test_failure_gets_screenshot_when_enabled(qapp, tmp_path):
    panel, db = make_panel(tmp_path, [workload("tool_bad", "raise SystemExit(3)")],
                           profile=RunProfile(name="p", repetitions=1, warmup_runs=0))
    panel.set_checked("tool_bad")
    panel.screenshot_box.setChecked(True)
    calls = []
    panel._on_screenshot_requested = lambda path, done: (calls.append(path), done.set())
    assert panel.start_plan()
    while panel.worker.isRunning():        # the screenshot request needs the GUI thread
        qapp.processEvents()
    qapp.processEvents()
    runs = db.get_runs(panel.last_session_id, include_test=True)
    assert [r["status"] for r in runs] == ["failed", "failed"]           # the failed run is run once more
    assert runs[1]["note"].startswith("Retry after a failed try (failed:")
    assert len(calls) == 2 and calls[0].endswith("screenshot.png") and "retry1" in calls[1]


def test_main_window_has_panel_and_shuts_it_down(qapp, tmp_path):
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {})
    assert w.automation_panel.output_root == (tmp_path / "automation_runs").resolve()
    with patch.object(w.automation_panel, "shutdown") as shutdown:
        w.close()
    shutdown.assert_called_once()


def test_llama_benchy_asks_settings_warns_and_shows_report(qapp, tmp_path):
    import json
    from tests.test_llama_benchy import RESULT, PyBenchy, _fake
    from app.all_workloads import load_workloads as load_all
    assert {"llama_benchy", "mlperf_client", "diskspd", "fio", "sevenzip_benchmark",
            "sevenzip_fm_benchmark"} <= {w.id for w in load_all()[0]}
    script = _fake(tmp_path, f"open(out, 'w').write({json.dumps(json.dumps(RESULT))})\n")
    wl = PyBenchy(script)
    wl.model = None
    profile = RunProfile(name="one", repetitions=1, warmup_runs=0, cooldown_seconds=0)
    panel, db = make_panel(tmp_path, [wl], profile=profile)
    panel.set_checked("llama_benchy")
    panel.telemetry_box.setChecked(False)
    panel.choose_settings = lambda w: None
    assert panel.start_plan() is False and "No llama-benchy settings chosen" in panel.status_label.text()
    panel.choose_settings = lambda w: {"model": "google/gemma-4-12b-qat", "depths": [0, 4096],
                                       "concurrency": [1, 2], "prefix_caching": True}
    assert not panel.report_btn.isEnabled()
    assert panel.start_plan() is True
    assert "WARNING: Do not use LM Studio for anything else" in panel.asked[0]
    assert "--concurrency 1 2 --enable-prefix-caching" in panel.asked[0]
    finish(qapp, panel)
    runs = db.get_runs(panel.last_session_id, include_test=True)
    assert [(r["score"], r["status"]) for r in runs] == [(14.098, "completed")]
    assert db.get_session_manifest(panel.last_session_id)["benchmarks"][0]["model"] == "google/gemma-4-12b-qat"
    assert panel.report_btn.isEnabled() and panel.reports[0][0] == "llama_benchy (run 1)"
    assert "| 1 | 0 | measured |" in panel.reports[0][1].read_text(encoding="utf-8")


def test_llama_settings_dialog_and_no_server(qapp):
    from app.ui import automation_panel
    from app.ui.llama_benchy_dialog import LlamaBenchyDialog, ReportDialog
    with patch.object(automation_panel.QMessageBox, "warning") as warn:
        assert automation_panel.choose_llama_settings(
            None, read_models=lambda: (_ for _ in ()).throw(OSError("refused")),
            start_server=lambda: "no lms", sizes=dict) is None
        assert "not reachable" in warn.call_args[0][2] and "lms server start: no lms" in warn.call_args[0][2]
    # not running yet (Linux llmster has no window): the app starts it, then reads the models (owner, 2026-10-20)
    up = []

    def read():
        if not up:
            raise OSError("Connection refused")
        return [{"id": "a", "type": "llm", "state": "not-loaded"}]
    assert automation_panel.language_models(None, read, start_server=lambda: up.append(1), sizes=dict) == read()
    models = [{"id": "a", "type": "llm", "state": "not-loaded"}, {"id": "emb", "type": "embeddings"},
              {"id": "b", "type": "vlm", "quantization": "Q4_K_M", "state": "loaded"}]
    assert [m["id"] for m in automation_panel.language_models(None, lambda: models, sizes=dict)] == ["b", "a"]
    box = LlamaBenchyDialog(automation_panel.language_models(None, lambda: models, sizes=dict), {"model": "a", "pp": 256})
    assert box.model.currentData() == "a" and box.pp.value() == 256
    box.depths.setText("8192 0")
    box.concurrency.setText("1 4")
    box.prefix.setChecked(True)
    assert box.values() == {"model": "a", "pp": 256, "tg": 128, "depths": [0, 8192], "concurrency": [1, 4],
                            "prefix_caching": True}
    box.concurrency.setText("0")
    assert box.values() is None and "Concurrency" in box.error.text()

    class Accepting(LlamaBenchyDialog):
        def exec(self):
            return automation_panel.QDialog.Accepted
    got = automation_panel.choose_llama_settings(None, {"depths": [0]}, read_models=lambda: models, dialog=Accepting,
                                               sizes=dict)
    assert got["model"] == "b" and got["depths"] == [0]
    assert "Concurrency" in ReportDialog("r", "| Concurrency |\n|---|\n| 1 |").view.toPlainText()


def test_resume_or_discard_an_interrupted_plan(qapp, tmp_path):
    """A journal left by a crash (written by hand here) is offered on start; resuming finishes it."""
    import json
    from app.automation import journal as jr
    db = Database(str(tmp_path / "a.db"))
    sid = db.create_session("Automated", is_test=True, is_manual=False)
    db.create_result(sid, "selftest_a", "cpu", 7.5, status="completed", is_warmup=True, run_index=0, is_test=True)
    journal = {"session_id": sid, "stamp": "20261018_050000", "cooldown_seconds": 0, "created": "2026-10-18T05:00:00",
               "items": [{"workload_id": "selftest_a", "repetitions": 2, "warmup_runs": 1, "timeout_seconds": 30,
                          "settings": None}],
               "done": [["selftest_a", 0, True]], "current": ["selftest_a", 1, False]}
    (tmp_path / "runs").mkdir()
    path = tmp_path / "runs" / jr.JOURNAL_NAME
    path.write_text(json.dumps(journal), encoding="utf-8")
    panel, _ = make_panel(tmp_path, [workload("selftest_a", "print('SCORE=8')")])
    assert not panel.resume_btn.isHidden() and "(2 run(s) left)" in panel.resume_btn.text()
    asked = []
    panel.ask_resume = lambda text: (asked.append(text), "later")[1]
    assert panel.resume_plan() is False and path.exists()
    assert "is stored as failed" in asked[0]
    panel.ask_resume = lambda text: "resume"
    assert panel.resume_plan() is True
    finish(qapp, panel)
    runs = [(r["run_index"], r["is_warmup"], r["status"], r["score"]) for r in db.get_runs(sid, include_test=True)]
    assert runs == [(0, 1, "completed", 7.5), (1, 0, "failed", None), (2, 0, "completed", 8.0)]
    assert not path.exists() and panel.resume_btn.isHidden()
    path.write_text(json.dumps(journal), encoding="utf-8")
    panel.reload()
    panel.ask_resume = lambda text: "discard"
    assert panel.resume_plan() is False and not path.exists() and "discarded" in panel.status_label.text()
    journal["session_id"] = 999                     # its session was deleted meanwhile
    path.write_text(json.dumps(journal), encoding="utf-8")
    asked.clear()
    panel.ask_resume = lambda text: (asked.append(text), "resume")[1]
    assert panel.resume_plan() is False and "cannot be resumed" in asked[0] and path.exists()
    assert "session was deleted" in panel.status_label.text() and not panel.is_running()


def test_a_model_that_does_not_fit_is_marked_in_the_llama_benchy_list(qapp):
    # Radxa Dragon Q8B, 2026-10-22: the 27B (17.7 GB file) was chosen on a 15 GB board and LM Studio refused it
    from app.ui import automation_panel
    from app.ui.llama_benchy_dialog import LlamaBenchyDialog
    models = [{"id": "qwen/qwen3.8-27b", "type": "llm", "state": "not-loaded"},
              {"id": "google/gemma-4-12b-qat", "type": "llm", "state": "not-loaded"},
              {"id": "other", "type": "llm", "state": "not-loaded"}]
    automation_panel.mark_fit(models, lambda: {"qwen/qwen3.8-27b": 17.74, "google/gemma-4-12b-qat": 7.2}, ram=15.0,
                              card=None)
    assert "does not fit" in models[0]["fit"] and "Download" not in models[0]["fit"]
    assert not models[1]["fit"] and "fit" not in models[2]
    box = LlamaBenchyDialog(models)
    assert box.model.itemText(0).endswith("most likely does not fit") and "fit" not in box.model.itemText(1)
    unknown = [{"id": "x", "type": "llm"}]
    automation_panel.mark_fit(unknown, dict)
    assert unknown == [{"id": "x", "type": "llm"}]


def test_main_window_offers_a_problem_report_after_a_failure(qapp, tmp_path):
    """Owner, 2026-10-23: a "Report a problem" button, and the offer when a test did not complete or on an error."""
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {})
    try:
        assert w.problem_btn.text() == "Report a problem" and w.problem_bar.isHidden()
        assert w.automation_panel.on_failures == w.offer_problem_report
        w.offer_problem_report("Did not complete: fio.", 3)
        assert not w.problem_bar.isHidden() and w.problem_session == 3
        w.error_seen.emit("Traceback")
        assert "The app hit an error." in w.problem_bar.label.text()
    finally:
        w.close()


def test_a_test_queued_from_the_panel_joins_the_session_and_its_manifest(qapp, tmp_path):
    """Queue next (owner, 2026-10-23): a test added while the plan runs gets the plan's counts, runs after it in
    the same session and is named in the session's manifest; the manifest saved at the start is not rewritten."""
    slow = workload("selftest_slow", "import time; time.sleep(2); print('SCORE=1')")
    later = workload("selftest_later", "print('SCORE=2')")
    panel, db = make_panel(tmp_path, [slow, later], profile=RunProfile(name="p", repetitions=1, warmup_runs=1,
                                                                      cooldown_seconds=0))
    assert panel.queue_workloads([later]) == ([], {"selftest_later": "no run is going on"})
    panel.set_checked("selftest_slow")
    assert panel.start_plan() is True and panel.can_queue()
    assert panel.queue_workloads([later, slow]) == (["selftest_later"], {"selftest_slow": "already in this run"})
    assert panel.step[1] == 4                          # 2 planned runs + the queued warm-up and run
    finish(qapp, panel)
    sid = panel.last_session_id
    runs = db.get_runs(sid, include_test=True)
    assert sorted((r["benchmark_name"], r["is_warmup"], r["score"]) for r in runs) == [
        ("selftest_later", 0, 2.0), ("selftest_later", 1, 2.0), ("selftest_slow", 0, 1.0), ("selftest_slow", 1, 1.0)]
    used = db.get_session_manifest(sid)["benchmarks"]
    assert [b["name"] for b in used] == ["selftest_slow", "selftest_later"] and used[1]["queued"] is True
    assert not panel.can_queue()

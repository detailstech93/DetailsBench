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
    assert panel.status_label.text() == f"Session #{sid} done: 3 completed."
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
    assert [r["status"] for r in db.get_runs(panel.last_session_id, include_test=True)] == ["failed"]
    assert len(calls) == 1 and calls[0].endswith("screenshot.png")


def test_main_window_has_panel_and_shuts_it_down(qapp, tmp_path):
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {})
    assert w.automation_panel.output_root == (tmp_path / "automation_runs").resolve()
    with patch.object(w.automation_panel, "shutdown") as shutdown:
        w.close()
    shutdown.assert_called_once()

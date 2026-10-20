"""PugetBench for Creators: a typed score per application, its own report section, and never inside a run of several
tests (REAL temporary database and window code; only the dialog answers and message boxes are faked)."""

import sys
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.benchmark_manager import BenchmarkInfo, BenchmarkStatus, BenchmarkType
from app.catalog import MANUAL, TestEntry
from app.database import Database
from app.pro import pugetbench
from app.ui.main_window import MainWindow
from app.ui.record_dialog import PugetResultDialog
from app.ui import run_tests
from app.ui.tests_page import TestsPage
from tests.test_tests_screen_ui import entry

PUGET = BenchmarkInfo(name="pugetbench_creators", display_name="PugetBench for Creators", type=BenchmarkType.SYNTHETIC,
                      status=BenchmarkStatus.READY_MANUAL, version="1.4.2", architecture="x64")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def library(id="pugetbench_creators", ref=PUGET):
    return entry(id, mode=MANUAL, source="library", ref=ref)


def test_the_dialog_stores_one_test_per_application(qapp):
    d = PugetResultDialog("new session", "1.4.2")
    d.application.setCurrentText("Premiere Pro")
    d.score.setText("1234")
    d.app_version.setText("Premiere Pro 25.5")
    d.preset.setText("Standard")
    d._try_accept()
    assert d.values["benchmark_name"] == "pugetbench_premiere_pro" and d.values["score"] == 1234.0
    assert d.values["note"] == ("Entered by: you (typed from PugetBench's results screen); PugetBench version: 1.4.2; "
                                "Application version: Premiere Pro 25.5; Preset: Standard")
    empty = PugetResultDialog("new session", None)
    empty._try_accept()
    assert empty.values is None and "enter the score" in empty.error.text()     # no score is never stored


def test_typed_scores_go_into_the_report_with_their_own_section(qapp, tmp_path):
    db = Database(str(tmp_path / "p.db"))
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(db, {})
    try:
        w.automation_panel.output_root = tmp_path / "automation_runs"
        for app, score in (("pugetbench_photoshop", 9876.0), ("pugetbench_premiere_pro", 1234.0)):
            answer = {"status": "completed", "score": score, "is_warmup": False, "unit": "points",
                      "benchmark_name": app, "display_name": app,
                      "note": pugetbench.note_text("1.4.2", "27.0", "Standard")}
            opened = []
            with patch.object(MainWindow, "_ask_result", return_value=answer), \
                    patch("app.manifest.git_commit", return_value="c" * 40):
                path = run_tests.record_and_report(w, library(), opener=opened.append)
        runs = db.get_runs(w.current_session_id)
        assert [(r["benchmark_name"], r["score"], r["is_manual"]) for r in runs] == \
            [("pugetbench_photoshop", 9876.0, 1), ("pugetbench_premiere_pro", 1234.0, 1)]
        assert opened == [path] and path.parent == tmp_path / "reports"
        html = path.read_text(encoding="utf-8")
        assert "<h2>PugetBench for Creators</h2>" in html and "TYPED IN BY YOU" in html
        # each typed score gets its own report, named after the test and the time (owner, 2026-10-20)
        assert "1,234" in html and "9,876" not in html and "PugetBench version" in html
        assert path.name.startswith("pugetbench_premiere_pro_") or path.name.startswith("PugetBench")
        assert "During the test" not in html.split("<h2>PugetBench for Creators</h2>")[1].split("</section>")[0]
        with patch.object(MainWindow, "_ask_result", return_value=None):         # cancelled: nothing, no report
            assert run_tests.record_and_report(w, library(), opener=opened.append) is None
    finally:
        with patch.object(w.automation_panel, "shutdown"):
            w.close()


def test_a_test_waiting_for_a_typed_score_never_runs_with_others(qapp):
    puget, auto, game = library(), entry("a"), entry("s", source="steam")
    assert run_tests.leave_out_typed([puget]) == ([puget], [])                     # alone: it runs
    assert run_tests.leave_out_typed([auto, puget, game]) == ([auto, game], [puget])
    page = TestsPage()
    page.set_entries([auto, puget])
    page.select_all_installed()
    assert page.selected_ids() == ["a"]

    class Window:
        status_label = type("L", (), {"setText": lambda self, t: None})()
    with patch.object(run_tests, "QMessageBox") as box, patch.object(run_tests, "start_other") as other:
        assert not run_tests.start_tests(Window(), [puget, library("geekbench6_cpu", ref=None)],
                                         licenses=lambda w, e: e)
    assert "only run one at a time" in box.information.call_args[0][2] and not other.called

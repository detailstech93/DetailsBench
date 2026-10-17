"""Manual result recording, session manifest creation and the statistics table.

Uses a REAL temporary Database with the REAL window code; only the dialog
answer is faked (no modal dialog is shown).
"""

import sys
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.benchmark_manager import BenchmarkInfo, BenchmarkStatus, BenchmarkType
from app.database import Database
from app.ui.main_window import MainWindow
from app.ui.record_dialog import parse_score


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


CB = BenchmarkInfo(name="cinebench", display_name="Cinebench", type=BenchmarkType.CPU,
                   status=BenchmarkStatus.READY_MANUAL, version="2026.1.3.0", architecture="x64")


@pytest.mark.parametrize("status, text, expected", [
    ("completed", "1234.5", (1234.5, None)),
    ("completed", "1,234", (1.234, None)),     # comma as decimal separator
    ("completed", "", (None, "enter the score shown by the benchmark")),
    ("completed", "abc", (None, "not a number: abc")),
    ("completed", "-5", (None, "the score must be a finite number of 0 or more")),
    ("completed", "nan", (None, "the score must be a finite number of 0 or more")),
    ("failed", "", (None, None)),
    ("failed", "0", (None, "a failed run must not have a score")),
])
def test_parse_score(status, text, expected):
    assert parse_score(status, text) == expected


@pytest.fixture
def window(qapp, tmp_path):
    db = Database(str(tmp_path / "ui.db"))
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(db, {})
    w.benchmark_manager.benchmarks = [CB]
    yield w, db
    w.close()


def record(window, **values):
    answer = {"status": "completed", "score": None, "is_warmup": False, "unit": "points", "note": None}
    answer.update(values)
    with patch.object(MainWindow, "_ask_result", return_value=answer), \
            patch("app.manifest.git_commit", return_value="c" * 40):
        window._on_record_clicked(CB)


def test_first_record_creates_manual_session_with_manifest(window):
    w, db = window
    record(w, score=1000.0)
    sid = w.current_session_id
    session = db.get_session(sid)
    assert session["is_manual"] == 1
    manifest = db.get_session_manifest(sid)
    assert manifest["session"]["id"] == sid
    assert [b["name"] for b in manifest["benchmarks"]] == ["cinebench"]
    assert manifest["benchmarks"][0]["version"] == "2026.1.3.0"
    run = db.get_runs(sid)[0]
    assert (run["score"], run["status"], run["is_manual"], run["run_index"]) == (1000.0, "completed", 1, 0)
    assert "[MANUAL]" in w.run_log.toPlainText()


def test_cancelled_dialog_saves_nothing(window):
    w, db = window
    with patch.object(MainWindow, "_ask_result", return_value=None):
        w._on_record_clicked(CB)
    assert w.current_session_id is None and db.get_sessions() == []


def test_statistics_table_excludes_warmup_and_failed(window):
    w, db = window
    record(w, score=1500.0, is_warmup=True)
    for score in (1000.0, 1100.0, 900.0):
        record(w, score=score)
    record(w, status="failed")
    w.refresh_history_screen()
    t = w.stats_table
    assert t.rowCount() == 1
    row = [t.item(0, c).text() for c in range(t.columnCount())]
    assert row[1:9] == ["cinebench", "3", "1,000.00", "1,000.00", "900.00", "1,100.00", "100.00", "10.0"]
    assert row[9] == "warmup 1, not_completed 1"
    assert row[10] == "MANUAL"
    assert [r["run_index"] for r in db.get_runs(w.current_session_id)] == [0, 1, 2, 3, 4]


def test_new_session_button_starts_a_second_session(window):
    w, db = window
    record(w, score=1.0)
    first = w.current_session_id
    w._start_new_session()
    record(w, score=2.0)
    assert w.current_session_id != first
    assert len(db.get_sessions()) == 2
    assert db.get_session_manifest(w.current_session_id) is not None


def test_failed_save_is_reported_not_crashing(window):
    w, db = window
    with patch.object(Database, "create_result", side_effect=ValueError("disk full")):
        record(w, score=1.0)
    assert "Result NOT saved: disk full" in w.run_log.toPlainText()

"""History search and run-profile UI with a REAL temporary database (no dialogs shown)."""

import sqlite3
import sys
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.benchmark_manager import BenchmarkInfo, BenchmarkStatus, BenchmarkType
from app.database import Database
from app.profiles import RunProfile
from app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(qapp, tmp_path):
    db = Database(str(tmp_path / "h.db"))
    a = db.create_session("Baseline", "fresh install", is_manual=True)
    b = db.create_session("After driver update", is_manual=True)
    conn = sqlite3.connect(db.db_path)
    conn.execute("UPDATE sessions SET created_at = '2026-10-01T10:00:00' WHERE id = ?", (a,))
    conn.execute("UPDATE sessions SET created_at = '2026-10-15T10:00:00' WHERE id = ?", (b,))
    conn.commit()
    conn.close()
    for score in (1000.0, 1100.0):
        db.create_result(a, "cinebench", "cpu", score, is_manual=True)
    db.create_result(b, "geekbench_7", "cpu", 2500.0, is_manual=True)
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(db, {})
    yield w, db, a, b
    w.close()


def column(table, col):
    return [table.item(r, col).text() for r in range(table.rowCount())]


def test_search_filters_sessions_and_stats(window):
    w, db, a, b = window
    w.refresh_history_screen()
    assert column(w.sessions_table, 0) == [f"#{b}", f"#{a}"]
    w.search_text.setText("baseline")
    w.refresh_history_screen()
    assert column(w.sessions_table, 0) == [f"#{a}"]
    assert column(w.stats_table, 1) == ["cinebench"]          # stats follow the search
    w.search_text.setText("")
    w.search_benchmark.setCurrentIndex(w.search_benchmark.findData("geekbench"))
    w.refresh_history_screen()
    assert column(w.sessions_table, 0) == [f"#{b}"]            # "Geekbench" also finds geekbench_7
    w.search_benchmark.setCurrentIndex(0)
    w.search_from.setText("2026-10-10")
    w.refresh_history_screen()
    assert column(w.sessions_table, 0) == [f"#{b}"]


def test_invalid_date_shows_message_and_keeps_table(window):
    w, *_ = window
    w.refresh_history_screen()
    rows = w.sessions_table.rowCount()
    w.search_to.setText("17/10/2026")
    w.refresh_history_screen()
    assert "use YYYY-MM-DD" in w.search_error.text()
    assert w.sessions_table.rowCount() == rows


def test_profiles_listed_saved_and_used_in_manifest(window):
    w, db, *_ = window
    assert [w.profile_combo.itemText(i) for i in range(w.profile_combo.count())] == ["Standard", "Quick check"]
    with patch.object(MainWindow, "_ask_profile", return_value=RunProfile("Long soak", 5, 2, 1200, 60)):
        w._on_save_profile_clicked()
    assert w.profile_combo.currentText() == "Long soak (saved)"
    cb = BenchmarkInfo(name="cinebench", display_name="Cinebench", type=BenchmarkType.CPU,
                       status=BenchmarkStatus.READY_MANUAL)
    w.benchmark_manager.benchmarks = [cb]
    answer = {"status": "completed", "score": 1.0, "is_warmup": False, "unit": "points", "note": None}
    with patch.object(MainWindow, "_ask_result", return_value=answer), \
            patch("app.manifest.git_commit", return_value=None):
        w._on_record_clicked(cb)
    settings = db.get_session_manifest(w.current_session_id)["run_settings"]
    assert settings["profile"] == "Long soak" and settings["repetitions"] == 5 and settings["cooldown_seconds"] == 60


def test_invalid_profile_from_db_is_reported(window):
    w, db, *_ = window
    with patch.object(MainWindow, "_ask_profile", return_value=RunProfile("Standard")):
        w._on_save_profile_clicked()
    assert "Profile NOT saved" in w.run_log.toPlainText()

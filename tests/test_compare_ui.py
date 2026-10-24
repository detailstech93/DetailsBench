"""Compare Results tab with a REAL temporary database."""

import sys
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.database import Database
from app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(qapp, tmp_path):
    db = Database(str(tmp_path / "c.db"))
    a = db.create_session("Before", is_manual=True)
    b = db.create_session("After", is_manual=True)
    for score in (2500.0, 2510.0, 2490.0):
        db.create_result(a, "geekbench_7", "cpu", score, benchmark_version="7.1.0.0", is_manual=True)
        db.create_result(b, "geekbench_7", "cpu", score + 100, benchmark_version="7.1.0.0", is_manual=True)
    db.create_result(a, "cinebench", "cpu", 1000.0, benchmark_version="2026.1.3.0", is_manual=True)
    db.create_result(b, "cinebench", "cpu", 1300.0, benchmark_version="2027.0.0.0", is_manual=True)
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(db, {})
    w.reload_compare_sessions()
    yield w, a, b
    w.close()


def cells(table, row):
    return [table.item(row, c).text() for c in range(table.columnCount())]


def test_default_selection_is_older_vs_newer(window):
    w, a, b = window
    assert w.compare_a.currentData()["id"] == a and w.compare_b.currentData()["id"] == b


def test_comparison_rows_status_and_reasons(window):
    w, a, b = window
    w.run_comparison()
    t = w.compare_table
    rows = {t.item(r, 0).text(): cells(t, r) for r in range(t.rowCount())}
    gb = rows["geekbench_7"]
    assert gb[1:6] == ["2,500.00", "2,600.00", "+100.00", "+4.00 %", "ok"]
    cb = rows["cinebench"]
    assert cb[5] == "not comparable"                       # 2026 vs 2027 major versions
    assert "block: Major benchmark versions differ." in cb[6]
    assert "warn:" in cb[6] and "fewer than 2 runs" in cb[6]
    assert cb[4] == "+30.00 %"                             # still shown for reference
    assert f"#{a} (A) with #{b} (B)" in w.compare_note.text()


def test_needs_two_sessions(qapp, tmp_path):
    db = Database(str(tmp_path / "empty.db"))
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(db, {})
    w.reload_compare_sessions()
    w.run_comparison()
    assert w.compare_note.text() == "Pick two sessions first."
    w.close()


def test_tab_hidden_and_updates_last(window):
    """Owner, 2026-10-25: Compare Results not shown (the Results library compares); Updates is the last tab."""
    w, _, _ = window
    names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert "Compare Results" not in names and w.tabs.indexOf(w.compare_tab) == -1
    assert names[-1] == "Updates" and names[-2] == "Results library"

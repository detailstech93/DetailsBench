"""History tab export and backup buttons with a REAL temporary database."""

import json
import sys
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from app.database import Database
from app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(qapp, tmp_path):
    db = Database(str(tmp_path / "w.db"))
    older = db.create_session("Older", is_manual=True)
    newer = db.create_session("Newer", is_manual=True)
    db.create_result(newer, "cinebench", "cpu", 1000.0, is_manual=True)
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(db, {})
    w.refresh_history_screen()
    yield w, older, newer, tmp_path
    w.close()


def select_row_of(w, session_id):
    for row in range(w.sessions_table.rowCount()):
        if w.sessions_table.item(row, 0).data(0x0100) == session_id:   # Qt.UserRole
            w.sessions_table.setCurrentCell(row, 1)
            return
    raise AssertionError(f"session {session_id} not in table")


def test_export_needs_selection(window):
    w, *_ = window
    w.sessions_table.setCurrentCell(-1, -1)
    with patch.object(QFileDialog, "getSaveFileName") as dialog:
        w.export_selected_session("json")
    dialog.assert_not_called()
    assert "Select a session" in w.export_note.text()


def test_export_json_of_selected_session(window):
    w, older, newer, tmp = window
    select_row_of(w, newer)
    assert w.selected_session_id() == newer
    target = tmp / "out.json"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
        w.export_selected_session("json")
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["session"]["id"] == newer and data["runs"][0]["score"] == 1000.0
    assert str(target) in w.export_note.text()


def test_export_cancelled_writes_nothing(window):
    w, older, newer, tmp = window
    select_row_of(w, older)
    with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")):
        w.export_selected_session("csv")
    assert not list(tmp.glob("*.csv"))


def test_html_report_asks_before_opening(window):
    w, older, newer, tmp = window
    select_row_of(w, newer)
    target = tmp / "r.html"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
         patch.object(MainWindow, "_confirm", return_value=False) as confirm, \
         patch("app.ui.main_window.webbrowser.open") as opener:
        w.export_selected_session("html")
    confirm.assert_called_once()
    opener.assert_not_called()
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_backup_button(window):
    w, older, newer, tmp = window
    w.backup_now()
    backups = list((tmp / "backups").glob("benchmark_manager_*.db"))
    assert len(backups) == 1
    assert "integrity ok" in w.export_note.text() and "sessions 2" in w.export_note.text()

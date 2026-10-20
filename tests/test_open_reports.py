"""The "Open reports" button (owner, 2026-10-20): the reports folder opens in the file manager. Fakes only."""

import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.ui import reports_folder


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


def test_the_folder_is_made_and_opened(tmp_path, monkeypatch):
    window = SimpleNamespace(status_label=Label())
    monkeypatch.setattr(reports_folder, "folder", lambda w: tmp_path / "Report")
    opened = []
    assert reports_folder.open_folder(window, opener=lambda p: opened.append(p) or True) == tmp_path / "Report"
    assert opened == [tmp_path / "Report"] and (tmp_path / "Report").is_dir() and window.status_label.text == ""
    reports_folder.open_folder(window, opener=lambda p: False)
    assert "could not be opened" in window.status_label.text
    monkeypatch.setattr(reports_folder, "folder", lambda w: None)
    assert reports_folder.open_folder(window, opener=lambda p: True) is None and "Reports folder" in window.status_label.text


def test_on_wsl_the_folder_opens_in_windows_explorer(tmp_path, monkeypatch):
    from app.ui import run_tests
    monkeypatch.setattr(run_tests, "on_wsl", lambda: True)
    sent = []
    monkeypatch.setattr(run_tests, "open_in_windows", lambda p, platform=None: sent.append((p, platform)) or True)
    assert reports_folder.show(tmp_path, platform="linux") and sent == [(tmp_path, "linux")]


def test_the_button_is_on_the_tests_screen(qapp):
    from app.ui.tests_page import TestsPage
    page = TestsPage()
    got = []
    page.open_reports_requested.connect(lambda: got.append(1))
    page.open_reports_btn.click()
    assert got == [1] and page.open_reports_btn.text() == "Open reports"

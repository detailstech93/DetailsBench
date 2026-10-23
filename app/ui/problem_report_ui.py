"""The "Report a problem" window and the bar that offers it after a failure (app/problem_report.py).

Nothing leaves the computer by itself: the zip goes to the reports folder, and the tester's browser opens a new
GitHub issue where they drag it (owner, 2026-10-23).
"""

import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from app import problem_report

logger = logging.getLogger(__name__)

BAR = ("QFrame#problemBar { background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px; } "
       "QFrame#problemBar QLabel { color: #78350f; }")
WHAT_GOES_IN = ("The report is a zip saved in your reports folder. It holds: the app's version, your system "
                "(OS, processor, graphics, memory), the app's log, and the text output of the tests that did not "
                "complete. Your user name, computer name, home folder, e-mail and IP addresses are removed, and so "
                "is anything in the settings that looks like a password or code.\n\n"
                "Nothing is sent by the app. Your browser opens a new GitHub issue (a GitHub account is needed): "
                "drag the zip into it, then press Submit.")


def data_dir() -> Path:
    from app.paths import resolve_database_path
    return Path(resolve_database_path()[0]).resolve().parent


def out_dir(window) -> Path:
    try:
        from app.ui import reports_folder
        folder = reports_folder.folder(window)
    except Exception:
        folder = None
    return Path(folder) if folder else data_dir() / "problem reports"


class ProblemDialog(QDialog):
    def __init__(self, window, session_id: Optional[int] = None, runs=(), system_info=None, intro: str = "",
                 open_url: Callable[[str], object] = None, show_folder: Callable[[Path], object] = None):
        super().__init__(window)
        self.window_, self.session_id, self.runs = window, session_id, list(runs)
        self.system_info = system_info or {}
        self._open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self._show_folder = show_folder
        self.zip_path: Optional[Path] = None
        self.setWindowTitle("Report a problem")
        self.setMinimumWidth(560)
        box = QVBoxLayout(self)
        if intro:
            box.addWidget(QLabel(intro))
        failed = problem_report.failed_runs(self.runs)
        if failed:
            names = sorted({str(r.get("benchmark_name")) for r in failed})
            box.addWidget(QLabel(f"Session #{session_id}: did not complete: {', '.join(names)}"))
        box.addWidget(QLabel("What did you do, and what happened? (optional, helps a lot)"))
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(110)
        box.addWidget(self.text)
        info = QLabel(WHAT_GOES_IN)
        info.setWordWrap(True)
        box.addWidget(info)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        row = QHBoxLayout()
        self.make_btn = QPushButton("Make the report and open GitHub")
        self.folder_btn = QPushButton("Show the file")
        self.folder_btn.setEnabled(False)
        close = QPushButton("Close")
        row.addStretch(1)
        for b in (self.make_btn, self.folder_btn, close):
            row.addWidget(b)
        box.addLayout(row)
        self.make_btn.clicked.connect(self.make)
        self.folder_btn.clicked.connect(self.show_file)
        close.clicked.connect(self.reject)

    def make(self) -> Optional[Path]:
        from app.manifest import git_commit
        what = self.text.toPlainText()
        try:
            self.zip_path = problem_report.make_zip(out_dir(self.window_), what, self.runs, self.session_id,
                                                    data_dir(), self.system_info, git_commit())
        except OSError as e:
            logger.exception("Could not make the problem report")
            self.status.setText(f"The report could not be saved: {e}")
            return None
        failed = problem_report.failed_runs(self.runs)
        self._open_url(problem_report.issue_url(what, failed, self.zip_path.name,
                                                problem_report.system_lines(self.system_info)))
        self.status.setText(f"Saved: {self.zip_path}\nYour browser opened a new GitHub issue: drag this file into "
                            "it, then press Submit.")
        self.folder_btn.setEnabled(True)
        return self.zip_path

    def show_file(self):
        if self.zip_path is None:
            return
        if self._show_folder is not None:
            self._show_folder(self.zip_path.parent)
            return
        from app.ui import reports_folder
        reports_folder.show(self.zip_path.parent)


class ProblemBar(QFrame):
    """Shown after a run where a test did not complete, or after an error the app did not catch."""

    def __init__(self, on_report: Callable[[], None], parent=None):
        super().__init__(parent)
        self.setObjectName("problemBar")
        self.setStyleSheet(BAR)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.report_btn, self.dismiss_btn = QPushButton("Report a problem..."), QPushButton("Dismiss")
        row.addWidget(self.label, 1)
        row.addWidget(self.report_btn)
        row.addWidget(self.dismiss_btn)
        self.report_btn.clicked.connect(on_report)
        self.dismiss_btn.clicked.connect(self.hide)
        self.hide()

    def offer(self, text: str):
        self.label.setText(text + " Send us a problem report so we can fix it?")
        self.show()

"""The app's update screens: a bar under the header when a newer version is out, and Advanced > Updates (version,
"Check now", the daily check and beta switches). The check and the update run off the window's thread; nothing is
updated without a click, and never while tests run. The logic is app/update/updater.py."""

import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from app import __version__
from app.update import updater

BAR = ("QFrame#updateBar { background: #dbeafe; border: 1px solid #60a5fa; border-radius: 6px; } "
       "QFrame#updateBar QLabel { color: #1e3a8a; font-weight: bold; }")


def _ask(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.Yes


def _warn(parent, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


class UpdateBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateBar")
        self.setStyleSheet(BAR)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel()
        self.notes_btn, self.update_btn = QPushButton("What's new"), QPushButton("Update")
        self.later_btn, self.skip_btn = QPushButton("Later"), QPushButton("Skip this version")
        row.addWidget(self.label, 1)
        for b in (self.notes_btn, self.update_btn, self.later_btn, self.skip_btn):
            row.addWidget(b)
        self.hide()

    def show_release(self, release: updater.Release):
        self.label.setText(f"DetailsBench {release.version}{' (beta)' if release.beta else ''} is out "
                           f"(this is {__version__}).")
        self.show()


class UpdatesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.addWidget(QLabel(f"<b>DetailsBench {__version__}</b>"))
        self.daily = QCheckBox("Check for a new version when the app opens (at most once a day)")
        self.beta = QCheckBox("Include beta versions")
        self.check_btn = QPushButton("Check now")
        self.update_btn = QPushButton("Update")
        self.update_btn.hide()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        note = QLabel("New versions come from the project's GitHub releases. The check sends nothing about this PC. "
                      "Updating changes only the app's own folder; your results and settings stay.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        row = QHBoxLayout()
        row.addWidget(self.check_btn)
        row.addWidget(self.update_btn)
        row.addStretch(1)
        for w in (self.daily, self.beta):
            box.addWidget(w)
        box.addLayout(row)
        box.addWidget(self.status)
        box.addWidget(note)
        box.addStretch(1)


class UpdateFlow(QObject):
    checked = Signal(object, bool)          # updater.Check, asked by the user
    updated = Signal(object)                # updater.Outcome
    progressed = Signal(str)

    def __init__(self, window, bar: UpdateBar, tab: UpdatesTab, data_dir: Optional[Path] = None,
                 check: Callable = updater.check, update: Callable = updater.update, ask: Callable = _ask,
                 warn: Callable = _warn,
                 start_thread: Optional[Callable] = None, open_url: Optional[Callable[[str], None]] = None,
                 relaunch: Optional[Callable[[], None]] = None):
        super().__init__(window)
        self.window, self.bar, self.tab, self.data_dir = window, bar, tab, data_dir
        self.check_fn, self.update_fn, self.ask, self.warn = check, update, ask, warn
        self.start_thread = start_thread or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.relaunch = relaunch or self._relaunch
        self.release: Optional[updater.Release] = None
        self.working = False
        settings = self._settings()
        tab.daily.setChecked(settings.get(updater.CHECK_KEY, True) is not False)
        tab.beta.setChecked(bool(settings.get(updater.BETA_KEY, False)))
        tab.daily.toggled.connect(lambda on: self._save(updater.CHECK_KEY, bool(on)))
        tab.beta.toggled.connect(lambda on: self._save(updater.BETA_KEY, bool(on)))
        tab.check_btn.clicked.connect(lambda: self.check(asked=True))
        tab.update_btn.clicked.connect(self.start_update)
        bar.update_btn.clicked.connect(self.start_update)
        bar.notes_btn.clicked.connect(lambda: self.release and self.open_url(self.release.url))
        bar.later_btn.clicked.connect(bar.hide)
        bar.skip_btn.clicked.connect(self.skip)
        self.checked.connect(self._on_checked)
        self.updated.connect(self._on_updated)
        self.progressed.connect(tab.status.setText)

    # settings.json
    def _dir(self) -> Path:
        from app.paths import get_data_dir
        return Path(self.data_dir or get_data_dir())

    def _settings(self) -> dict:
        from app.paths import load_settings
        return load_settings(self._dir())[0]

    def _save(self, key, value) -> None:
        from app.paths import save_setting
        try:
            save_setting(self._dir(), key, value)
        except (OSError, ValueError) as e:
            self.tab.status.setText(f"Setting not saved: {e}")

    def busy(self) -> bool:
        """Tests (or an install) running: no check, no update."""
        page = getattr(self.window, "tests_page", None)
        flow = getattr(self.window, "run_all_flow", None)
        return bool(getattr(page, "busy", False)) or bool(flow and flow.busy())

    # the check
    def at_start(self) -> bool:
        if not updater.due(self._settings()) or self.busy():
            return False
        return self.check(asked=False)

    def check(self, asked: bool) -> bool:
        if self.working:
            return False
        self.working = True
        self.tab.check_btn.setEnabled(False)
        self.tab.status.setText("Checking...")
        beta = self.tab.beta.isChecked()
        self.start_thread(lambda: self.checked.emit(self._safe_check(beta), asked))
        return True

    def _safe_check(self, beta: bool):
        try:
            return self.check_fn(beta=beta)
        except Exception as e:          # never let the check break the app
            return updater.Check(message=f"The check failed: {e}", error=True)

    def _on_checked(self, result, asked: bool):
        from datetime import datetime
        self.working = False
        self.tab.check_btn.setEnabled(True)
        self.tab.status.setText(result.message)
        if not result.error:
            self._save(updater.LAST_KEY, datetime.now().isoformat(timespec="seconds"))
        self.release = result.release
        self.tab.update_btn.setVisible(bool(result.release))
        if result.release and (asked or updater.offered(result.release, self._settings())):
            self.bar.show_release(result.release)
        else:
            self.bar.hide()

    def skip(self):
        if self.release:
            self._save(updater.SKIP_KEY, self.release.version)
        self.bar.hide()

    # the update
    def start_update(self) -> bool:
        release = self.release
        if not release or self.working:
            return False
        if self.busy():
            self.tab.status.setText("Tests are running: update when they end.")
            return False
        if not updater.git_copy():
            self.open_url(release.url)
            self.tab.status.setText(f"This copy cannot update itself: version {release.version} is on its page "
                                    "(opened in the browser).")
            return False
        if not self.ask(self.window, "Update DetailsBench",
                        f"Update DetailsBench from {__version__} to {release.version}?\n\nThe app downloads the new "
                        "version from GitHub and installs its Python libraries; your results and settings stay. "
                        "Restart it afterwards."):
            return False
        self.working = True
        self.bar.update_btn.setEnabled(False)
        self.tab.update_btn.setEnabled(False)
        self.start_thread(lambda: self.updated.emit(self._safe_update(release)))
        return True

    def _safe_update(self, release):
        try:
            return self.update_fn(release, progress=self.progressed.emit)
        except Exception as e:
            return updater.Outcome(False, f"The update failed: {e}")

    def _on_updated(self, outcome):
        self.working = False
        self.bar.update_btn.setEnabled(True)
        self.tab.update_btn.setEnabled(True)
        self.tab.status.setText(outcome.message)
        if outcome.previous:
            self._save(updater.PREVIOUS_KEY, outcome.previous)
        if not outcome.ok:
            self.warn(self.window, "Update", outcome.message)
            return
        self.bar.hide()
        if self.ask(self.window, "Update", outcome.message + "\n\nRestart now?"):
            self.relaunch()

    def _relaunch(self):
        from PySide6.QtWidgets import QApplication
        subprocess.Popen(updater.restart_command(), cwd=str(updater.ROOT))
        QApplication.instance().quit()

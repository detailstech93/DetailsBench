"""The "Shut down the computer when the run ends" (owner, 2026-10-21): off by default on every run. When the run ends,
the computer is shut down only if it was not stopped, its results are in the database and its report is on disk;
then a 60 s countdown with Cancel, then the system's own command. If anything is missing, or the command is
refused, the computer stays on and the reason is shown. Not offered in WSL: there it would stop Linux, not the PC."""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

logger = logging.getLogger(__name__)

SECONDS = 60


def command(platform: Optional[str] = None) -> Optional[List[str]]:
    """The system's own shutdown command, or None where it is not offered."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return ["shutdown", "/s", "/t", "0"]
    if platform.startswith("linux"):
        return ["systemctl", "poweroff"]          # allowed without sudo for the user at the computer (polkit)
    return None


def why_not_offered(platform: Optional[str] = None, wsl: Optional[Callable[[], bool]] = None) -> Optional[str]:
    if command(platform) is None:
        return "not offered on this system"
    if wsl is None:
        from app.ui.run_tests import on_wsl
        wsl = on_wsl
    if (platform or sys.platform).startswith("linux") and wsl():
        return "not offered in WSL: it would stop Linux, not the computer"
    return None


def why_not_now(db, session_id: Optional[int], summary, report: Optional[Path]) -> Optional[str]:
    """None when everything of the run is saved; else why the computer stays on."""
    if session_id is None:
        return "the run has no session"
    if summary is None or getattr(summary, "stopped", False) or not hasattr(summary, "records"):
        return "the run was stopped or did not end normally"
    from app.automation.runner import SKIPPED_BY_USER
    if any(str(getattr(getattr(r, "result", None), "error", "") or "").startswith(SKIPPED_BY_USER) for r in summary.records):
        return "a test was skipped (Skip cancels the shutdown)"         # owner, 2026-10-21
    try:
        stored = db.get_runs(session_id, include_test=True)
    except Exception as e:
        return f"its results could not be read back from the database: {e}"
    if summary.records and not stored:
        return "its results are not in the database"
    if report is None or not Path(report).is_file() or Path(report).stat().st_size == 0:
        return "its report was not written"
    return None


def shut_down(run=subprocess.run, platform: Optional[str] = None) -> Optional[str]:
    """None when the system accepted the command; else why not."""
    argv = command(platform)
    if argv is None:
        return "no shutdown command on this system"
    if hasattr(os, "sync"):
        os.sync()                                   # files written by the run reach the disk first
    try:
        done = run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    if done.returncode != 0:
        lines = [l for l in ((done.stderr or "") + (done.stdout or "")).splitlines() if l.strip()]
        return lines[-1] if lines else f"exit code {done.returncode}"
    return None


class CountdownDialog(QDialog):
    """Accepted when the time runs out or on "Shut down now"; rejected on Cancel."""

    def __init__(self, parent=None, seconds: int = SECONDS):
        super().__init__(parent)
        self.setWindowTitle("Shut down")
        self.left = seconds
        box = QVBoxLayout(self)
        self.label = QLabel()
        box.addWidget(self.label)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Shut down now")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancel (keep it on)")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self._show()

    def _show(self):
        self.label.setText(f"The tests are over and everything is saved.\nThe computer shuts down in {self.left} s.")

    def tick(self):
        self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.accept()
        else:
            self._show()


def _countdown(window) -> bool:
    return CountdownDialog(window).exec() == QDialog.Accepted


def after_run(window, session_id: Optional[int], summary, report: Optional[Path],
              countdown: Callable = _countdown, shutdown: Callable = shut_down) -> bool:
    """At the end of a run with the box ticked. True when the shutdown was handed to the system."""
    why = why_not_now(window.db, session_id, summary, report)
    if why is None and not countdown(window):
        why = "cancelled"
    if why is None:
        why = shutdown()
        if why is None:
            window.status_label.setText("Shutting down...")
            return True
        why = f"the system refused: {why}"
    logger.info("Not shut down: %s", why)
    window.status_label.setText(f"{window.status_label.text()}  The computer was not shut down: {why}.")
    return False

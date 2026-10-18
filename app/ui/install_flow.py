"""The Install button on the Tests screen (app/install/winget.py does the work).

winget: ask winget about the package in the background, show what will be downloaded (name, publisher, URL, size,
SHA256) and ask; only after Yes is it installed, still in the background, with the moving bar on the Tests screen.
Then the app scans again so the test shows as installed. Linux: the distribution's package manager through
pkexec, after the owner saw the exact commands. A Steam game opens Steam's own install dialog.
"""

import logging
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from app.catalog import TestEntry
from app.install import plans, winget
from app.ui.tests_page import RunState, install_plan

logger = logging.getLogger(__name__)


def ask_yes(parent, title: str, text: str) -> bool:
    from app.ui.long_question import ask_yes_no
    return ask_yes_no(parent, title, text)


class InstallFlow(QObject):
    described = Signal(object)          # winget.Package (from the worker thread)
    finished = Signal(object)           # winget.Outcome

    def __init__(self, window, ask: Callable[..., bool] = ask_yes, describe=winget.describe,
                 install=winget.install, linux_commands=plans.linux_commands, run_commands=None, start_thread: Optional[Callable[[Callable[[], None]], None]] = None):
        super().__init__(window)
        self.window = window
        self.ask, self.describe_fn, self.install_fn = ask, describe, install
        self.linux_commands = linux_commands
        if run_commands is None:
            from app.ui.frame_capture_panel import run_commands
        self.run_commands = run_commands
        from app.install import direct
        self.direct = direct
        self.start_thread = start_thread or (lambda fn: threading.Thread(target=fn, daemon=True,
                                                                         name="install").start())
        self.state = RunState()
        self.entry: Optional[TestEntry] = None
        self.described.connect(self._on_described)
        self.finished.connect(self._on_finished)

    def busy(self) -> bool:
        return self.state.running

    def start(self, entry: TestEntry) -> bool:
        if self.busy():
            return False
        plan = install_plan(entry, self.window.tests_page.host)
        if plan is None:
            return False
        self.entry = entry
        if plan["kind"] == "linux":
            return self._linux(entry, plan)
        if plan["kind"] == "steam":
            url = winget.steam_install_url(plan["app_id"])
            if not self.ask(self.window, "Install", f"Open Steam's install dialog for {entry.name}?\n\n"
                            f"Steam shows the size and the folder there and installs it from Steam's servers "
                            f"({url}). When Steam has finished, press 'I installed something: check again'."):
                return False
            self.window.tests_page.open_url(url)
            return True
        if plan["kind"] == "zip":
            return self._zip(entry, plan)
        self._show(f"Asking Windows Package Manager about {entry.name}...")
        self.start_thread(lambda: self.described.emit(self._safe(lambda: self.describe_fn(entry.id),
                                                                 winget.Package(entry.id, ""))))
        return True

    def _linux(self, entry: TestEntry, plan: dict) -> bool:
        commands, problem = self.linux_commands(plan["family"], plan["package"])
        if problem:
            QMessageBox.warning(self.window, "Install", f"{entry.name} cannot be installed by the app: {problem}.")
            return False
        shown = "\n".join(" ".join(c) for c in commands)
        if not self.ask(self.window, "Install", f"Install {entry.name} with your distribution's package manager?\n\n"
                        f"{shown}\n\nThe package {plan['package']!r} comes from your distribution's own servers; "
                        f"the system asks for your password (pkexec). This package name was not checked on a real "
                        f"{plan['family']} machine yet: if it is not found, nothing is installed."):
            return False
        self._show(f"Installing {plan['package']} (enter your password in the system prompt)...")

        def work():
            text = self.run_commands(commands)
            ok = text == "done"
            self.finished.emit(winget.Outcome(ok, f"{plan['package']} installed." if ok else
                                              f"{plan['package']} was not installed: {text}"))
        self.start_thread(lambda: self._safe(work, winget.Outcome(False, "")))
        return True

    def _zip(self, entry: TestEntry, plan: dict) -> bool:
        bad = self.direct.problem(plan)
        if bad:
            QMessageBox.warning(self.window, "Install", f"{entry.name} cannot be installed by the app: {bad}.")
            return False
        if not self.ask(self.window, "Install", self.direct.confirm_text(entry.name, plan)):
            return False
        self._show(f"Downloading {plan['product']} {plan['version']}...")
        failed = winget.Outcome(False, "")
        self.start_thread(lambda: self.finished.emit(self._safe(lambda: self.direct.install(entry.name, plan),
                                                                failed)))
        return True

    def _safe(self, fn, failed):
        try:
            return fn()
        except Exception as e:          # the screen must come back whatever happened
            logger.exception("Install step failed")
            if isinstance(failed, winget.Outcome):
                failed.message = f"The install failed: {e}"
            else:
                failed.problem = str(e)
            return failed

    def _show(self, text: str):
        self.state = RunState(True, 0, None, text)
        page = self.window.tests_page
        page.watch(lambda: self.state, lambda: None)
        page.stop_btn.setEnabled(False)         # an installer that is running is not cut off

    def _end(self, text: str):
        self.state = RunState(False, 0, None, text)

    def _on_described(self, pkg: "winget.Package"):
        if pkg.problem:
            self._end(f"Not installed: {pkg.problem}")
            QMessageBox.warning(self.window, "Install", f"{self.entry.name} cannot be installed by the app: "
                                f"{pkg.problem}\n\nUse the official download page instead.")
            return
        if not self.ask(self.window, "Install", winget.confirm_text(pkg)):
            self._end("Install cancelled; nothing was downloaded.")
            return
        self.state = RunState(True, 0, None, f"Installing {pkg.name} {pkg.version} with winget "
                                             f"(accept the Windows prompt if one appears)...")
        failed = winget.Outcome(False, "")
        self.start_thread(lambda: self.finished.emit(self._safe(lambda: self.install_fn(pkg), failed)))

    def _on_finished(self, outcome: "winget.Outcome"):
        if not outcome.ok and not outcome.message:
            outcome.message = "The install failed (see the log)."
        self._end(outcome.message + (" Checking the installed tests again..." if outcome.ok else ""))
        if outcome.ok:
            self.window.start_scan()
        else:
            QMessageBox.warning(self.window, "Install", outcome.message)

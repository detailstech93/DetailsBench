"""Run all tests (Tests screen): first the tests that are not installed, then everything together.

1. Every missing test this computer can install with one click (app/install/plans.py: winget on Windows, the
   distribution's package manager on Linux) is checked in the background: winget says what it would download
   (URL, size, SHA256; a URL off the vendor's host is refused), Linux gets its exact commands.
2. One question lists them all: Install them, then run everything / Run only what is installed / Cancel.
3. Install: one after the other in the background (each installer may show UAC, pkexec its password prompt);
   one that fails is reported and the others go on. Then the app scans again and, once the scan is done, starts
   the tests that can run (the usual run dialog: repetitions, game preset, stress minutes).
Missing Steam games are not downloaded here (Steam's own dialog, often tens of GB): the question names them.
"""

import logging
import threading
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from app.catalog import MISSING, TestEntry
from app.install import plans, winget
from app.ui.tests_page import RunState, install_plan

logger = logging.getLogger(__name__)

INSTALL, INSTALLED_ONLY = "install", "installed_only"


def ask_choice(parent, text: str) -> Optional[str]:
    box = QMessageBox(QMessageBox.Question, "Run all tests", text, parent=parent)
    install = box.addButton("Install them, then run all", QMessageBox.AcceptRole)
    only = box.addButton("Run only installed tests", QMessageBox.NoRole)
    box.addButton(QMessageBox.Cancel)
    box.setDefaultButton(install)
    box.exec()
    return {install: INSTALL, only: INSTALLED_ONLY}.get(box.clickedButton())


class Item:
    """One missing test and what installing it means."""

    def __init__(self, entry: TestEntry, plan: dict):
        self.entry, self.plan = entry, plan
        self.package: Optional[winget.Package] = None
        self.commands: List[List[str]] = []
        self.problem: Optional[str] = None

    def line(self) -> str:
        if self.problem:
            return f"- {self.entry.name}: cannot be installed by the app ({self.problem})"
        if self.package is not None:
            p = self.package
            return (f"- {self.entry.name}: {p.name} {p.version} by {p.publisher or 'unknown publisher'}, "
                    f"{winget.size_text(p.size_bytes)}, from {p.url} (SHA256 {p.sha256[:16]}...)")
        return (f"- {self.entry.name}: package {self.plan['package']!r} from your distribution "
                f"(not checked on a real {self.plan['family']} machine yet): " + "; ".join(" ".join(c) for c in
                                                                                          self.commands))


class RunAllFlow(QObject):
    checked = Signal(object)        # [Item] (from the worker thread)
    installed = Signal(object)      # [str] one line per install

    def __init__(self, window, ask: Callable[..., Optional[str]] = ask_choice, describe=winget.describe,
                 install=winget.install, linux_commands=plans.linux_commands, run_commands=None,
                 start_thread: Optional[Callable[[Callable[[], None]], None]] = None):
        super().__init__(window)
        self.window = window
        self.ask, self.describe_fn, self.install_fn = ask, describe, install
        self.linux_commands = linux_commands
        if run_commands is None:
            from app.ui.frame_capture_panel import run_commands
        self.run_commands = run_commands
        self.start_thread = start_thread or (lambda fn: threading.Thread(target=fn, daemon=True,
                                                                         name="run-all").start())
        self.state = RunState()
        self.steam_missing: List[str] = []
        self.checked.connect(self._on_checked)
        self.installed.connect(self._on_installed)

    def busy(self) -> bool:
        return self.state.running

    def start(self, entries: List[TestEntry]) -> bool:
        page = self.window.tests_page
        if self.busy() or page.busy:
            return False
        items, self.steam_missing = [], []
        for e in entries:
            if not (e.compatible and e.state == MISSING):
                continue
            plan = install_plan(e, page.host)
            if plan is not None and plan["kind"] in ("winget", "linux"):
                items.append(Item(e, plan))
            elif e.source == "steam":
                self.steam_missing.append(e.name)
        if not items:
            return self._run_tests()
        self._show(f"Checking the {len(items)} tests that are not installed...")
        self.start_thread(lambda: self.checked.emit(self._check(items)))
        return True

    def _check(self, items: List[Item]) -> List[Item]:
        for item in items:
            try:
                if item.plan["kind"] == "winget":
                    item.package = self.describe_fn(item.entry.id)
                    item.problem = item.package.problem
                else:
                    item.commands, item.problem = self.linux_commands(item.plan["family"], item.plan["package"])
            except Exception as e:          # one broken check must not stop the others
                logger.exception("Install check failed")
                item.problem = str(e)
        return items

    def _show(self, text: str):
        self.state = RunState(True, 0, None, text)
        page = self.window.tests_page
        page.watch(lambda: self.state, lambda: None)
        page.stop_btn.setEnabled(False)         # an installer that is running is not cut off

    def _end(self, text: str):
        self.state = RunState(False, 0, None, text)

    def _on_checked(self, items: List[Item]):
        good = [i for i in items if not i.problem]
        text = (f"{len(items)} tests for this PC are not installed:\n\n" + "\n".join(i.line() for i in items))
        if self.steam_missing:
            text += ("\n\nNot downloaded here (install them from Steam if you want them): "
                     + ", ".join(self.steam_missing))
        if good:
            text += ("\n\nInstall them: each comes from the address shown; Windows may ask for administrator rights "
                     "(UAC) or Linux for your password. By installing you accept each program's license. Then all "
                     "tests run.\nRun only installed tests: nothing is downloaded.")
        choice = self.ask(self.window, text)
        if choice is None:
            self._end("Cancelled; nothing was downloaded or run.")
            return
        if choice != INSTALL or not good:
            self._end("Running the tests that are installed...")
            self._run_tests()
            return
        self.state = RunState(True, 0, len(good), f"Installing {len(good)} tests...")
        self.start_thread(lambda: self.installed.emit(self._install(good)))

    def _install(self, items: List[Item]) -> List[str]:
        lines = []
        for n, item in enumerate(items):
            self.state = RunState(True, n, len(items), f"Installing {item.entry.name} "
                                                       f"(accept the system prompt if one appears)...")
            try:
                if item.package is not None:
                    out = self.install_fn(item.package)
                    lines.append(out.message)
                else:
                    result = self.run_commands(item.commands)
                    lines.append(f"{item.plan['package']} installed." if result == "done" else
                                 f"{item.plan['package']} was not installed: {result}")
            except Exception as e:
                logger.exception("Install failed")
                lines.append(f"{item.entry.name}: the install failed ({e})")
        return lines

    def _on_installed(self, lines: List[str]):
        self.state = RunState(True, 0, None, " ".join(lines) + " Checking the installed tests again...")
        self.window.after_scan.append(self._after_scan)
        self.window.start_scan()

    def _after_scan(self):
        self._end("Installs done. Starting the tests...")
        self._run_tests()

    def _run_tests(self) -> bool:
        runnable = [e for e in self.window.tests_page.entries if e.can_run()]
        if not runnable:
            QMessageBox.information(self.window, "Run all tests", "No test can run on this PC yet.")
            return False
        self.window.run_tests(runnable)
        return True

"""Run all tests (Tests screen): first the tests that are not installed, then everything together.

1. Every missing test this computer can install with one click (app/install/plans.py: winget on Windows, the
   distribution's package manager on Linux, the vendor's pinned file for Blender and V-Ray) is checked in the background: winget says what it would download
   (URL, size, SHA256; a URL off the vendor's host is refused), Linux gets its exact commands.
2. One question lists them all: Install them, then run everything / Run only what is installed / Cancel.
3. Install: one after the other in the background (each installer may show UAC, pkexec its password prompt);
   one that fails is reported and the others go on. Then the app scans again and, once the scan is done, starts
   the tests that can run (the usual run dialog: repetitions, game preset, stress minutes).
Missing Steam games are not downloaded here (Steam's own dialog, often tens of GB): the question names them.

Install selected (owner, 2026-10-20: tick several tests that are not installed, install them all with one button):
the same flow with install_only, which asks only Install them / Cancel and runs nothing after the scan.
"""

import dataclasses
import logging
import threading
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from app.catalog import MISSING, TestEntry
from app.install import direct, plans, winget
from app.ui.tests_page import RunState, install_plan

logger = logging.getLogger(__name__)

INSTALL, INSTALLED_ONLY = "install", "installed_only"


def ask_choice(parent, text: str, install_only: bool = False) -> Optional[str]:
    from app.ui.long_question import ask_buttons
    if install_only:
        return ask_buttons(parent, "Install selected tests", text, [("Install them", INSTALL), ("Cancel", None)])
    return ask_buttons(parent, "Run all tests", text, [("Install them, then run all", INSTALL),
                                                       ("Run only installed tests", INSTALLED_ONLY),
                                                       ("Cancel", None)])


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
        if self.plan["kind"] == "zip":
            p = self.plan
            text = (f"- {self.entry.name}: {p['product']} {p['version']}, {winget.size_text(p['size'])}, from "
                    f"{p['url']} ({direct.checksum_text(p)[:24]}...)")
            if p.get("then"):
                from app.ui.install_flow import THEN
                text += ". " + THEN[p["then"]][0]()
            return text
        if self.plan["kind"] == "lmstudio":
            from app.ai.model_set import install_text
            return f"- {self.entry.name}: " + install_text(self.plan["test_id"]).splitlines()[0]
        if self.package is not None:
            p = self.package
            return (f"- {self.entry.name}: {p.name} {p.version} by {p.publisher or 'unknown publisher'}, "
                    f"{winget.size_text(p.size_bytes)}, from {p.url} (SHA256 {p.sha256[:16]}...)")
        if self.plan.get("fetch") == "chrome":
            from app.install.chrome_linux import SERVER, TERMS_URL
            return (f"- {self.entry.name}: Google Chrome (stable), the newest from {SERVER}, kept only if size and "
                    f"SHA-256 match Google's package list, then apt installs it (terms: {TERMS_URL})")
        return (f"- {self.entry.name}: package {self.plan['package']!r} from your distribution "
                f"(not checked on a real {self.plan['family']} machine yet): " + "; ".join(" ".join(c) for c in
                                                                                          self.commands))


def collect(entries: List[TestEntry], host) -> Tuple[List[Item], List[str]]:
    """(what to install, missing Steam games) for these tests; one program for several tests is one item."""
    items, steam_missing = [], []
    for e in entries:
        if not (e.compatible and e.state == MISSING):
            continue
        plan = install_plan(e, host)
        if plan is not None and plan["kind"] == "zip":
            same = next((i for i in items if i.plan.get("folder") == plan["folder"]), None)
            if same is None:
                items.append(Item(e, plan))
            else:                           # one program for several tests (Blender CPU and GPU)
                same.entry = dataclasses.replace(same.entry, name=f"{same.entry.name} + {e.name}")
        elif plan is not None and plan["kind"] == "linux" and any(i.plan == plan for i in items):
            same = next(i for i in items if i.plan == plan)     # one browser for both web tests
            same.entry = dataclasses.replace(same.entry, name=f"{same.entry.name} + {e.name}")
        elif plan is not None and plan["kind"] in ("winget", "linux", "lmstudio"):
            items.append(Item(e, plan))
        elif e.source == "steam":
            steam_missing.append(e.name)
    return items, steam_missing


def check_items(items: List[Item], describe=winget.describe, linux_commands=plans.linux_commands) -> List[Item]:
    """What each install would download or run (winget is asked: seconds; call it off the Qt thread)."""
    for item in items:
        try:
            if item.plan["kind"] == "zip":
                item.problem = direct.problem(item.plan)
            elif item.plan["kind"] == "lmstudio":
                from app.ai.lmstudio import lms_path
                item.problem = None if lms_path() else ("LM Studio is not installed (Install LM Studio on the "
                                                        "model's row first)")
            elif item.plan["kind"] == "winget":
                item.package = describe(item.entry.id)
                item.problem = item.package.problem
            else:
                item.commands, item.problem = linux_commands(item.plan["family"], item.plan["package"])
        except Exception as e:          # one broken check must not stop the others
            logger.exception("Install check failed")
            item.problem = str(e)
    return items


def question(items: List[Item], steam_missing: List[str], install_only: bool) -> str:
    good = [i for i in items if not i.problem]
    text = (f"{len(items)} {'selected tests' if install_only else 'tests for this PC'} are not "
            f"installed:\n\n" + "\n".join(i.line() for i in items))
    if steam_missing:
        text += "\n\nNot downloaded here (install them from Steam if you want them): " + ", ".join(steam_missing)
    if good:
        text += ("\n\nInstall them: one after the other; each comes from the address shown; Windows may ask for "
                 "administrator rights (UAC) or Linux for your password. By installing you accept each "
                 "program's license.")
        text += ("" if install_only else " Then all tests run.\nRun only installed tests: nothing is downloaded.")
    return text


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
        self.install_only = self.confirmed = False
        self.lines: List[str] = []
        self.checked.connect(self._on_checked)
        self.installed.connect(self._on_installed)

    def busy(self) -> bool:
        return self.state.running

    def start(self, entries: List[TestEntry], install_only: bool = False, confirmed: bool = False) -> bool:
        """`confirmed`: the owner already answered the list on the remote page (install only, no window here)."""
        page = self.window.tests_page
        if self.busy() or page.busy:
            return False
        self.install_only, self.confirmed = install_only or confirmed, confirmed
        install_only = self.install_only
        items, self.steam_missing = collect(entries, page.host)
        if not items:
            if install_only:
                self._show("")
                self._end("Nothing to install: the selected tests have no one-click install here.")
                return False
            return self._run_tests()
        self._show(f"Checking the {len(items)} tests that are not installed...")
        self.start_thread(lambda: self.checked.emit(self._check(items)))
        return True

    def _check(self, items: List[Item]) -> List[Item]:
        return check_items(items, self.describe_fn, self.linux_commands)

    def _show(self, text: str):
        self.state = RunState(True, 0, None, text)
        page = self.window.tests_page
        page.watch(lambda: self.state, lambda: None)
        page.stop_btn.setEnabled(False)         # an installer that is running is not cut off

    def _end(self, text: str):
        self.state = RunState(False, 0, None, text)

    def _on_checked(self, items: List[Item]):
        good = [i for i in items if not i.problem]
        text = question(items, self.steam_missing, self.install_only)
        if self.install_only and not good:
            self._end("Nothing was installed: " + " ".join(i.line()[2:] for i in items))
            return
        if self.confirmed:
            choice = INSTALL                # answered on the remote page
        else:
            choice = (self.ask(self.window, text, install_only=True) if self.install_only
                      else self.ask(self.window, text))
        if self.install_only and choice != INSTALL:
            self._end("Cancelled; nothing was downloaded.")
            return
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
                if item.plan["kind"] == "zip":
                    lines.append(self.install_zip(item).message)
                elif item.plan["kind"] == "lmstudio":
                    from app.ai.model_set import install
                    lines.append(install(item.plan["test_id"]).message)
                elif item.package is not None:
                    out = self.install_fn(item.package)
                    lines.append(out.message)
                else:
                    from app.install import chrome_linux
                    what = item.plan.get("label") or item.plan["package"]
                    why = chrome_linux.prepare(item.plan)
                    result = self.run_commands(item.commands) if why is None else why
                    chrome_linux.cleanup(item.plan)
                    lines.append(f"{what} installed." if result == "done" else f"{what} was not installed: {result}")
            except Exception as e:
                logger.exception("Install failed")
                lines.append(f"{item.entry.name}: the install failed ({e})")
        return lines

    def install_zip(self, item: Item) -> "winget.Outcome":
        from app.install import direct
        from app.ui.install_flow import THEN
        outcome = direct.install(item.entry.name, item.plan)
        then = THEN.get(item.plan.get("then", ""))
        if outcome.ok and then:
            name = item.entry.name

            def progress(text):
                self.state = RunState(True, self.state.done, self.state.total, f"{name}: {text}")
            outcome = then[1](direct.target_folder(item.plan) / item.plan["exe"], progress)
        return outcome

    def _on_installed(self, lines: List[str]):
        self.lines = list(lines)
        self.state = RunState(True, 0, None, " ".join(lines) + " Checking the installed tests again...")
        self.window.after_scan.append(self._after_scan)
        self.window.start_scan()

    def _after_scan(self):
        if self.install_only:
            self._end("Installs done: " + " ".join(self.lines) + " Tick the tests and run them when you want.")
            return
        self._end("Installs done. Starting the tests...")
        self._run_tests()

    def _run_tests(self) -> bool:
        runnable = [e for e in self.window.tests_page.section_entries() if e.can_run() and not e.waits_for_typed_score()]
        if not runnable:
            QMessageBox.information(self.window, "Run all tests", "No test can run on this PC yet.")
            return False
        self.window.run_tests(runnable)
        return True

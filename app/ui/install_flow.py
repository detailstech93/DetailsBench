"""The Install button on the Tests screen (app/install/winget.py does the work).

winget: ask winget about the package in the background, show what will be downloaded (name, publisher, URL, size,
SHA256) and ask; only after Yes is it installed, still in the background, with the moving bar on the Tests screen.
Then the app scans again so the test shows as installed. Linux: the distribution's package manager through
pkexec, after the owner saw the exact commands. A Steam game opens Steam's own install dialog.

From the remote page (app/ui/remote_control.py) the same question is shown on the phone first (question_text);
the owner's Install there is the answer, so start(entry, confirmed=True) asks nothing at the PC and shows no
message box there (what happened is in the progress text the page reads). A vendor zip, an LM Studio model, winget
and Linux's package manager go this way (owner, 2026-10-21: apt and .deb from the phone too); Linux's password is
still typed at the PC, in the system's own prompt (the page says so and never asks for it). Steam stays at the PC.
"""

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from app.catalog import TestEntry
from app.install import plans, winget
from app.ui.tests_page import RunState, install_plan

logger = logging.getLogger(__name__)


def _blender_text() -> str:
    from app.pro import blender_benchmark as bb
    return bb.CONTENT_NOTE.format(v=bb.BLENDER_VERSION) + f" They are kept in {bb.cache_parent()}."


def _blender_download(exe, progress):
    from app.pro import blender_benchmark as bb
    return bb.download_content(exe, progress=progress)


def _ncurses_text() -> str:
    from app.pro import passmark
    return passmark.ncurses_install_text()


def _ncurses_install(exe, progress):
    from app.pro import passmark
    return passmark.install_ncurses5(progress)


def _handbrake_text() -> str:
    from app.pro import handbrake
    return handbrake.cli_install_text()


def _handbrake_install(clip, progress):
    from app.pro import handbrake
    return handbrake.install_cli(clip, progress)


# what the same Install button downloads after a "zip" tool ("then" in data/installers.py): (text for the
# question, download(exe, progress) -> winget.Outcome)
THEN = {"blender_content": (_blender_text, _blender_download),
        "ncurses5": (_ncurses_text, _ncurses_install),
        "handbrake_cli": (_handbrake_text, _handbrake_install)}


REMOTE_KINDS = ("zip", "lmstudio", "winget", "linux")
WINGET_NOTE = "\n\nWindows may show an administrator prompt on the PC during the install: someone must press Yes there."
LINUX_NOTE = ("\n\nThe PC asks for your password during the install, in the system's own prompt: someone must type "
              "it at the PC. This page never asks for it or sends it.")


def linux_question(name: str, plan: dict, commands) -> str:
    """The question before a Linux install (the distribution's package manager, or Chrome's checked .deb)."""
    shown = "\n".join(" ".join(c) for c in commands)
    if plan.get("fetch") == "chrome":
        from app.install.chrome_linux import question
        return question(Path(plan["package"])) + f"\n\nCommands:\n{shown}"
    return (f"Install {name} with your distribution's package manager?\n\n{shown}\n\nThe package "
            f"{plan['package']!r} comes from your distribution's own servers; the system asks for your password "
            f"(pkexec). This package name was not checked on a real {plan['family']} machine yet: if it is not "
            f"found, nothing is installed.")


def question_text(entry: TestEntry, host=None, describe=winget.describe):
    """(the question the Install button would ask, "") or (None, why it cannot be installed from the phone). May
    take a few seconds (winget is asked about the package): call it off the Qt thread."""
    plan = install_plan(entry, host)
    if plan is None:
        return None, "the app cannot install it (only its official download page)"
    kind = plan.get("kind", "winget")
    if kind not in REMOTE_KINDS:
        return None, "Steam installs it at the PC" if kind == "steam" else f"{kind} installs only at the PC"
    if kind == "linux":
        commands, problem = plans.linux_commands(plan["family"], plan["package"])
        if problem:
            return None, problem
        return linux_question(entry.name, plan, commands) + LINUX_NOTE, ""
    if kind == "zip":
        from app.install import direct
        bad = direct.problem(plan)
        if bad:
            return None, bad
        text = direct.confirm_text(entry.name, plan)
        then = THEN.get(plan.get("then", ""))
        return (text + ("\n\n" + then[0]() if then else "")), ""
    if kind == "lmstudio":
        from app.ai import model_set
        return model_set.install_text(plan["test_id"]), ""
    pkg = describe(entry.id)
    if pkg.problem:
        return None, pkg.problem
    return winget.confirm_text(pkg) + WINGET_NOTE, ""


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
        self.remote = False                 # started from the remote page: answered there, no window at the PC
        self.described.connect(self._on_described)
        self.finished.connect(self._on_finished)

    def busy(self) -> bool:
        return self.state.running

    def start(self, entry: TestEntry, confirmed: bool = False) -> bool:
        """`confirmed`: the owner already answered the question on the remote page."""
        if self.busy():
            return False
        plan = install_plan(entry, self.window.tests_page.host)
        if plan is None:
            return False
        if confirmed and plan.get("kind", "winget") not in REMOTE_KINDS:
            return False
        self.entry, self.remote = entry, confirmed
        if plan["kind"] == "linux":
            return self._linux(entry, plan)
        if plan["kind"] == "steam":
            url = winget.steam_install_url(plan["app_id"])
            if not self._ask("Install", f"Open Steam's install dialog for {entry.name}?\n\n"
                             f"Steam shows the size and the folder there and installs it from Steam's servers "
                             f"({url}). When Steam has finished, press 'I installed something: check again'."):
                return False
            self.window.tests_page.open_url(url)
            return True
        if plan["kind"] == "zip":
            return self._zip(entry, plan)
        if plan["kind"] == "lmstudio":
            return self._lmstudio(entry, plan)
        self._show(f"Asking Windows Package Manager about {entry.name}...")
        self.start_thread(lambda: self.described.emit(self._safe(lambda: self.describe_fn(entry.id),
                                                                 winget.Package(entry.id, ""))))
        return True

    def _linux(self, entry: TestEntry, plan: dict) -> bool:
        commands, problem = self.linux_commands(plan["family"], plan["package"])
        if problem:
            self._warn(f"{entry.name} cannot be installed by the app: {problem}.")
            return False
        if not self._ask("Install", linux_question(entry.name, plan, commands)):
            return False
        what = plan.get("label") or plan["package"]
        self._show(f"Installing {what} (enter your password in the system prompt)...")

        def work():
            from app.install import chrome_linux
            if plan.get("fetch"):
                self._progress(f"Downloading {what}...")
            why = chrome_linux.prepare(plan)
            text = self.run_commands(commands) if why is None else why
            chrome_linux.cleanup(plan)
            ok = text == "done"
            self.finished.emit(winget.Outcome(ok, f"{what} installed." if ok else
                                              f"{what} was not installed: {text}"))
        self.start_thread(lambda: self._safe(work, winget.Outcome(False, "")))
        return True

    def _zip(self, entry: TestEntry, plan: dict) -> bool:
        bad = self.direct.problem(plan)
        if bad:
            self._warn(f"{entry.name} cannot be installed by the app: {bad}.")
            return False
        then = THEN.get(plan.get("then", ""))
        if not self.remote:
            text = self.direct.confirm_text(entry.name, plan)
            if then:
                text += "\n\n" + then[0]()
            if not self._ask("Install", text):
                return False
        what = f"{plan['product']} {plan['version']}"
        self._show(f"Downloading {what}...")
        failed = winget.Outcome(False, "")

        def downloaded(done: int, total: int):     # the green bar (and the remote page) follow the download
            if total > 0:
                self.state = RunState(True, 0, None, f"Downloading {what}: {done / 1e6:,.0f} of {total / 1e6:,.0f} MB",
                                      share=min(1.0, done / total))

        def work():
            outcome = self.direct.install(entry.name, plan, progress=downloaded)
            if outcome.ok and then:
                exe = self.direct.target_folder(plan) / plan["exe"]
                outcome = then[1](exe, self._progress)
            return outcome
        self.start_thread(lambda: self.finished.emit(self._safe(work, failed)))
        return True

    def _lmstudio(self, entry: TestEntry, plan: dict) -> bool:
        from app.ai import model_set
        if not self.remote and not self._ask("Install", model_set.install_text(plan["test_id"])):
            return False
        self._show(f"Downloading {entry.name} with LM Studio...")
        failed = winget.Outcome(False, "")
        self.start_thread(lambda: self.finished.emit(self._safe(
            lambda: model_set.install(plan["test_id"], self._progress), failed)))
        return True

    def start_lmstudio(self, missing=None, install=None) -> bool:
        """Linux: LM Studio's official install.sh (app/ai/lmstudio_linux.py), libatomic first when it is missing."""
        from app.ai import lmstudio_linux
        if self.busy():
            return False
        first: list = []
        if (missing or lmstudio_linux.libatomic_missing)():
            host = self.window.tests_page.host or plans.this_host()
            package = {"debian": "libatomic1", "fedora": "libatomic", "arch": "gcc-libs", "suse": "libatomic1"}.get(
                host.family or "")
            if not package:
                self._warn("LM Studio needs the system library libatomic, which is missing; install it with your "
                           "package manager, then press Install LM Studio again.")
                return False
            first, problem = self.linux_commands(host.family, package)
            if problem:
                self._warn(f"LM Studio needs libatomic, which cannot be installed by the app: {problem}.")
                return False
        if not self._ask("Install", lmstudio_linux.question(first)):
            return False
        self.entry, self.remote = None, False
        self._show("Installing LM Studio (it downloads about 1 GB; enter your password if asked)...")

        def work():
            if first:
                text = self.run_commands(first)
                if text != "done":
                    return winget.Outcome(False, f"LM Studio was not installed: libatomic could not be installed ({text}).")
                self._progress("Installing LM Studio (it downloads about 1 GB)...")
            return (install or lmstudio_linux.install)()
        self.start_thread(lambda: self.finished.emit(self._safe(work, winget.Outcome(False, ""))))
        return True

    def _ask(self, title: str, text: str) -> bool:
        return True if self.remote else self.ask(self.window, title, text)

    def _warn(self, text: str):
        if self.remote:
            logger.warning("Remote install: %s", text)
        else:
            QMessageBox.warning(self.window, "Install", text)

    def _progress(self, text: str):
        self.state = RunState(True, 0, None, text)     # read by the Tests screen's timer

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
            self._warn(f"{self.entry.name} cannot be installed by the app: {pkg.problem}\n\nUse the official "
                       "download page instead.")
            return
        if not self._ask("Install", winget.confirm_text(pkg)):
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
            self._warn(outcome.message)

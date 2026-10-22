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

Waiting in line (owner, 2026-10-22): while something installs, Install still works for the downloads that need no
system prompt (a vendor zip, an LM Studio model: QUEUE_KINDS); the question is asked at once and the download waits
in line, its button greyed out as "In line (n)". An installer that needs a prompt (UAC, the password) never waits:
it could come up with nobody at the PC. Stop ends everything (a download at once; an installer that is running
finishes first, it is never cut off) and empties the line; Skip ends the download going on and starts the next.
"""

import logging
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

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
QUEUE_KINDS = ("zip", "lmstudio")       # downloads with no system prompt: they can wait in line
STOPPED = "stopped by user"


class Stopped(Exception):
    """Stop or Skip pressed during a download (raised from its progress callback)."""

    def __init__(self):
        super().__init__(STOPPED)


def queueable(entry: TestEntry, host=None) -> bool:
    """Can this install wait in line behind the one going on?"""
    from app.ui import tests_page
    try:
        plan = install_plan(entry, host)
        return plan is not None and plan.get("kind") in QUEUE_KINDS and not tests_page.needs_lmstudio(entry, host)
    except Exception:
        return False
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
        self.asked = False                  # waited in line: its question was answered when it was put there
        self.after_lmstudio: Optional[TestEntry] = None     # a model to download once LM Studio is installed
        self.queue: List[Tuple[TestEntry, bool]] = []       # (entry, from the remote page), waiting in line
        self.cancel = threading.Event()     # the download going on: Stop or Skip ends it
        self.cancellable = False            # the step going on is such a download (not an installer)
        self.stopping = False
        self.lines: List[str] = []          # what each install of this line said
        self.any_ok = False
        self.described.connect(self._on_described)
        self.finished.connect(self._on_finished)

    def busy(self) -> bool:
        return self.state.running

    def start(self, entry: TestEntry, confirmed: bool = False, queued: bool = False, keep: bool = False) -> bool:
        """`confirmed`: the owner already answered the question on the remote page. `queued`: it waited in line
        (its question was answered then); the line and what it said so far are kept."""
        if self.busy():
            return False
        plan = install_plan(entry, self.window.tests_page.host)
        if plan is None:
            return False
        if confirmed and plan.get("kind", "winget") not in REMOTE_KINDS:
            return False
        if not (queued or keep):
            self.queue, self.lines, self.stopping, self.any_ok = [], [], False, False
        self.cancel = threading.Event()
        self.entry, self.remote, self.asked = entry, confirmed, queued
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
        self._show(f"Downloading {what}...", cancellable=True)
        failed = winget.Outcome(False, "")
        cancel = self.cancel

        def downloaded(done: int, total: int):     # the green bar (and the remote page) follow the download
            if cancel.is_set():
                raise Stopped()
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
        from app.ui import tests_page
        host = self.window.tests_page.host
        if tests_page.needs_lmstudio(entry, host):
            # LM Studio is missing (Radxa board, 2026-10-21: the model's Install only said "lms was not found"):
            # on Linux it is installed first (asked at the PC: it may need the password for libatomic), then the model
            if self.remote or not tests_page.lmstudio_one_click(host):
                self._warn(f"{entry.name} downloads through LM Studio, which is not installed here: "
                           + ("press Install LM Studio at the PC first." if tests_page.lmstudio_one_click(host)
                              else f"install it from {tests_page.LMSTUDIO_URL}, open it once, then Rescan."))
                return False
            self.after_lmstudio = entry
            if not self.start_lmstudio():
                self.after_lmstudio = None
                return False
            return True
        if not self._ask("Install", model_set.install_text(plan["test_id"])):
            return False
        self._show(f"Downloading {entry.name} with LM Studio...", cancellable=True)
        failed = winget.Outcome(False, "")
        cancel = self.cancel
        self.start_thread(lambda: self.finished.emit(self._safe(
            lambda: model_set.install(plan["test_id"], self._progress, stop=cancel), failed)))
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
        self.entry, self.remote, self.asked = None, False, False
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
        return True if self.remote or self.asked else self.ask(self.window, title, text)

    def _warn(self, text: str):
        if self.remote or self.asked:
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

    def _show(self, text: str, cancellable: bool = False):
        self.cancellable = cancellable
        self.state = RunState(True, 0, None, text)
        self.window.tests_page.watch(lambda: self.state, self._stop, self._skip)
        self._mark()

    # ---- the line (owner, 2026-10-22) ---------------------------------------------------------------------------
    def queue_open(self) -> bool:
        return self.busy() and not self.stopping

    def queued_ids(self) -> List[str]:
        return [e.id for e, _ in self.queue]

    def enqueue(self, entry: TestEntry, remote: bool = False) -> Tuple[bool, str]:
        """Put a download in line behind the install going on (its question was answered already)."""
        if not self.queue_open():
            return False, "Nothing is installing now: press Install again."
        if entry.id in self.queued_ids() or (self.entry is not None and self.entry.id == entry.id):
            return False, f"{entry.name} is already in line."
        self.queue.append((entry, remote))
        self._mark()
        return True, f"{entry.name} is in line ({len(self.queue)}): it downloads after the one going on."

    def _mark(self):
        mark = getattr(self.window.tests_page, "show_queue", None)
        if mark is not None:
            mark(self.queued_ids())

    def _stop(self):
        """Stop: everything ends (a download at once, an installer that is running finishes) and the line empties."""
        self.stopping = True
        self.queue.clear()
        self.cancel.set()
        self._mark()
        self.state = RunState(True, 0, None, "Stopping: a download ends now; an installer that is running "
                                             "finishes first (it is never cut off)...")

    def _skip(self):
        """Skip: the download going on ends and the next in line starts."""
        name = self.entry.name if self.entry is not None else "LM Studio"
        if not self.cancellable:
            self.state = RunState(True, 0, None, f"{name} is installing and cannot be cut off: the next in line "
                                                 "starts when it ends.")
            return
        self.cancel.set()
        self.state = RunState(True, 0, None, f"Skipping {name}" + (": the next in line starts..." if self.queue
                                                                    else "..."))

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
        cut = self.cancel.is_set()          # Stop or Skip: no message box for it
        model, self.after_lmstudio = self.after_lmstudio, None
        if outcome.ok and model is not None and not self.stopping:   # LM Studio is in: now its model
            self._end(outcome.message)
            if self.start(model, keep=True):
                return
        self.lines.append(outcome.message)
        self.any_ok = self.any_ok or outcome.ok
        if not outcome.ok and not cut and not self.queue:
            self._warn(outcome.message)
        while self.queue and not self.stopping:     # the next in line
            entry, remote = self.queue.pop(0)
            self._end(outcome.message)
            if self.start(entry, confirmed=remote, queued=True):
                return
            self.lines.append(f"{entry.name} did not start.")
        text = " ".join(self.lines)
        self._mark()
        self._end(text + (" Checking the installed tests again..." if self.any_ok else ""))
        if self.any_ok:
            self.window.start_scan()

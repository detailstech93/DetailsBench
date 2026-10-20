"""Remote start in the app (plan Phase D): the window that switches the server on and off, and the answers the page
gets (app/remote/server.py). Every answer is made on the Qt thread (MainThread), as if the owner had pressed the
buttons on the Tests screen; nothing on the PC waits for a click when the page starts a run:
- every test for this PC is listed on the page; the ones that need someone at the PC are shown with the reason and
  cannot be ticked there (a typed score, a license still to accept, the llama-benchy settings window, a Steam game
  played by hand, a test that is not installed);
- tests that ask Windows for administrator rights (PassMark: once per run; game benchmarks: PresentMon at each run)
  can be started, with a warning: the UAC prompt shows on the PC and someone must press Yes there;
- the counts come from the page, game tests use the game preset chosen last on the PC, a stress test its last
  length;
- the start question is answered by the page's Start button (its text is kept in the log);
- LibreHardwareMonitor is used only when it already runs (otherwise the run goes ahead without CPU sensors; its
  question would wait at the PC);
- the page follows the run: the test and run going on now, each test's state and scores so far, the time gone and
  left, and the report when it is written (shown on the page, with the earlier reports of the reports folder);
- a missing test whose Install needs nobody at the PC (a vendor zip, an LM Studio model, winget) can be installed
  from the page: the page shows the same question the Install button asks, and the owner's Install there is the
  answer (app/ui/install_flow.py); a license not accepted yet can be read and accepted on the page too.
"""

import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout)

from app.catalog import GROUPS, TestEntry
from app.remote import server as remote
from app.ui.tests_page import elapsed_text, progress_percent

logger = logging.getLogger(__name__)

SETTINGS_KEY = "remote_start"          # settings.json: {"port": ..., "local_only": ...}; never "on"
GROUP_TITLES = dict(GROUPS)
# a report in the reports folder, or in one run's folder there (app/report_set.py): "<run folder>/<name>.html"
REPORT_NAME = re.compile(r"^(?:[\w\-. ]{1,100}/)?[\w\-. ]{1,200}\.html$")
MAX_REPORTS = 15
TITLE = re.compile(r"<title>(?:Benchmark report: )?(.*?)</title>", re.S)


def report_title(path: Path) -> str:
    """What a report is about (the tests, from its <title>), for the page's list; "" when unreadable."""
    import html
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except OSError:
        return ""
    m = TITLE.search(head)
    return html.unescape(m.group(1)).strip()[:160] if m else ""


def tests_key(entries: List[TestEntry]) -> str:
    """Changes when a test is installed, removed or its license accepted: the page then reads the tests again."""
    import zlib
    parts = []
    for e in entries:
        lic = getattr(e.ref, "license", None)
        parts.append(f"{e.id}:{e.state}:{bool(lic) and bool(e.ref.license_ok())}")
    return format(zlib.crc32("|".join(parts).encode("utf-8")), "08x")
ADMIN_WARNING = ("Windows asks once for administrator rights at the start, on the PC: someone must press Yes there "
                 "(No: each such test asks for itself).")
GAME_WARNING = ("A game: it opens on the PC's screen, and Windows asks for administrator rights on the PC at each "
                "run (frame capture): someone must press Yes there.")

GREEN, GREY = "#2e7d32", "#616161"
_SWITCH = ("QPushButton {{ background: {0}; color: white; font-size: 15px; font-weight: bold; border: 3px solid {3}; "
           "border-radius: 8px; padding: 10px 18px; }} QPushButton:hover {{ background: {1}; }} "
           "QPushButton:pressed {{ background: {2}; padding-top: 13px; padding-bottom: 7px; }}")
SWITCH_ON = _SWITCH.format(GREEN, "#388e3c", "#1b5e20", GREEN)
SWITCH_OFF = _SWITCH.format(GREY, "#757575", "#424242", GREY)
SWITCH_FLASH = 450                     # ms the switch glows after a click: the click was taken
SWITCH_ON_FLASH = _SWITCH.format(GREEN, "#388e3c", "#1b5e20", "#b9f6ca")
SWITCH_OFF_FLASH = _SWITCH.format(GREY, "#757575", "#424242", "#e0e0e0")
PILL = "QLabel {{ background: {0}; color: white; font-weight: bold; border-radius: 10px; padding: 3px 12px; }}"
CARD = "QFrame#card { border: 1px solid #c8ced8; border-radius: 8px; }"
BUTTON = "QPushButton { padding: 6px 14px; }"


class MainThread(QObject):
    """Runs a function on the Qt thread for the server's threads and waits for its answer."""
    call = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.call.connect(self._run)

    @Slot(object)
    def _run(self, job):
        job()

    def run(self, fn: Callable, timeout: float = 20.0):
        if QThread.currentThread() is self.thread():
            return fn()
        box, done = {}, threading.Event()

        def job():
            try:
                box["value"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                done.set()
        self.call.emit(job)
        if not done.wait(timeout):
            raise TimeoutError("the app on the PC is busy")
        if "error" in box:
            raise box["error"]
        return box["value"]


def remote_listing(entries: List[TestEntry], platform: str = sys.platform,
                   elevated: Optional[bool] = None) -> List[Tuple[TestEntry, str, str]]:
    """Every test for this PC as (entry, why it cannot start from the page or "", a warning or ""), in the order of
    the Tests screen's groups. No administrator warning while the app runs as administrator (nothing asks then)."""
    from app import elevation
    from app.ui.run_tests import game_of
    asks = platform == "win32" and not (elevation.is_elevated(platform) if elevated is None else elevated)
    out = []
    for e in entries:
        if not e.compatible:
            continue                            # not for this PC at all
        ref, why, warning = e.ref, "", ""
        if e.waits_for_typed_score():
            why = "Its score is typed in on the PC: start it there."
        elif e.source != "workload":
            why = "It needs someone at the PC (a game played by hand): start it there."
        elif not e.can_run():
            why = f"Not ready on the PC: {e.state_reason or e.state}"[:160]
        elif getattr(ref, "license", None) and not ref.license_ok():
            why = "Its license agreement is not accepted yet: read it with Accept license."
        elif hasattr(ref, "configure") and getattr(ref, "asks_settings", True):
            why = "Its settings are chosen in a window on the PC: start it there."
        elif asks and game_of(e) is not None:
            warning = GAME_WARNING
        elif asks and getattr(ref, "needs_admin", False):
            warning = ADMIN_WARNING
        out.append((e, why, warning))
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    return sorted(out, key=lambda x: (order.get(x[0].group, len(order)), x[0].name.lower()))


ADMIN_NOTICE = (
    "The app is not running as administrator.\n\n"
    "If you want every test to run remotely, restart the app as administrator first. PassMark and the game "
    "benchmarks need administrator rights: without them Windows asks on this PC (a UAC prompt), and nobody can "
    "answer it remotely, so the run waits there.\n\n"
    "The other tests run remotely either way.")


def admin_notice(platform: str = sys.platform, elevated: Optional[bool] = None) -> Optional[str]:
    """The notice the Remote button shows first (owner, 2026-10-20), or None (not Windows, or already admin)."""
    if platform != "win32":
        return None
    from app import elevation
    return None if (elevation.is_elevated(platform) if elevated is None else elevated) else ADMIN_NOTICE


def admin_first(window, choose: Optional[Callable[[str], bool]] = None, restart: Optional[Callable] = None,
                notice: Callable[[], Optional[str]] = admin_notice) -> bool:
    """The Remote button, before its window: the notice, if any, with Restart as administrator or Continue. True
    when the app is restarting (this window closes; the remote window is not opened)."""
    text = notice()
    if not text:
        return False
    if choose is None:
        def choose(message: str) -> bool:
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(QMessageBox.Information, "Remote: administrator rights", message, parent=window)
            again = box.addButton("Restart as administrator", QMessageBox.AcceptRole)
            box.addButton("Continue", QMessageBox.RejectRole)
            box.exec()
            return box.clickedButton() is again
    if not choose(text):
        return False
    if restart is None:
        from app.ui import admin_restart

        def restart():                          # the notice was the question: no second one
            return admin_restart.restart(window, ask=lambda w, t: True)
    return bool(restart())


def remote_entries(entries: List[TestEntry], platform: str = sys.platform) -> List[TestEntry]:
    """The tests the page may start."""
    return [e for e, why, _ in remote_listing(entries, platform) if not why]


def remote_start(window, ids: List[str], repetitions: int, warmup_runs: int, cooldown_seconds: int,
                 launch: Optional[Callable] = None) -> Tuple[bool, str]:
    """Start the tests the page picked, like Run selected on the Tests screen but without its windows."""
    from app.ui import run_tests
    from app.ui.automation_panel import EXTRA_RUNS
    panel, page = window.automation_panel, window.tests_page
    if panel.is_running() or window.frame_panel.is_running() or page.busy or panel.lhm.busy():
        return False, "A run is already going on; wait for it or stop it first."
    offered = {e.id: e for e in remote_entries(page.entries)}
    missing = [i for i in ids if i not in offered]
    if missing:
        return False, "These tests cannot start from here now: " + ", ".join(missing)
    entries = [offered[i] for i in ids]
    data_dir = window.frame_panel.data_dir
    games = [g for g in (run_tests.game_of(e) for e in entries) if g]
    preset = None
    if games:
        preset = run_tests.last_preset(data_dir)
        run_tests.save_preset(data_dir, games, preset, run_tests.read_offered(games, data_dir))
    minutes = None
    if any(e.id.startswith("stress_") for e in entries):
        from app.stress.workload import stress_minutes
        minutes = stress_minutes()
    choice = run_tests.RunChoice(repetitions, warmup_runs, cooldown_seconds, preset, minutes, EXTRA_RUNS)
    run_tests._save_plan(data_dir, entries, choice)
    lhm = panel.lhm
    try:
        lhm_ready = lhm.platform_name != "win32" or lhm.declined or lhm.running()
    except Exception:
        lhm_ready = False
    tools_ready = not lhm_ready                     # not running: no question at the PC, no CPU sensors

    def confirm(text: str) -> bool:
        logger.info("Run started from the remote page:\n%s", text)
        return True
    started = (launch or run_tests.launch)(window, entries, choice, confirm=confirm, tools_ready=tools_ready)
    if not started:
        return False, panel.status_label.text() or "Not started."
    window.status_label.setText(f"Started from the remote page: {len(entries)} test(s).")
    note = "" if lhm_ready or not panel.telemetry_box.isChecked() else \
        " Without CPU sensors: LibreHardwareMonitor is not running on the PC."
    admin = [e for e in entries if remote_listing([e])[0][2]]
    if admin:
        note += " Windows will ask for administrator rights on the PC: someone must press Yes there."
    return True, f"Started {len(entries)} test(s), {repetitions} measured run(s) each.{note}"


def _score(value) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def plan_tests(panel, db, running: bool) -> List[dict]:
    """Each test of the plan going on now (or the last one): its state, runs and scores so far."""
    runner = getattr(getattr(panel, "worker", None), "runner", None)
    sid = getattr(panel, "last_session_id", None)
    if runner is None or sid is None or getattr(runner, "session_id", sid) != sid:
        return []
    try:
        rows = (db.get_session(sid) or {}).get("results", [])
    except Exception:
        logger.exception("Could not read the session's results")
        rows = []
    now = panel.step[2] if running else ""
    out = []
    for item in runner.plan:
        wl = item.workload
        name = wl.display_name
        mine = [r for r in rows if r.get("benchmark_name") == wl.id]
        measured = [r for r in mine if not r.get("is_warmup")]
        good = [r for r in measured if (r.get("status") or "completed") == "completed" and r.get("score") is not None]
        if running and now.startswith(name + ":"):
            state = "running"
        elif running:
            state = "waiting" if not mine else "running" if len(measured) < item.repetitions else "done"
        else:
            state = "done" if good else "failed" if measured else "not run"
        scores = [float(r["score"]) for r in good]
        out.append({"name": name, "state": state, "runs": len(measured), "planned": item.repetitions,
                    "warmups": item.warmup_runs, "failed": len(measured) - len(good),
                    "scores": [_score(s) for s in scores], "unit": (good[0].get("unit") if good else "") or "",
                    "average": _score(sum(scores) / len(scores)) if scores else ""})
    return out


class RemoteApi:
    """What the page asks (app/remote/server.py), answered on the Qt thread."""

    def __init__(self, window, main: MainThread):
        self.window, self.main = window, main

    def info(self) -> dict:
        return self.main.run(self._info)

    def _info(self) -> dict:
        w = self.window
        si = w.system_info or {}
        specs = [(k, str(si.get(key))) for k, key in (("Processor", "cpu_brand"), ("Cores", "core_count"),
                                                     ("Memory (GB)", "ram_size_gb"), ("Graphics", "gpu_name"),
                                                     ("System", "operating_system")) if si.get(key) is not None]
        tests = [{"id": e.id, "name": e.name, "group": GROUP_TITLES.get(e.group, e.group.title()),
                  "minutes": e.expected_minutes, "verified": bool(e.verified), "can_start": not why,
                  "why": why, "warning": warning, "install": self._can_install(e),
                  "license": bool(e.can_run() and getattr(e.ref, "license", None) and not e.ref.license_ok())}
                 for e, why, warning in remote_listing(w.tests_page.entries)]
        profile = w.selected_profile()
        return {"specs": specs, "tests": tests,
                "defaults": {"repetitions": max(1, int(profile.repetitions)), "warmup_runs": int(profile.warmup_runs),
                             "cooldown_seconds": int(profile.cooldown_seconds)},
                "note": "A failed run is stored as failed and run once more; uneven scores get up to a few extra "
                        "runs. Game tests use the game settings chosen last on the PC."}

    def _can_install(self, e: TestEntry) -> bool:
        from app.catalog import MISSING
        from app.ui.install_flow import REMOTE_KINDS
        from app.ui.tests_page import install_plan
        if not e.compatible or e.state != MISSING:
            return False
        try:
            plan = install_plan(e, self.window.tests_page.host)
        except Exception:
            return False
        return plan is not None and plan.get("kind", "winget") in REMOTE_KINDS

    def _entry(self, test_id: str) -> Tuple[Optional[TestEntry], object]:
        page = self.window.tests_page
        return next((e for e in page.entries if e.id == test_id), None), page.host

    def question(self, test_id: str, what: str) -> Tuple[bool, str]:
        """The question the PC would ask before an install or about a license, to show on the page."""
        entry, host = self.main.run(lambda: self._entry(test_id))
        if entry is None:
            return False, "No such test on this PC."
        if what == "license":
            lic = getattr(entry.ref, "license", None)
            if not lic or entry.ref.license_ok():
                return False, "Nothing to accept for this test."
            return True, lic[2]()
        if not self.main.run(lambda: self._can_install(entry)):
            return False, "This test cannot be installed from here."
        from app.ui.install_flow import question_text
        text, why = question_text(entry, host)          # winget is asked here, off the Qt thread
        return (True, text) if text else (False, f"Cannot be installed from here: {why}")

    def act(self, test_id: str, what: str) -> Tuple[bool, str]:
        """The owner answered Yes on the page: accept the license, or start the install (no window at the PC)."""
        def run():
            w = self.window
            entry, _ = self._entry(test_id)
            if entry is None:
                return False, "No such test on this PC."
            if what == "license":
                lic = getattr(entry.ref, "license", None)
                if not lic:
                    return False, "Nothing to accept for this test."
                try:
                    entry.ref.accept_license()
                except (OSError, ValueError) as e:
                    return False, f"The answer could not be saved: {e}"
                logger.info("License of %s accepted from the remote page", lic[1])
                return True, f"{lic[1]}: license accepted."
            if w.tests_page.busy or w.automation_panel.is_running() or w.install_flow.busy():
                return False, "Something is going on at the PC; wait for it first."
            if not self._can_install(entry):
                return False, "This test cannot be installed from here."
            if not w.install_flow.start(entry, confirmed=True):
                return False, "The install did not start."
            logger.info("Install of %s started from the remote page", entry.id)
            return True, f"Installing {entry.name}: follow it under Now."
        return self.main.run(run)

    def state(self) -> dict:
        return self.main.run(self._state)

    def _state(self) -> dict:
        w = self.window
        page, panel = w.tests_page, w.automation_panel
        report = getattr(w, "last_report_path", None)
        report = Path(report) if report and Path(report).is_file() else None
        out = {"running": False, "text": page.progress_label.text() if not page.progress_box.isHidden() else "",
               "percent": None, "report": bool(report), "report_name": report.name if report else "",
               "step": "", "elapsed": "", "left": "", "tests": []}
        running = False
        if page.busy:
            s = page.get_state()
            running = s.running
            percent = progress_percent(s)
            out.update(running=s.running, text=s.text, percent=percent)
            if s.running and s.total:
                out["step"] = f"Run {min(s.done + 1, s.total)} of {s.total}"
        started = getattr(panel, "plan_started_at", None)
        if started is not None and (running or panel.is_running() or getattr(panel, "plan_ended_at", None)):
            ended = None if running else getattr(panel, "plan_ended_at", None)
            gone = (ended or time.monotonic()) - started
            out["elapsed"] = elapsed_text(gone)
            p = out["percent"]
            if running and p and 0 < p < 100 and gone > 5:
                out["left"] = "about " + elapsed_text(gone * (100 - p) / p)
        out["tests"] = plan_tests(panel, w.db, running)
        listed = self.reports()
        out["newest_report"] = listed[0]["name"] if listed else ""     # also after the app started again
        out["tests_key"] = tests_key(page.entries)
        return out

    def start(self, ids, repetitions, warmup_runs, cooldown_seconds) -> Tuple[bool, str]:
        return self.main.run(lambda: remote_start(self.window, ids, repetitions, warmup_runs, cooldown_seconds))

    def stop(self) -> str:
        def stop():
            page = self.window.tests_page
            if not page.busy:
                return "Nothing is running."
            page._stop()
            self.window.emergency_stop_all()
            return "Stopping: the current run is being cut off."
        return self.main.run(stop)

    def _folder(self) -> Optional[Path]:
        from app.ui import reports_folder
        return self.main.run(lambda: reports_folder.folder(self.window))

    def reports(self) -> List[dict]:
        """The newest reports in the reports folder (name and time), newest first."""
        folder = self._folder()
        if folder is None or not folder.is_dir():
            return []
        found = []
        for p in list(folder.glob("*.html")) + list(folder.glob("*/*.html")):
            name = p.relative_to(folder).as_posix()
            try:
                if p.is_file() and REPORT_NAME.match(name) and ".." not in name.split("/"):
                    found.append((p.stat().st_mtime, name))
            except OSError:
                continue
        found.sort(reverse=True)
        return [{"name": n, "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(t)),
                 "title": report_title(folder / n)} for t, n in found[:MAX_REPORTS]]

    def report(self, name: Optional[str] = None) -> Optional[str]:
        """The last report written (no name), or one of reports() by its name; None when there is none."""
        if name is None:
            last = self.main.run(lambda: getattr(self.window, "last_report_path", None))
            if last and Path(last).is_file():
                return Path(last).read_text(encoding="utf-8", errors="replace")
            listed = self.reports()
            if not listed:
                return None
            name = listed[0]["name"]
        if not REPORT_NAME.match(name) or name not in {r["name"] for r in self.reports()}:
            return None                         # only a report the folder listing offers, never another file
        path = self._folder() / name
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


class RemoteControl:
    """The server's on/off switch for the main window (off when the app starts and when it closes)."""

    def __init__(self, window, server_class=remote.RemoteServer):
        self.window = window
        self.main = MainThread(window)
        self.api = RemoteApi(window, self.main)
        self.server_class = server_class
        self.server = None

    def settings(self) -> dict:
        from app.paths import load_settings
        try:
            value = load_settings(self.window.frame_panel.data_dir)[0].get(SETTINGS_KEY)
        except Exception:
            value = None
        value = value if isinstance(value, dict) else {}
        port = value.get("port")
        return {"port": port if isinstance(port, int) and 1024 <= port <= 65535 else remote.DEFAULT_PORT,
                "local_only": value.get("local_only") is True}

    def running(self) -> bool:
        return self.server is not None and self.server.running()

    def start(self, local_only: bool, port: int) -> Tuple[bool, str]:
        self.stop()
        server = self.server_class(self.api, local_only=local_only, port=port)
        try:
            server.start()
        except OSError as e:
            return False, f"Remote mode could not open port {port}: {e}"
        self.server = server
        try:
            from app.paths import save_setting
            save_setting(self.window.frame_panel.data_dir, SETTINGS_KEY, {"port": port, "local_only": local_only})
        except (OSError, ValueError):
            logger.exception("Could not save the remote start settings")
        return True, ""

    def stop(self):
        server, self.server = self.server, None
        if server is not None:
            server.stop()


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #667085;")
    return label


class RemoteDialog(QDialog):
    """The remote mode window: one big switch, then the address and the code to type on the phone."""

    def __init__(self, control: RemoteControl, parent=None):
        super().__init__(parent)
        self.control = control
        self.setWindowTitle("Remote mode")
        self.setMinimumWidth(520)
        self.setStyleSheet(CARD)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("Remote mode")
        font = QFont(title.font())
        font.setPointSize(font.pointSize() + 6)
        font.setBold(True)
        title.setFont(font)
        self.pill = QLabel()
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.pill)
        layout.addLayout(top)
        layout.addWidget(_muted("Start and follow tests from a phone or another computer on the same Wi-Fi or "
                                "network, or from anywhere through Tailscale (use the 100.x address below; nothing "
                                "else from outside is let in). Off when the app starts and when it closes."))

        self.switch = QPushButton()                  # the on/off switch of remote mode
        self.switch.setCheckable(True)
        self.switch.setMinimumHeight(52)
        self.switch.setCursor(Qt.PointingHandCursor)
        self.switch.clicked.connect(self.toggle)
        layout.addWidget(self.switch)

        self.card = QFrame()
        self.card.setObjectName("card")
        grid = QGridLayout(self.card)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(QLabel("1. On the phone open:"), 0, 0, 1, 3)
        self.address = QLabel()
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        big = QFont(self.address.font())
        big.setPointSize(big.pointSize() + 4)
        big.setBold(True)
        self.address.setFont(big)
        self.copy_address = QPushButton("Copy")
        self.open_here = QPushButton("Open here")
        self.open_here.setToolTip("Open the page in this PC's browser")
        grid.addWidget(self.address, 1, 0, 1, 2)
        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_address)
        buttons.addWidget(self.open_here)
        grid.addLayout(buttons, 1, 2)
        grid.addWidget(QLabel("2. Type this code:"), 2, 0, 1, 3)
        self.code = QLabel()
        code_font = QFont("Consolas")
        code_font.setStyleHint(QFont.Monospace)
        code_font.setPointSize(big.pointSize() + 8)
        code_font.setBold(True)
        code_font.setLetterSpacing(QFont.AbsoluteSpacing, 6)
        self.code.setFont(code_font)
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.copy_code = QPushButton("Copy")
        grid.addWidget(self.code, 3, 0, 1, 2)
        grid.addWidget(self.copy_code, 3, 2, Qt.AlignLeft)
        grid.addWidget(_muted("The code is new every time remote mode is switched on. Windows may ask to allow the "
                              "app through the firewall: that answer is yours (the app never changes firewall "
                              "rules)."), 4, 0, 1, 3)
        self.copy_address.clicked.connect(lambda: self._copy(self.first_address()))
        self.copy_code.clicked.connect(lambda: self._copy(self.control.server.code if self.control.running() else ""))
        self.open_here.clicked.connect(self._open_here)
        layout.addWidget(self.card)

        box = QGroupBox("Settings")
        form = QGridLayout(box)
        form.setColumnStretch(1, 1)
        saved = control.settings()
        self.local_only = QCheckBox("This PC only (127.0.0.1: other devices cannot open it)")
        self.local_only.setChecked(saved["local_only"])
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(saved["port"])
        self.port.setMaximumWidth(110)
        form.addWidget(self.local_only, 0, 0, 1, 3)
        form.addWidget(QLabel("Port:"), 1, 0)
        form.addWidget(self.port, 1, 1, Qt.AlignLeft)
        form.addWidget(QLabel("Reports:"), 2, 0)
        self.reports = QLabel()
        self.reports.setWordWrap(True)
        self.reports.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_reports = QPushButton("Change...")
        self.change_reports.clicked.connect(self._change_reports)
        form.addWidget(self.reports, 2, 1)
        form.addWidget(self.change_reports, 2, 2)
        from app import elevation
        elevated = elevation.is_elevated()
        self.admin = QPushButton("Restart as administrator")
        self.admin.clicked.connect(self._restart_as_admin)
        self.admin.setVisible(sys.platform == "win32" and not elevated)
        if sys.platform == "win32":
            form.addWidget(QLabel("Prompts:"), 3, 0)
            form.addWidget(_muted("The app runs as administrator: PassMark and games ask nothing on the PC."
                                  if elevated else "PassMark and games make Windows ask for administrator rights on "
                                  "the PC. Restart as administrator once here, and they ask nothing."), 3, 1)
            form.addWidget(self.admin, 3, 2)
        layout.addWidget(box)

        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.setMinimumWidth(100)
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        for b in (self.copy_address, self.open_here, self.copy_code, self.change_reports, self.admin, close):
            b.setStyleSheet(BUTTON)
            b.setAutoDefault(False)
        self.switch.setAutoDefault(False)
        self.refresh()

    def first_address(self) -> str:
        if not self.control.running():
            return ""
        addresses = self.control.server.addresses()
        return addresses[0] if addresses else f"http://127.0.0.1:{self.control.server.port}/"

    def _copy(self, text: str):
        if text:
            QGuiApplication.clipboard().setText(text)
            self.message.setText(f"Copied: {text}")

    def _open_here(self):
        if self.control.running():
            QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{self.control.server.port}/"))

    def _change_reports(self):
        from app.ui import reports_folder
        window = self.control.window
        reports_folder.choose(window, always=True)
        self.refresh()

    def _restart_as_admin(self):
        if self.control.window.restart_as_admin():
            self.accept()

    def toggle(self) -> bool:
        on = self.control.running()
        self.switch.setText("Switching off..." if on else "Switching on...")   # seen at once: the click was taken
        self.switch.setEnabled(False)
        QApplication.processEvents()
        try:
            return self._toggle()
        finally:
            self.switch.setEnabled(True)
            self.switch.setStyleSheet(SWITCH_ON_FLASH if self.control.running() else SWITCH_OFF_FLASH)
            QTimer.singleShot(SWITCH_FLASH, self._unflash)

    def _unflash(self):
        try:
            self.switch.setStyleSheet(SWITCH_ON if self.control.running() else SWITCH_OFF)
        except RuntimeError:
            pass                                # the window was closed meanwhile

    def _toggle(self) -> bool:
        if self.control.running():
            self.control.stop()
            self.message.setText("Remote mode is off: the page no longer answers.")
        else:
            from app.ui import reports_folder
            reports_folder.choose(self.control.window)     # the first time: where all reports go
            ok, why = self.control.start(self.local_only.isChecked(), self.port.value())
            self.message.setText(why if not ok else "")
        self.refresh()
        return self.control.running()

    def refresh(self):
        from app.ui import reports_folder
        on = self.control.running()
        self.switch.setChecked(on)
        self.switch.setText("ON  ·  click to switch off" if on else "OFF  ·  click to switch on")
        self.switch.setStyleSheet(SWITCH_ON if on else SWITCH_OFF)
        self.pill.setText("ON" if on else "OFF")
        self.pill.setStyleSheet(PILL.format(GREEN if on else GREY))
        self.local_only.setEnabled(not on)
        self.port.setEnabled(not on)
        folder = reports_folder.folder(self.control.window)
        self.reports.setText(str(folder) if folder else "(no data folder)")
        self.card.setVisible(on)
        if not on:
            self.address.setText("")
            self.code.setText("")
            self.adjustSize()
            return
        addresses = self.control.server.addresses()
        self.address.setText("\n".join(a.rstrip("/") for a in addresses) if addresses else
                             f"http://127.0.0.1:{self.control.server.port} (no network address: this PC only)")
        self.code.setText(self.control.server.code)
        self.adjustSize()

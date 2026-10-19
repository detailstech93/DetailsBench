"""Remote start in the app (plan Phase D): the window that switches the server on and off, and the answers the page
gets (app/remote/server.py). Every answer is made on the Qt thread (MainThread), as if the owner had pressed the
buttons on the Tests screen; nothing on the PC waits for a click when the page starts a run:
- only tests that run by themselves are offered (no typed score, no license still to accept, no llama-benchy
  settings window, no Steam frame capture, and on Windows nothing that needs the UAC prompt: PassMark and the game
  benchmarks, whose PresentMon runs as administrator; nobody could answer the prompt from the page);
- the counts come from the page, game tests use the game preset chosen last on the PC, a stress test its last
  length;
- the start question is answered by the page's Start button (its text is kept in the status line);
- LibreHardwareMonitor is used only when it already runs (otherwise the run goes ahead without CPU sensors; its
  question would wait at the PC).
"""

import logging
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout)

from app.catalog import GROUPS, TestEntry
from app.remote import server as remote
from app.ui.tests_page import progress_percent

logger = logging.getLogger(__name__)

SETTINGS_KEY = "remote_start"          # settings.json: {"port": ..., "local_only": ...}; never "on"
GROUP_TITLES = dict(GROUPS)
SWITCH_ON = "QPushButton { background: #2e7d32; color: white; font-weight: bold; border-radius: 6px; }"
SWITCH_OFF = "QPushButton { background: #757575; color: white; font-weight: bold; border-radius: 6px; }"


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


def remote_entries(entries: List[TestEntry], platform: str = sys.platform) -> List[TestEntry]:
    """The tests the page may start: automatic ones that need nobody at the PC."""
    from app.ui.run_tests import game_of
    out = []
    for e in entries:
        ref = e.ref
        if e.source != "workload" or not e.can_run() or e.waits_for_typed_score():
            continue
        lic = getattr(ref, "license", None)
        if lic and not ref.license_ok():
            continue
        if hasattr(ref, "configure") and getattr(ref, "asks_settings", True):
            continue
        if platform == "win32" and (getattr(ref, "needs_admin", False) or game_of(e) is not None):
            continue                            # the UAC prompt would wait at the PC
        out.append(e)
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    return sorted(out, key=lambda e: (order.get(e.group, len(order)), e.name.lower()))


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
    return True, f"Started {len(entries)} test(s), {repetitions} measured run(s) each.{note}"


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
                  "minutes": e.expected_minutes, "verified": bool(e.verified)}
                 for e in remote_entries(w.tests_page.entries)]
        profile = w.selected_profile()
        return {"specs": specs, "tests": tests,
                "defaults": {"repetitions": max(1, int(profile.repetitions)), "warmup_runs": int(profile.warmup_runs),
                             "cooldown_seconds": int(profile.cooldown_seconds)},
                "note": "A failed run is stored as failed and run once more; uneven scores get up to a few extra "
                        "runs." + (" Game tests use the game settings chosen last on the PC." if sys.platform !=
                                   "win32" else " PassMark and game tests ask Windows for administrator rights, "
                                   "so they start from the PC.")}

    def state(self) -> dict:
        return self.main.run(self._state)

    def _state(self) -> dict:
        page = self.window.tests_page
        report = getattr(self.window, "last_report_path", None)
        out = {"running": False, "text": page.progress_label.text() if not page.progress_box.isHidden() else "",
               "percent": None, "report": bool(report and Path(report).is_file())}
        if page.busy:
            s = page.get_state()
            runs = f"Run {min(s.done + 1, s.total)} of {s.total}: " if s.running and s.total else ""
            out.update(running=s.running, text=runs + s.text, percent=progress_percent(s))
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

    def report(self) -> Optional[str]:
        path = self.main.run(lambda: getattr(self.window, "last_report_path", None))
        if not path or not Path(path).is_file():
            return None
        return Path(path).read_text(encoding="utf-8", errors="replace")


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
            return False, f"Remote start could not open port {port}: {e}"
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


class RemoteDialog(QDialog):
    def __init__(self, control: RemoteControl, parent=None):
        super().__init__(parent)
        self.control = control
        self.setWindowTitle("Remote start")
        layout = QVBoxLayout(self)
        intro = QLabel("Start tests from a phone or another computer on the same local network: open the address "
                       "below in its browser and type the code. Off until you switch it on here; off again when the "
                       "app closes. No internet is used. Windows may ask to allow the app through the firewall: "
                       "that answer is yours; the app never changes firewall rules.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        saved = control.settings()
        self.local_only = QCheckBox("This PC only (127.0.0.1, not reachable from other devices)")
        self.local_only.setChecked(saved["local_only"])
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(saved["port"])
        form.addRow("", self.local_only)
        form.addRow("Port:", self.port)
        layout.addLayout(form)
        self.switch = QPushButton()                  # the on/off switch of remote mode
        self.switch.setCheckable(True)
        self.switch.setMinimumHeight(44)
        self.switch.clicked.connect(self.toggle)
        layout.addWidget(self.switch)
        self.address = QLabel()
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.address.setWordWrap(True)
        layout.addWidget(self.address)
        self.code = QLabel()
        font = QFont(self.code.font())
        font.setPointSize(font.pointSize() + 12)
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        self.code.setFont(font)
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.code)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def toggle(self) -> bool:
        if self.control.running():
            self.control.stop()
            self.message.setText("Remote start is off.")
        else:
            ok, why = self.control.start(self.local_only.isChecked(), self.port.value())
            self.message.setText(why if not ok else "Remote start is on. The code is new every time it is "
                                                     "switched on.")
        self.refresh()
        return self.control.running()

    def refresh(self):
        on = self.control.running()
        self.switch.setChecked(on)
        self.switch.setText("Remote mode: ON  (click to switch off)" if on else
                            "Remote mode: OFF  (click to switch on)")
        self.switch.setStyleSheet(SWITCH_ON if on else SWITCH_OFF)
        self.local_only.setEnabled(not on)
        self.port.setEnabled(not on)
        if not on:
            self.address.setText("")
            self.code.setText("")
            return
        addresses = self.control.server.addresses()
        self.address.setText("Open in the browser:\n" + "\n".join(addresses) if addresses else
                             "No local network address found on this PC; only this PC can open "
                             f"http://127.0.0.1:{self.control.server.port}/")
        self.code.setText(f"Code: {self.control.server.code}")

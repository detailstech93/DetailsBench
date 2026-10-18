"""LibreHardwareMonitor check before a run with "Record telemetry" on (Windows).

When a run starts, the panel calls LhmPreflight.ensure(on_done). Nothing is asked when
LibreHardwareMonitor is already running (any copy). Otherwise ONE question: download it (if this
app has no copy yet) and start it. On yes: download (verified, see app/telemetry/lhm_provision.py),
turn its web server on with a random password, start it through UAC and wait until it answers.
On no, or when a step fails, the run goes ahead without CPU sensors and the status says why;
a "no" is remembered until the app is restarted, so it is not asked before every run.
The slow steps run off the GUI thread; on_done is always called exactly once.
"""

import logging
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from app.paths import get_data_dir
from app.telemetry import lhm_provision

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 30.0


class _Task(QThread):
    done = Signal(object)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:
            logger.exception("LibreHardwareMonitor step failed")
            self.done.emit(e)


def wait_until_answering(timeout: float = READY_TIMEOUT_SECONDS, sleep: Callable = time.sleep,
                         clock: Callable = time.monotonic) -> Optional[str]:
    """None when its web server gives CPU sensors, else the last reason (after timeout seconds)."""
    from app.telemetry.sources import LhmSource, _provisioned_lhm_settings
    source = LhmSource(settings=_provisioned_lhm_settings)
    end = clock() + timeout
    while True:
        ok, reason = source.available()
        if ok:
            return None
        if clock() >= end:
            return reason
        sleep(1.0)


class LhmPreflight(QObject):
    def __init__(self, report: Callable[[str], None], parent_widget=None, data_dir: Optional[Path] = None,
                 finder: Optional[Callable] = None, planner: Optional[Callable] = None,
                 downloader: Optional[Callable] = None, configurer: Optional[Callable] = None,
                 launcher: Optional[Callable] = None, running: Optional[Callable[[], bool]] = None,
                 confirm: Optional[Callable[[str, str], bool]] = None, waiter: Optional[Callable] = None,
                 arch: Optional[str] = None, platform_name: str = sys.platform):
        super().__init__(parent_widget)
        self.report = report
        self.parent_widget = parent_widget
        self.data_dir = data_dir or get_data_dir()
        self.platform_name = platform_name
        self._arch = arch
        self.finder = finder or (lambda: lhm_provision.find_local(self.data_dir))
        self.planner = planner or lhm_provision.plan_download
        self.downloader = downloader or (lambda asset: lhm_provision.download_lhm(asset, self.data_dir,
                                                                                   arch=self.arch))
        self.configurer = configurer or lhm_provision.configure_web_server
        self.launcher = launcher or lhm_provision.launch
        self.running = running or lhm_provision.is_running
        self.confirm = confirm or self._ask
        self.waiter = waiter or wait_until_answering
        self.declined = False
        self.continuing = False
        self._task = None
        self._on_done = None

    @property
    def arch(self) -> str:
        if self._arch is None:
            from app.frames.provision import native_arch
            self._arch = native_arch()
        return self._arch

    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self.parent_widget, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def busy(self) -> bool:
        return self._on_done is not None

    def _run(self, fn, on_done):
        self._task = _Task(fn)
        self._task.done.connect(on_done)
        self._task.start()

    def active(self) -> bool:
        """Busy, or the run is going on after the check (its next window may be open): a screen that follows
        the run must keep waiting."""
        return self.busy() or self.continuing

    def _front(self):
        """LibreHardwareMonitor and its UAC prompt took the front: bring this app back before the run's next
        window, so that window does not open unseen behind them (2026-10-18)."""
        if self.parent_widget is None:
            return
        try:
            window = self.parent_widget.window()
            if window.isMinimized():
                window.showNormal()
            window.raise_()
            window.activateWindow()
            QApplication.alert(window)
        except Exception:
            logger.exception("Could not bring the window to the front")

    def _finish(self, note: Optional[str] = None):
        if note:
            self.report(note)
        callback, self._on_done = self._on_done, None
        if callback is not None:
            self._front()
            self.continuing = True
            try:
                callback()
            finally:
                self.continuing = False

    def ensure(self, on_done: Callable[[], None]) -> bool:
        """Make sure LibreHardwareMonitor runs (asking first), then call on_done(). False if already busy."""
        if self.busy():
            return False
        self._on_done = on_done
        if self.platform_name != "win32" or self.declined:
            self._finish()
            return True
        try:
            if self.running():
                self._finish()
                return True
            exe = self.finder()
        except Exception as e:
            self._finish(f"Telemetry without CPU sensors: could not check LibreHardwareMonitor ({e}).")
            return True
        if exe:
            self._offer(str(exe), None)
            return True
        self.report("Telemetry: LibreHardwareMonitor is not on this PC; reading its official release list (GitHub)...")
        self._run(self.planner, self._on_planned)
        return True

    @Slot(object)
    def _on_planned(self, asset):
        if getattr(asset, "error", None) or not getattr(asset, "url", None):
            self._finish("Telemetry without CPU sensors: LibreHardwareMonitor cannot be downloaded "
                         f"({getattr(asset, 'error', asset)}).")
            return
        self._offer(None, asset)

    def _offer(self, exe: Optional[str], asset):
        why = ("Record telemetry is on. CPU temperature, package power and core clocks on Windows come from "
               "LibreHardwareMonitor, which is not running.\n\n")
        if asset is not None:
            steps = (f"Download {asset.name} (LibreHardwareMonitor {asset.version}, {asset.size / 1048576:.1f} MB, "
                     f"MPL-2.0)\nfrom {asset.url}\nSHA-256 {asset.sha256}\n"
                     f"Kept only if the size and the SHA-256 published by GitHub match (the zip is not code-signed); "
                     f"extracted under {lhm_provision.tools_dir(self.data_dir)}.\n\nThen start it:")
            title = "Download and start LibreHardwareMonitor?"
        else:
            steps = f"Start {exe}:"
            title = "Start LibreHardwareMonitor?"
        text = (why + steps + " its local web server (port 8085) is turned on with a new random password and it "
                "starts minimized to the tray. Its web server listens on every network interface (it cannot be "
                "limited to this PC) and can also change fan settings, so it never runs without a password. "
                "It needs administrator rights, so Windows shows the UAC prompt; it may then ask to install its "
                "sensor driver (PawnIO), which is its own prompt.\n\n"
                "No: the run goes ahead without CPU sensors, and this is not asked again until the app restarts.")
        if asset is not None and self.arch == "arm64":
            text += "\n\n" + lhm_provision.ARM_NOTE
        if not self.confirm(title, text):
            self.declined = True
            self._finish("Telemetry without CPU sensors: LibreHardwareMonitor declined.")
            return
        if exe is not None:
            self._start(exe, [])
            return
        self.report(f"Downloading {asset.name}...")
        self._run(lambda: self.downloader(asset), self._on_downloaded)

    @Slot(object)
    def _on_downloaded(self, found):
        if getattr(found, "error", None) or not getattr(found, "exe", None):
            self._finish("Telemetry without CPU sensors: LibreHardwareMonitor download failed, nothing kept "
                         f"({getattr(found, 'error', found)}).")
            return
        self._start(found.exe, list(found.notes or []))

    def _start(self, exe: str, notes: list):
        problem = self.configurer(exe) or self.launcher(exe)
        if problem:
            self._finish(f"Telemetry without CPU sensors: LibreHardwareMonitor not started ({problem}).")
            return
        self._notes = notes
        self.report("Waiting for LibreHardwareMonitor to answer (accept its driver prompt if it shows one)...")
        self._run(self.waiter, self._on_answering)

    @Slot(object)
    def _on_answering(self, problem):
        notes = "; ".join(getattr(self, "_notes", []) or [])
        if problem:
            self._finish(f"LibreHardwareMonitor started but gives no CPU sensors yet ({problem}); "
                         "the run goes ahead without them.")
            return
        self._finish("LibreHardwareMonitor running; telemetry includes CPU sensors" + (f" ({notes})." if notes else "."))

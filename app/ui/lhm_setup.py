"""The "Set up LibreHardwareMonitor..." button (Windows): download if needed, web server on, start through UAC.

Shown next to "Record telemetry" in both run panels. Every step that downloads, writes its settings
or starts it asks first; the slow steps run off the GUI thread. See app/telemetry/lhm_provision.py.
"""

import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import QMessageBox, QPushButton

from app.paths import get_data_dir
from app.telemetry import lhm_provision

logger = logging.getLogger(__name__)


class _Task(QThread):
    done = Signal(object)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:
            logger.exception("LibreHardwareMonitor setup step failed")
            self.done.emit(e)


class LhmSetupButton(QPushButton):
    def __init__(self, report: Callable[[str], None], data_dir: Optional[Path] = None, parent=None,
                 finder: Optional[Callable] = None, planner: Optional[Callable] = None,
                 downloader: Optional[Callable] = None, configurer: Optional[Callable] = None,
                 launcher: Optional[Callable] = None, running: Optional[Callable[[], bool]] = None,
                 confirm: Optional[Callable[[str, str], bool]] = None, arch: Optional[str] = None,
                 platform_name: str = sys.platform):
        super().__init__("Set up LibreHardwareMonitor...", parent)
        self.report = report
        self.data_dir = data_dir or get_data_dir()
        if arch is None:
            from app.frames.provision import native_arch
            arch = native_arch()
        self.arch = arch
        self.finder = finder or (lambda: lhm_provision.find_local(self.data_dir))
        self.planner = planner or lhm_provision.plan_download
        self.downloader = downloader or (lambda asset: lhm_provision.download_lhm(asset, self.data_dir, arch=self.arch))
        self.configurer = configurer or lhm_provision.configure_web_server
        self.launcher = launcher or lhm_provision.launch
        self.running = running or lhm_provision.is_running
        self.confirm = confirm or self._ask
        self._task = None
        self.setToolTip("CPU temperature, package power and core clocks for telemetry come from "
                        "LibreHardwareMonitor (Windows). Downloads it if needed, turns on its local web server "
                        "with a random password and starts it through the UAC prompt; every step asks first.")
        self.setVisible(platform_name == "win32")
        self.clicked.connect(self.start)

    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def _run(self, fn, on_done) -> bool:
        if self._task is not None and self._task.isRunning():
            return False
        self._task = _Task(fn)
        self._task.done.connect(on_done)
        self.setEnabled(False)
        self._task.start()
        return True

    def start(self):
        if self.running():
            self.report("LibreHardwareMonitor is already running. If telemetry reports it as not available, "
                        "close it (tray icon > Exit) and press Set up LibreHardwareMonitor again.")
            return
        exe = self.finder()
        if exe:
            self._ready(str(exe), [])
            return
        self.report("Reading the official LibreHardwareMonitor release list (GitHub)...")
        self._run(self.planner, self._on_planned)

    @Slot(object)
    def _on_planned(self, asset):
        self.setEnabled(True)
        if getattr(asset, "error", None) or not getattr(asset, "url", None):
            self.report(f"LibreHardwareMonitor download not possible: {getattr(asset, 'error', asset)}")
            return
        text = (f"Download {asset.name} (LibreHardwareMonitor {asset.version}, {asset.size / 1048576:.1f} MB, "
                f"MPL-2.0)\nfrom {asset.url}\nSHA-256 {asset.sha256}\n\n"
                f"Kept only if the size and the SHA-256 published by GitHub match (the zip is not code-signed). "
                f"Extracted under {lhm_provision.tools_dir(self.data_dir)}.")
        if self.arch == "arm64":
            text += "\n\n" + lhm_provision.ARM_NOTE
        if not self.confirm("Download LibreHardwareMonitor?", text):
            self.report("LibreHardwareMonitor download cancelled.")
            return
        self.report(f"Downloading {asset.name}...")
        self._run(lambda: self.downloader(asset), self._on_downloaded)

    @Slot(object)
    def _on_downloaded(self, found):
        self.setEnabled(True)
        if getattr(found, "error", None) or not getattr(found, "exe", None):
            self.report(f"LibreHardwareMonitor download failed, nothing kept: {getattr(found, 'error', found)}")
            return
        self._ready(found.exe, list(found.notes or []))

    def _ready(self, exe: str, notes: list):
        text = (f"Start {exe}?\n\n"
                "Before it starts, its local web server (port 8085) is turned on with a new random password, and "
                "it is set to start minimized to the tray. Its web server listens on every network interface (it "
                "cannot be limited to this PC) and can also change fan settings, so it is never left without a "
                "password.\n\n"
                "It needs administrator rights to read CPU sensors, so Windows shows the UAC prompt. "
                "LibreHardwareMonitor may then ask to install its sensor driver (PawnIO); that is its own prompt.")
        if not self.confirm("Start LibreHardwareMonitor?", text):
            self.report("LibreHardwareMonitor ready but not started" + (": " + "; ".join(notes) if notes else "."))
            return
        problem = self.configurer(exe)
        if problem:
            self.report(f"LibreHardwareMonitor not started: {problem}")
            return
        problem = self.launcher(exe)
        if problem:
            self.report(f"LibreHardwareMonitor not started: {problem}")
            return
        self.report("LibreHardwareMonitor started (minimized to the tray); telemetry reads it from the next run"
                    + (". " + "; ".join(notes) if notes else "."))

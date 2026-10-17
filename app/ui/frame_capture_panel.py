"""Run tab section for frame capture with PresentMon (Phase 6).

The user selects the official PresentMon once (kept in settings.json), starts the
game themselves, picks its process, and confirms. Each capture run shows a UAC
prompt; the app stays non-elevated. Runs go through AutomationWorker (QThread) into a new
session: score = average FPS, 1% low and the other numbers in result_metrics.

The process list is read in a worker thread and shown only; just the chosen
process name (and pid for the capture) is used, nothing else is stored.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout,
)

from app.automation.runner import PlanItem, RunSummary
from app.frames.presentmon import (
    CAPTURE_FLAGS_VERIFIED, SETTING_KEY, FrameCaptureWorkload, check_presentmon_path,
)
from app.paths import get_data_dir, load_settings, save_setting
from app.ui.automation_panel import STOP_SHORTCUT, AutomationWorker

logger = logging.getLogger(__name__)


def list_processes() -> List[Tuple[str, int]]:
    """(name, pid) of running processes, sorted by name; never this app or system pids."""
    import psutil
    own = os.getpid()
    out = []
    for proc in psutil.process_iter(["name", "pid"]):
        name, pid = proc.info.get("name"), proc.info.get("pid")
        if name and pid not in (0, 4, own):
            out.append((name, pid))
    return sorted(out, key=lambda item: (item[0].lower(), item[1]))


class ProcessListWorker(QThread):
    ready = Signal(list)

    def __init__(self, lister: Callable[[], List[Tuple[str, int]]]):
        super().__init__()
        self.lister = lister

    def run(self):
        try:
            self.ready.emit(self.lister())
        except Exception:
            logger.exception("Could not list processes")
            self.ready.emit([])


class FrameCapturePanel(QGroupBox):
    def __init__(self, db, get_profile: Callable, get_environment: Callable[[], Optional[dict]],
                 on_session_changed: Callable[[], None] = lambda: None, data_dir: Optional[Path] = None,
                 lister: Callable[[], List[Tuple[str, int]]] = list_processes,
                 output_root: Optional[Path] = None, parent=None):
        super().__init__("Frame capture (PresentMon)", parent)
        self.db = db
        self.get_profile = get_profile
        self.get_environment = get_environment
        self.on_session_changed = on_session_changed
        self.data_dir = data_dir or get_data_dir()
        self.lister = lister
        db_path = getattr(db, "db_path", None)
        if output_root is None and isinstance(db_path, str):
            output_root = Path(db_path).resolve().parent / "automation_runs"
        self.output_root = output_root
        self.workload_factory = FrameCaptureWorkload     # tests swap in a fake elevated runner
        self.worker: Optional[AutomationWorker] = None
        self._list_worker: Optional[ProcessListWorker] = None
        self.last_session_id: Optional[int] = None
        self.last_summary = None

        layout = QVBoxLayout(self)
        row1 = QHBoxLayout()
        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        self.select_btn = QPushButton("Select PresentMon...")
        self.select_btn.clicked.connect(self.select_presentmon)
        row1.addWidget(self.path_label, 1)
        row1.addWidget(self.select_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Game process:"))
        self.process_combo = QComboBox()
        self.process_combo.setMinimumWidth(260)
        row2.addWidget(self.process_combo, 1)
        self.refresh_btn = QPushButton("Refresh list")
        self.refresh_btn.clicked.connect(self.refresh_processes)
        row2.addWidget(self.refresh_btn)
        row2.addWidget(QLabel("Seconds:"))
        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(10, 600)
        self.seconds_spin.setValue(60)
        row2.addWidget(self.seconds_spin)
        self.capture_btn = QPushButton("Capture frames...")
        self.capture_btn.clicked.connect(self.start_capture)
        row2.addWidget(self.capture_btn)
        self.stop_btn = QPushButton(f"Emergency stop ({STOP_SHORTCUT})")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("QPushButton:enabled { color: white; background: #b91c1c; }")
        self.stop_btn.clicked.connect(self.emergency_stop)
        row2.addWidget(self.stop_btn)
        layout.addLayout(row2)

        self.status_label = QLabel("Start the game first, then refresh the list and pick its process.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.show_path()

    # ---- PresentMon path ------------------------------------------------------
    def presentmon_path(self) -> Optional[str]:
        settings, _ = load_settings(self.data_dir)
        value = settings.get(SETTING_KEY)
        return value if isinstance(value, str) else None

    def show_path(self):
        path = self.presentmon_path()
        problem = check_presentmon_path(path)
        flags = "" if CAPTURE_FLAGS_VERIFIED else "  (capture flags not yet verified on this machine)"
        self.path_label.setText(f"PresentMon: {path}{flags}" if not problem else f"PresentMon: {problem}")

    def ask_presentmon_file(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Select the official PresentMon executable", "",
                                              "Programs (*.exe)")
        return path

    def select_presentmon(self):
        path = self.ask_presentmon_file()
        if not path:
            return
        problem = check_presentmon_path(path)
        if problem:
            self.status_label.setText(f"Not saved: {problem}")
            return
        try:
            save_setting(self.data_dir, SETTING_KEY, str(Path(path)))
        except (OSError, ValueError) as e:
            self.status_label.setText(f"Not saved: {e}")
            return
        self.show_path()
        self.status_label.setText("PresentMon path saved.")

    # ---- processes -------------------------------------------------------------
    def refresh_processes(self):
        if self._list_worker is not None and self._list_worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self._list_worker = ProcessListWorker(self.lister)
        self._list_worker.ready.connect(self._on_processes)
        self._list_worker.start()

    @Slot(list)
    def _on_processes(self, processes):
        self.refresh_btn.setEnabled(True)
        self.process_combo.clear()
        for name, pid in processes:
            self.process_combo.addItem(f"{name} (pid {pid})", (name, pid))
        self.status_label.setText(f"{len(processes)} processes listed (not stored).")

    # ---- capture ------------------------------------------------------------------
    def confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "Capture frames?", text,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _create_session(self, workload, profile) -> int:
        from app.manifest import build_manifest, git_commit
        name = f"Frame capture {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        sid = self.db.create_session(name, f"PresentMon capture of {workload.process_name}", is_manual=False)
        settings = dict(profile.settings(), capture_seconds=workload.seconds)
        manifest = build_manifest(
            {"id": sid, "name": name, "is_test": False, "is_manual": False},
            run_settings=settings, environment=self.get_environment(),
            benchmarks_used=[{"name": workload.id, "display_name": workload.display_name,
                              "version": workload.version(), "executable_path": workload.presentmon_path,
                              "detection_method": "selected by the user"}],
            commit=git_commit())
        try:
            self.db.save_session_manifest(sid, manifest)
        except Exception:
            logger.exception("Could not save session manifest")
        return sid

    def start_capture(self) -> bool:
        if self.is_running():
            return False
        if self.db is None or self.output_root is None:
            self.status_label.setText("No database available; nothing was captured.")
            return False
        path = self.presentmon_path()
        problem = check_presentmon_path(path)
        if problem:
            self.status_label.setText(f"{problem}. Use 'Select PresentMon...' first.")
            return False
        target = self.process_combo.currentData()
        if not target:
            self.status_label.setText("Pick the game process first (Refresh list).")
            return False
        name, pid = target
        workload = self.workload_factory(path, name, seconds=self.seconds_spin.value(), process_id=pid)
        profile = self.get_profile()
        runs = profile.warmup_runs + profile.repetitions
        text = (f"Capture {workload.seconds} s of frames from {name} (pid {pid}): "
                f"{profile.warmup_runs} warmup + {profile.repetitions} measured captures "
                f"(profile {profile.name!r}), cooldown {profile.cooldown_seconds} s.\n\n"
                f"Command (runs elevated):\n{workload.describe_command()}\n\n"
                f"Windows will show a UAC prompt for EACH of the {runs} captures; declining one cancels the rest. "
                f"Only PresentMon is started and stopped; the game is never touched.")
        if not CAPTURE_FLAGS_VERIFIED:
            text += "\n\nWARNING: the capture flags are not yet verified with this PresentMon (--help)."
        if not self.confirm(text):
            self.status_label.setText("Cancelled; nothing was captured.")
            return False
        sid = self._create_session(workload, profile)
        self.last_session_id, self.last_summary = sid, None
        plan = [PlanItem(workload, repetitions=profile.repetitions, warmup_runs=profile.warmup_runs)]
        self.worker = AutomationWorker(self.db, sid, plan, self.output_root, profile.cooldown_seconds, False)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_plan.connect(self._on_finished)
        self._set_running(True)
        self.status_label.setText(f"Capturing in session #{sid}...")
        self.worker.start()
        return True

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def emergency_stop(self):
        if self.is_running():
            self.worker.stop()
            self.status_label.setText("Stopping: PresentMon is being terminated...")

    def shutdown(self, wait_ms: int = 15000):
        if self.is_running():
            self.worker.stop()
            self.worker.wait(wait_ms)
        if self._list_worker is not None and self._list_worker.isRunning():
            self._list_worker.wait(5000)

    def _set_running(self, running: bool):
        for w in (self.capture_btn, self.select_btn, self.refresh_btn, self.process_combo, self.seconds_spin):
            w.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    @Slot(str)
    def _on_progress(self, message: str):
        self.status_label.setText(message)

    @Slot(object)
    def _on_finished(self, summary):
        self._set_running(False)
        self.last_summary = summary
        if isinstance(summary, RunSummary):
            parts = []
            for rec in summary.records:
                r = rec.result
                kind = "warmup" if rec.is_warmup else f"run {rec.run_index}"
                low = (r.metrics or {}).get("low_1pct_fps")
                if r.status == "completed":
                    parts.append(f"{kind}: {r.score:.1f} fps" + (f", 1% low {low[0]:.1f}" if low else ""))
                else:
                    parts.append(f"{kind}: {r.status} ({r.error})")
            text = f"Session #{self.last_session_id}: " + ("; ".join(parts) or "no captures")
            if summary.stopped:
                text += f". Stopped; {summary.not_started} capture(s) not started."
        else:
            text = f"Session #{self.last_session_id}: {summary}"
        self.status_label.setText(text)
        self.on_session_changed()

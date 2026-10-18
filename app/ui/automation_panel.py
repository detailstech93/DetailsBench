"""Run tab section for automated command-line tools (Phase 5).

The plan runs in an AutomationWorker (QThread), never on the GUI thread. Every
automated run starts a NEW session (is_manual=False; is_test when every selected
tool is a self-test). Emergency stop: the button or Ctrl+Shift+X while this
window has focus. There is no system-wide hotkey. A stop or timeout terminates only
the process tree the app itself started.

llama-benchy (AI, against LM Studio) is listed with the tool specs; its model is picked from LM Studio's
models before each plan, and the confirmation warns not to use LM Studio for anything else meanwhile.

Screenshots on failure are off by default (they capture the whole desktop) and
are kept only in the run folder. Qt may grab the screen only on the GUI thread,
so the worker asks the panel for one and waits at most 10 s for it.
"""

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.ai.llama_benchy import load_workloads
from app.automation.plan_text import plan_overview
from app.automation.runner import AutomationRunner, PlanItem, RunSummary
from app.telemetry import sources as telemetry_sources

logger = logging.getLogger(__name__)

STOP_SHORTCUT = "Ctrl+Shift+X"


TELEMETRY_TIP = ("Every second during each run: CPU load and memory (psutil), NVIDIA GPU temperature, clocks, "
                 "power and load (nvidia-smi), and CPU temperature, power and clocks when LibreHardwareMonitor "
                 "runs with its Remote Web Server on. Sources that are not available are listed, never "
                 "recorded as 0. Saved as telemetry.csv in each run folder.")


class AutomationWorker(QThread):
    progress = Signal(str)
    finished_plan = Signal(object)              # RunSummary, or an error str
    screenshot_requested = Signal(str, object)  # path, threading.Event

    def __init__(self, db, session_id: int, plan: List[PlanItem], output_root: Path,
                 cooldown_seconds: float, take_screenshots: bool, telemetry: Optional[Callable] = None):
        super().__init__()
        self.stop_event = threading.Event()
        self.runner = AutomationRunner(
            db, session_id, plan, output_root, cooldown_seconds=cooldown_seconds,
            stop_event=self.stop_event, on_progress=self.progress.emit,
            screenshot=self._screenshot if take_screenshots else None, telemetry=telemetry)

    def stop(self):
        self.stop_event.set()

    def _screenshot(self, path: Path) -> bool:
        done = threading.Event()
        self.screenshot_requested.emit(str(path), done)
        return done.wait(10) and Path(path).is_file()

    def run(self):
        try:
            self.finished_plan.emit(self.runner.run())
        except Exception as e:   # e.g. database error; the runs already stored stay stored
            logger.exception("Automated plan failed")
            self.finished_plan.emit(f"plan aborted: {e}")


def _spec_value(wl, name: str):
    """timeout_seconds / verified from the tool spec, or from the workload itself (llama-benchy)."""
    spec = getattr(wl, "spec", None)
    return getattr(spec, name) if spec is not None else getattr(wl, name)


def language_models(parent, read_models=None) -> Optional[list]:
    """LM Studio's language models (loaded ones first); None (with a message) when there are none to measure.
    Reads LM Studio's local server once (5 s limit)."""
    from app.ai import lmstudio
    try:
        models = (read_models or lmstudio.read_models)()
    except (OSError, ValueError) as e:
        QMessageBox.warning(parent, "llama-benchy", f"LM Studio's server is not reachable: {e}\n"
                            "Start the server in LM Studio (Developer tab) and try again.")
        return None
    models = [m for m in models if m.get("type") in (None, "llm", "vlm")]
    models.sort(key=lambda m: m.get("state") != "loaded")
    if not models:
        QMessageBox.warning(parent, "llama-benchy", "LM Studio lists no language models.")
        return None
    return models


def choose_llama_settings(parent, current=None, read_models=None, dialog=None) -> Optional[dict]:
    """Model, pp, tg, depths, concurrency and prefix caching for llama-benchy; None when cancelled."""
    from app.ui.llama_benchy_dialog import LlamaBenchyDialog
    models = language_models(parent, read_models)
    if models is None:
        return None
    box = (dialog or LlamaBenchyDialog)(models, current, parent)
    return box.values() if box.exec() == QDialog.Accepted else None


def find_reports(summary) -> list:
    """(title, path) of the reports written by the runs of a finished plan (llama-benchy)."""
    from app.ai.llama_benchy import REPORT_FILE
    out = []
    for rec in getattr(summary, "records", []) or []:
        path = Path(rec.run_dir) / REPORT_FILE if rec.run_dir else None
        if path and path.is_file():
            kind = "warm-up" if rec.is_warmup else f"run {rec.run_index}"
            out.append((f"{rec.workload_id} ({kind})", path))
    return out


class AutomationPanel(QGroupBox):
    """`get_profile()` gives the selected RunProfile; `get_environment()` a dict or None;
    `on_session_changed()` is called after a plan ends (history refresh)."""

    def __init__(self, db, get_profile: Callable, get_environment: Callable[[], Optional[dict]],
                 on_session_changed: Callable[[], None] = lambda: None,
                 loader: Callable[[], Tuple[list, List[str]]] = load_workloads,
                 output_root: Optional[Path] = None, parent=None):
        super().__init__("Automated runs (command-line tools)", parent)
        self.db = db
        self.get_profile = get_profile
        self.get_environment = get_environment
        self.on_session_changed = on_session_changed
        self.loader = loader
        db_path = getattr(db, "db_path", None)
        if output_root is None and isinstance(db_path, str):
            output_root = Path(db_path).resolve().parent / "automation_runs"
        self.output_root = output_root
        self.workloads: list = []
        self.worker: Optional[AutomationWorker] = None
        self.last_session_id: Optional[int] = None
        self.last_summary = None
        self.reports: list = []

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Run", "Tool", "Availability", "Command"])
        header = self.table.horizontalHeader()
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setMaximumHeight(150)
        layout.addWidget(self.table)
        self.errors_label = QLabel("")
        self.errors_label.setWordWrap(True)
        self.errors_label.setStyleSheet("color: #b45309;")
        layout.addWidget(self.errors_label)

        row = QHBoxLayout()
        self.reload_btn = QPushButton("Reload tool specs")
        self.reload_btn.clicked.connect(self.reload)
        self.screenshot_box = QCheckBox("Screenshot on failure (whole desktop, kept in the run folder)")
        self.telemetry_box = QCheckBox("Record telemetry")
        self.telemetry_box.setChecked(True)
        self.telemetry_box.setToolTip(TELEMETRY_TIP)
        self.run_btn = QPushButton("Run automated...")
        self.run_btn.clicked.connect(lambda: self.start_plan())
        self.stop_btn = QPushButton(f"Emergency stop ({STOP_SHORTCUT})")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("QPushButton:enabled { color: white; background: #b91c1c; }")
        self.stop_btn.clicked.connect(self.emergency_stop)
        row.addWidget(self.reload_btn)
        row.addWidget(self.screenshot_box, 1)
        row.addWidget(self.telemetry_box)
        from app.ui.lhm_setup import LhmPreflight
        self.lhm = LhmPreflight(lambda text: self.status_label.setText(text), self)
        row.addWidget(self.run_btn)
        self.report_btn = QPushButton("Show report")
        self.report_btn.setToolTip("Results table of the last plan's llama-benchy run (llama_benchy_report.md "
                                   "in the run folder).")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(lambda: self.show_report())
        row.addWidget(self.report_btn)
        row.addWidget(self.stop_btn)
        layout.addLayout(row)
        self.status_label = QLabel("Idle. A system-wide stop hotkey is not available; "
                                   f"{STOP_SHORTCUT} works while this window has focus.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # the Ctrl+Shift+X shortcut lives in MainWindow (one shortcut stops every panel)
        self.reload()

    # ---- tool list -------------------------------------------------------
    def reload(self):
        try:
            self.workloads, errors = self.loader()
        except Exception as e:
            logger.exception("Could not load tool specs")
            self.workloads, errors = [], [f"could not load tool specs: {e}"]
        self.table.setRowCount(len(self.workloads))
        for r, wl in enumerate(self.workloads):
            available, detail = wl.availability()
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled if available else Qt.NoItemFlags)
            check.setCheckState(Qt.Unchecked)
            self.table.setItem(r, 0, check)
            name = wl.display_name + (" [test]" if wl.is_test else "")
            self.table.setItem(r, 1, QTableWidgetItem(name))
            self.table.setItem(r, 2, QTableWidgetItem(detail))
            self.table.setItem(r, 3, QTableWidgetItem(wl.describe_command() or "-"))
        self.errors_label.setText("Spec errors (files skipped):\n" + "\n".join(errors) if errors else "")

    def set_checked(self, workload_id: str, checked: bool = True):
        for r, wl in enumerate(self.workloads):
            if wl.id == workload_id:
                self.table.item(r, 0).setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def selected_workloads(self) -> list:
        # an unavailable tool is never selected, even if its box was set in code
        return [wl for r, wl in enumerate(self.workloads)
                if self.table.item(r, 0).flags() & Qt.ItemIsUserCheckable
                and self.table.item(r, 0).checkState() == Qt.Checked]

    # ---- plan --------------------------------------------------------------
    def build_plan(self, workloads, profile) -> List[PlanItem]:
        return [PlanItem(wl, repetitions=profile.repetitions, warmup_runs=profile.warmup_runs,
                         timeout_seconds=_spec_value(wl, "timeout_seconds")) for wl in workloads]

    def overview(self, plan: List[PlanItem], cooldown: int) -> dict:
        return plan_overview([{"display_name": p.workload.display_name,
                               "command": p.workload.describe_command(),
                               "repetitions": p.repetitions, "warmup_runs": p.warmup_runs,
                               "timeout_seconds": int(p.timeout_seconds),
                               "verified": _spec_value(p.workload, "verified"), "is_test": p.workload.is_test}
                              for p in plan], cooldown)

    def choose_settings(self, workload) -> Optional[dict]:
        current = {k: getattr(workload, k) for k in ("model", "pp", "tg", "depths", "concurrency", "prefix_caching")}
        return choose_llama_settings(self, current)

    def show_report(self, index: int = -1):
        from app.ui.llama_benchy_dialog import ReportDialog
        if self.reports:
            title, path = self.reports[index]
            ReportDialog(f"Report: {title}", path.read_text(encoding="utf-8"), self).exec()

    def confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "Run automated benchmarks?", text,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _create_session(self, workloads, profile) -> int:
        from app.manifest import build_manifest, git_commit
        name = f"Automated {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        is_test = all(wl.is_test for wl in workloads)
        sid = self.db.create_session(name, "Automated command-line runs", is_test=is_test, is_manual=False)
        manifest = build_manifest(
            {"id": sid, "name": name, "is_test": is_test, "is_manual": False},
            run_settings=profile.settings(), environment=self.get_environment(),
            benchmarks_used=[{"name": wl.id, "display_name": wl.display_name, "version": wl.version(),
                              "executable_path": wl.executable()[0],
                              "detection_method": "tool spec " + wl.executable()[1],
                              **({"model": wl.model} if getattr(wl, "model", None) else {})}
                             for wl in workloads],
            commit=git_commit())
        try:
            self.db.save_session_manifest(sid, manifest)
        except Exception:
            logger.exception("Could not save session manifest")
        return sid

    def start_plan(self, tools_ready: bool = False) -> bool:
        if self.is_running() or self.lhm.busy():
            return False
        if self.db is None or self.output_root is None:
            self.status_label.setText("No database available; nothing was run.")
            return False
        workloads = self.selected_workloads()
        if not workloads:
            self.status_label.setText("Tick at least one available tool first.")
            return False
        if self.telemetry_box.isChecked() and not tools_ready:
            # LibreHardwareMonitor for CPU sensors: asks to download/start it only when it is not running.
            started = []
            self.lhm.ensure(lambda: started.append(self.start_plan(tools_ready=True)))
            return bool(started and started[0])      # empty: it asks or downloads first, the run starts later
        for wl in workloads:
            if hasattr(wl, "configure"):      # llama-benchy: model and settings are chosen for every plan
                settings = self.choose_settings(wl)
                if not settings:
                    self.status_label.setText("No llama-benchy settings chosen; nothing was run.")
                    return False
                wl.configure(settings)
        profile = self.get_profile()
        plan = self.build_plan(workloads, profile)
        info = self.overview(plan, int(profile.cooldown_seconds))
        text = (info["text"] + f"\n\nRun settings from profile {profile.name!r}. A new session is created. "
                f"Emergency stop: the red button or {STOP_SHORTCUT}.")
        warnings = [wl.warning for wl in workloads if getattr(wl, "warning", None)]
        if warnings:
            text += "\n\nWARNING: " + "\n".join(warnings)
        if not self.confirm(text):
            self.status_label.setText("Cancelled; nothing was run.")
            return False
        sid = self._create_session(workloads, profile)
        self.last_session_id, self.last_summary = sid, None
        self.worker = AutomationWorker(self.db, sid, plan, self.output_root, profile.cooldown_seconds,
                                       self.screenshot_box.isChecked(),
                                       telemetry_sources.new_sampler if self.telemetry_box.isChecked() else None)
        self.worker.progress.connect(self._on_progress)
        self.worker.screenshot_requested.connect(self._on_screenshot_requested)
        self.worker.finished_plan.connect(self._on_finished)
        self._set_running(True)
        self.status_label.setText(f"Running in session #{sid} ({info['total_runs']} runs)...")
        self.worker.start()
        return True

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def emergency_stop(self):
        if self.is_running():
            self.worker.stop()
            self.status_label.setText("Stopping: the current run is being terminated...")

    def shutdown(self, wait_ms: int = 15000):
        """Called from closeEvent: stop and wait (the child tree is terminated within ~5 s)."""
        if self.is_running():
            self.worker.stop()
            self.worker.wait(wait_ms)

    def _set_running(self, running: bool):
        self.run_btn.setEnabled(not running)
        self.reload_btn.setEnabled(not running)
        self.table.setEnabled(not running)
        self.telemetry_box.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    @Slot(str)
    def _on_progress(self, message: str):
        self.status_label.setText(message)

    @Slot(str, object)
    def _on_screenshot_requested(self, path: str, done: threading.Event):
        try:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                screen.grabWindow(0).save(path, "PNG")
        except Exception:
            logger.exception("Screenshot failed")
        finally:
            done.set()

    @Slot(object)
    def _on_finished(self, summary):
        self._set_running(False)
        self.last_summary = summary
        self.reports = find_reports(summary)
        self.report_btn.setEnabled(bool(self.reports))
        if isinstance(summary, RunSummary):
            counts = {}
            for rec in summary.records:
                counts[rec.result.status] = counts.get(rec.result.status, 0) + 1
            parts = ", ".join(f"{n} {status}" for status, n in sorted(counts.items())) or "no runs"
            text = f"Session #{self.last_session_id} done: {parts}."
            if summary.stopped:
                text += f" Stopped; {summary.not_started} run(s) not started."
        else:
            text = f"Session #{self.last_session_id}: {summary}"
        self.status_label.setText(text)
        self.on_session_changed()

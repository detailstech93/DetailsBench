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

from app.all_workloads import load_workloads
from app.automation.plan_text import plan_overview
from app.automation import journal as plan_journal
from app.automation.runner import AutomationRunner, PlanItem, RunSummary, averages_text, plan_averages
from app.telemetry import sources as telemetry_sources

logger = logging.getLogger(__name__)

STOP_SHORTCUT = "Ctrl+Shift+X"


TELEMETRY_TIP = ("Every second during each run: CPU load and memory (psutil), NVIDIA GPU temperature, clocks, "
                 "power and load (nvidia-smi), and CPU temperature, power and clocks when LibreHardwareMonitor "
                 "runs with its Remote Web Server on. Sources that are not available are listed, never "
                 "recorded as 0. Saved as telemetry.csv in each run folder.")


class AutomationWorker(QThread):
    progress = Signal(str)
    step = Signal(int, int, str)                # runs finished, runs planned, what is happening
    finished_plan = Signal(object)              # RunSummary, or an error str
    screenshot_requested = Signal(str, object)  # path, threading.Event

    def __init__(self, db, session_id: int, plan: List[PlanItem], output_root: Path,
                 cooldown_seconds: float, take_screenshots: bool, telemetry: Optional[Callable] = None,
                 **resume):
        super().__init__()
        self.stop_event = threading.Event()
        self.runner = AutomationRunner(
            db, session_id, plan, output_root, cooldown_seconds=cooldown_seconds,
            stop_event=self.stop_event, on_progress=self.progress.emit,
            screenshot=self._screenshot if take_screenshots else None, telemetry=telemetry,
            on_step=self.step.emit, **resume)

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
    """(title, path) of the reports written by the runs of a finished plan (llama-benchy, storage tools)."""
    from app.ai.llama_benchy import REPORT_FILE as AI_REPORT
    from app.storage.disk_tools import REPORT_FILE as STORAGE_REPORT
    out = []
    for rec in getattr(summary, "records", []) or []:
        for name in (AI_REPORT, STORAGE_REPORT):
            path = Path(rec.run_dir) / name if rec.run_dir else None
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
        self.on_plan_finished: Optional[Callable[[int, str], None]] = None    # set by the Tests screen
        self.asking = False          # the "start these runs?" question is open (after the sensors were prepared)
        self.step: Tuple[int, int, str] = (0, 0, "")                          # progress of the running plan

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
        self.report_btn.setToolTip("Results tables of the last plan's runs that write a report (llama-benchy, "
                                   "DiskSpd, fio; the .md file in each run folder).")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(lambda: self.show_report())
        row.addWidget(self.report_btn)
        self.resume_btn = QPushButton("Resume interrupted plan...")
        self.resume_btn.setToolTip("A plan was still running when the app ended (crash, power loss or forced "
                                   "close). Resume it in the same session, or discard it.")
        self.resume_btn.setVisible(False)
        self.resume_btn.clicked.connect(lambda: self.resume_plan())
        row.addWidget(self.resume_btn)
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
        self.update_resume()

    # ---- unattended runs: resume after a crash -------------------------------
    def journal_path(self) -> Optional[Path]:
        return Path(self.output_root) / plan_journal.JOURNAL_NAME if self.output_root is not None else None

    def unfinished_plan(self) -> Optional[dict]:
        path = self.journal_path()
        return plan_journal.read_journal(path) if path is not None and path.is_file() else None

    def update_resume(self):
        data = None if self.is_running() else self.unfinished_plan()
        self.resume_btn.setVisible(data is not None)
        if data is not None:
            left = plan_journal.remaining_runs(data)
            self.resume_btn.setText(f"Resume interrupted plan ({left} run(s) left)...")

    def ask_resume(self, text: str) -> str:
        """'resume', 'later' or 'discard'."""
        box = QMessageBox(QMessageBox.Question, "Resume interrupted plan?", text, parent=self)
        resume = box.addButton("Resume", QMessageBox.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.DestructiveRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        return {resume: "resume", discard: "discard"}.get(box.clickedButton(), "later")

    def resume_plan(self) -> bool:
        if self.is_running() or self.db is None:
            return False
        data = self.unfinished_plan()
        if data is None:
            self.update_resume()
            return False
        plan, done, interrupted, missing = plan_journal.plan_from_journal(data, self.workloads)
        lines = [f"A plan of session #{data['session_id']} (started {data.get('created', '?')}) did not finish.",
                 f"{plan_journal.remaining_runs(data)} run(s) left."]
        if interrupted:
            lines.append(f"The run in progress ({interrupted[0]}, {'warm-up' if interrupted[2] else 'run'} "
                         f"{interrupted[1]}) is stored as failed; it has no result.")
        if missing:
            lines.append("Not available now, skipped: " + ", ".join(missing))
        session_gone = self.db.get_session(int(data["session_id"])) is None
        if session_gone:
            lines.append("Its session is no longer in the database, so it cannot be resumed; Discard removes it.")
        warnings = [p.workload.warning for p in plan if getattr(p.workload, "warning", None)]
        if warnings:
            lines.append("WARNING: " + "\n".join(warnings))
        choice = self.ask_resume("\n".join(lines))
        if choice == "discard":
            plan_journal.PlanJournal(self.journal_path()).finish()
            self.status_label.setText("Interrupted plan discarded; the runs already stored stay stored.")
            self.update_resume()
            return False
        if choice != "resume":
            return False
        if session_gone:
            self.status_label.setText("The interrupted plan cannot be resumed: its session was deleted.")
            return False
        sid = int(data["session_id"])
        self.last_session_id, self.last_summary = sid, None
        self._start_worker(sid, plan, float(data.get("cooldown_seconds") or 0),
                           done=done, interrupted=interrupted, stamp=data["stamp"])
        self.status_label.setText(f"Resuming session #{sid}...")
        return True

    def _start_worker(self, sid: int, plan, cooldown: float, **resume):
        self.plan_workloads = [item.workload for item in plan]
        if any(getattr(wl, "needs_admin", False) for wl in self.plan_workloads):
            # one UAC prompt now for the whole run, so nobody has to stay at the PC (declined: each such test
            # asks for itself)
            from app.frames import admin_helper
            self.status_label.setText("Asking Windows once for administrator permission for this run...")
            why = admin_helper.begin()
            if why:
                self.status_label.setText(f"No administrator helper ({why}); each test that needs it will ask.")
        self.worker = AutomationWorker(self.db, sid, plan, self.output_root, cooldown,
                                       self.screenshot_box.isChecked(),
                                       telemetry_sources.new_sampler if self.telemetry_box.isChecked() else None,
                                       journal=plan_journal.PlanJournal(self.journal_path()), **resume)
        self.worker.progress.connect(self._on_progress)
        runner = self.worker.runner
        self.step = (0, sum((s[0].workload.id, s[1], s[2]) not in runner.done for s in runner._schedule()),
                     "Starting...")
        self.worker.step.connect(self._on_step)
        self.worker.screenshot_requested.connect(self._on_screenshot_requested)
        self.worker.finished_plan.connect(self._on_finished)
        self._set_running(True)
        self.worker.start()

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
        # a workload with fixed_counts (warmups, runs) ignores the profile: a stress test runs once for its minutes
        plan = []
        for wl in workloads:
            warmups, runs = getattr(wl, "fixed_counts", None) or (profile.warmup_runs, profile.repetitions)
            plan.append(PlanItem(wl, repetitions=runs, warmup_runs=warmups,
                                 timeout_seconds=_spec_value(wl, "timeout_seconds")))
        return plan

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

    def report_text(self) -> str:
        """Every report of the last plan, each under its run's title, one after another."""
        parts = []
        for title, path in self.reports:
            try:
                text = path.read_text(encoding="utf-8")
                parts.append(f"## {title}\n\n" + "\n".join("###" + line[1:] if line.startswith("# ") else line
                                                         for line in text.splitlines()) + "\n")
            except OSError as e:
                parts.append(f"## {title}\n\nReport not readable: {e}\n")
        return "\n".join(parts)

    def show_report(self):
        from app.ui.llama_benchy_dialog import ReportDialog
        if self.reports:
            ReportDialog(f"Reports: session #{self.last_session_id}", self.report_text(), self).exec()

    def confirm(self, text: str) -> bool:
        from app.ui.long_question import ask_yes_no
        return ask_yes_no(self, "Run automated benchmarks?", text)

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

    def start_plan(self, tools_ready: bool = False, workloads: Optional[list] = None, profile=None) -> bool:
        """Run the ticked tools, or `workloads` with `profile` (the Tests screen passes both)."""
        if self.is_running() or self.lhm.busy():
            return False
        if self.db is None or self.output_root is None:
            self.status_label.setText("No database available; nothing was run.")
            return False
        if workloads is None:
            workloads = self.selected_workloads()
        else:
            workloads = [wl for wl in workloads if wl.availability()[0]]   # never run a tool that is gone
        if not workloads:
            self.status_label.setText("Tick at least one available tool first.")
            return False
        if self.telemetry_box.isChecked() and not tools_ready:
            # LibreHardwareMonitor for CPU sensors: asks to download/start it only when it is not running.
            started = []
            self.lhm.ensure(lambda: started.append(self.start_plan(True, workloads, profile)))
            return bool(started and started[0])      # empty: it asks or downloads first, the run starts later
        chosen = {}
        for wl in workloads:
            if hasattr(wl, "configure"):      # llama-benchy: model and settings are chosen for every plan
                settings = self.choose_settings(wl)
                if not settings:
                    self.status_label.setText("No llama-benchy settings chosen; nothing was run.")
                    return False
                wl.configure(settings)
                chosen[wl.id] = settings
        profile = profile or self.get_profile()
        plan = self.build_plan(workloads, profile)
        for item in plan:
            item.settings = chosen.get(item.workload.id)      # kept in the journal to resume after a crash
        info = self.overview(plan, int(profile.cooldown_seconds))
        text = (info["text"] + f"\n\nRun settings from profile {profile.name!r}. A new session is created. "
                f"Emergency stop: the red button or {STOP_SHORTCUT}.")
        warnings = [wl.warning for wl in workloads if getattr(wl, "warning", None)]
        if warnings:
            text += "\n\nWARNING: " + "\n".join(warnings)
        self.asking = True
        try:
            confirmed = self.confirm(text)
        finally:
            self.asking = False
        if not confirmed:
            self.status_label.setText("Cancelled; nothing was run.")
            return False
        sid = self._create_session(workloads, profile)
        self.last_session_id, self.last_summary = sid, None
        self._start_worker(sid, plan, profile.cooldown_seconds)
        self.status_label.setText(f"Running in session #{sid} ({info['total_runs']} runs)...")
        return True

    def run_progress(self):
        """(fraction done, seconds left) of the run going on now, for a workload that knows it (a stress test
        runs for set minutes), else None."""
        for wl in getattr(self, "plan_workloads", []):
            progress = getattr(wl, "progress", None)
            if progress is not None:
                return progress
        return None

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
        from app.frames import admin_helper
        admin_helper.end()

    def _set_running(self, running: bool):
        self.run_btn.setEnabled(not running)
        self.reload_btn.setEnabled(not running)
        self.table.setEnabled(not running)
        self.telemetry_box.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.resume_btn.setEnabled(not running)

    @Slot(str)
    def _on_progress(self, message: str):
        self.status_label.setText(message)

    @Slot(int, int, str)
    def _on_step(self, done: int, total: int, text: str):
        self.step = (done, total, text)

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
        from app.frames import admin_helper
        admin_helper.end()                  # the administrator helper ends with the run
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
            try:
                averages = averages_text(plan_averages(self.db.get_runs(self.last_session_id, include_test=True)))
            except Exception:
                logger.exception("Could not compute the averages")
                averages = ""
            if averages:
                text += "\n" + averages
        else:
            text = f"Session #{self.last_session_id}: {summary}"
        self.status_label.setText(text)
        self.update_resume()
        self.on_session_changed()
        if self.on_plan_finished is not None and self.last_session_id is not None:
            callback, self.on_plan_finished = self.on_plan_finished, None     # one plan, one callback
            callback(self.last_session_id, text)

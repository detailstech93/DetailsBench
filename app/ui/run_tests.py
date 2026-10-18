"""Tests screen -> run: ask how many times, run the automatic tests as one plan, then open the report.

Automatic tests (tool specs, storage, AI, simulation scripts) run together in the automation runner.
Assisted and manual tests (Steam built-in benchmarks, library entries) keep their own flows: one of them
alone goes straight there; mixed with automatic tests they are listed to start after the plan.
"""

import dataclasses
import logging
import webbrowser
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox, QSpinBox,
                               QVBoxLayout)

from app.catalog import TestEntry
from app.ui.tests_page import RunState

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RunChoice:
    repetitions: int
    warmup_runs: int
    cooldown_seconds: int


def split_entries(entries: List[TestEntry]):
    """(automatic workloads for one plan, other entries that keep their own flow)."""
    runnable = [e for e in entries if e.can_run()]
    return ([e for e in runnable if e.source == "workload"], [e for e in runnable if e.source != "workload"])


class RunTestsDialog(QDialog):
    def __init__(self, entries: List[TestEntry], profile, others: List[TestEntry], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Run tests")
        layout = QVBoxLayout(self)
        names = "\n".join(f"  • {e.name}" for e in entries)
        layout.addWidget(QLabel(f"{len(entries)} test(s):\n{names}"))
        form = QFormLayout()
        self.repetitions = QSpinBox()
        self.repetitions.setRange(1, 50)
        self.repetitions.setValue(max(1, int(profile.repetitions)))
        self.warmup = QSpinBox()
        self.warmup.setRange(0, 10)
        self.warmup.setValue(int(profile.warmup_runs))
        self.cooldown = QSpinBox()
        self.cooldown.setRange(0, 3600)
        self.cooldown.setSuffix(" s")
        self.cooldown.setValue(int(profile.cooldown_seconds))
        form.addRow("How many times (measured runs):", self.repetitions)
        form.addRow("Warm-up runs before them (not counted):", self.warmup)
        form.addRow("Pause between runs:", self.cooldown)
        layout.addLayout(form)
        if others:
            note = QLabel("Not in this plan (they need you, start them after it from the Tests screen):\n"
                          + "\n".join(f"  • {e.name}" for e in others))
            note.setStyleSheet("color: #B26A00;")
            layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Next: review and start")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choice(self) -> RunChoice:
        return RunChoice(self.repetitions.value(), self.warmup.value(), self.cooldown.value())


def ask_choice(window, entries, profile, others) -> Optional[RunChoice]:
    dialog = RunTestsDialog(entries, profile, others, window)
    return dialog.choice() if dialog.exec() == QDialog.Accepted else None


def plan_profile(profile, choice: RunChoice):
    """The selected profile with the counts chosen here (its name is kept, so the manifest says where they came from)."""
    return dataclasses.replace(profile, repetitions=choice.repetitions, warmup_runs=choice.warmup_runs,
                               cooldown_seconds=choice.cooldown_seconds,
                               name=f"{profile.name} (Tests screen: {choice.repetitions}x)", builtin=False)


def write_report(db, session_id: int, folder: Path) -> Path:
    from app.export import build_session_export, default_filename, write_export
    export = build_session_export(db, session_id)
    folder.mkdir(parents=True, exist_ok=True)
    return write_export(export, folder / default_filename(export, "html"), "html")


def open_report(path: Path) -> bool:
    return bool(webbrowser.open(path.resolve().as_uri()))


def plan_state(panel, result: dict) -> RunState:
    """Progress of the automation plan (also while LibreHardwareMonitor is being prepared before it)."""
    if "final" in result:
        return RunState(False, *result["final"])
    if panel.is_running():
        result["started"] = True
        done, total, text = panel.step
        return RunState(True, done, total, text)
    if result.get("started"):
        # the thread has ended but its "finished" signal is not delivered yet: the report comes next
        done, total, _ = panel.step
        return RunState(True, done, total, "Writing the report...")
    if panel.lhm.busy():
        return RunState(True, 0, None, "Preparing the temperature sensors (LibreHardwareMonitor)...")
    panel.on_plan_finished = None          # it never started: a later plan must not open this report
    return RunState(False, 0, None, "Not started.")


def start_tests(window, entries: List[TestEntry], ask=ask_choice, opener=open_report) -> bool:
    automatic, others = split_entries(entries)
    if not automatic and len(others) == 1:
        return start_other(window, others[0])
    if not automatic:
        QMessageBox.information(window, "Run tests", "These tests need you; start them one at a time.")
        return False
    panel = window.automation_panel
    if panel.is_running() or window.frame_panel.is_running():
        QMessageBox.information(window, "Run tests", "A run is already going on; wait for it or stop it first.")
        return False
    choice = ask(window, automatic, window.selected_profile(), others)
    if choice is None:
        return False
    folder = Path(panel.output_root).parent / "reports" if panel.output_root else None
    result: dict = {}

    def finished(session_id: int, text: str):
        done, total, _ = panel.step
        first = text.splitlines()[0]
        result["final"] = (done, total, first)
        window.status_label.setText(first)
        if folder is None:
            return
        try:
            path = write_report(window.db, session_id, folder)
        except Exception as e:
            logger.exception("Could not write the report")
            result["final"] = (done, total, f"{first} The report could not be written: {e}")
            window.status_label.setText(f"Session #{session_id} done; the report could not be written: {e}")
            return
        result["final"] = (done, total, f"{first} Report opened: {path.name}")
        window.status_label.setText(f"Session #{session_id} done. Report: {path}")
        opener(path)

    panel.on_plan_finished = finished
    started = panel.start_plan(workloads=[e.ref for e in automatic], profile=plan_profile(window.selected_profile(),
                                                                                         choice))
    if started or panel.lhm.busy():
        # stay on the Tests screen: green bar, percent and Stop there (Ctrl+Shift+X still works)
        window.tests_page.watch(lambda: plan_state(panel, result), panel.emergency_stop)
    else:
        panel.on_plan_finished = None          # cancelled; a later plan must not open this report
    return started


def start_other(window, entry: TestEntry) -> bool:
    """One assisted or manual test: hand it to the flow that already runs it."""
    if window.automation_panel.is_running() or window.frame_panel.is_running():
        QMessageBox.information(window, "Run tests", "A run is already going on; wait for it or stop it first.")
        return False
    if entry.source == "steam":
        game = entry.ref if isinstance(entry.ref, dict) and entry.ref.get("store") == "steam" else None
        frame = window.frame_panel
        if game is None:
            return False
        frame.refresh_games()
        if not frame.select_game(game["app_id"]):
            QMessageBox.information(window, "Run tests", f"{game['name']} is not fully installed.")
            return False
        frame.benchmark_game()
        if frame.is_running() or frame.lhm.busy():
            window.tests_page.watch(
                lambda: RunState(frame.is_running() or frame.lhm.busy(), 0, None, frame.status_label.text()),
                frame.emergency_stop)
        return True
    if entry.source == "library":
        window._on_launch_clicked(entry.ref)
        window.tests_page.offer_record(f"{entry.name} was started. Run its benchmark, then enter the score.",
                                       lambda: window._on_record_clicked(entry.ref))
        return True
    return False

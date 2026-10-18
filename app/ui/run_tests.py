"""Tests screen -> run: ask how many times (and, for games, a preset), run the automatic tests as one plan,
then open the report.

Automatic tests (tool specs, storage, AI, simulation scripts) run together in the automation runner.
Assisted and manual tests (Steam built-in benchmarks, library entries) keep their own flows: one of them
alone goes straight there; mixed with automatic tests they are listed to start after the plan.
"""

import dataclasses
import logging
import os
import sys
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QMessageBox,
                               QSpinBox, QVBoxLayout)

from app.catalog import TestEntry
from app.games import presets
from app.ui.tests_page import RunState

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RunChoice:
    repetitions: int
    warmup_runs: int
    cooldown_seconds: int
    preset: Optional[presets.GamePreset] = None      # None: no game selected (or the dialog had no preset)
    stress_minutes: Optional[int] = None             # None: no stress test selected


PRESET_KEY = "game_preset"          # settings.json: the last preset chosen on the Tests screen
Offered = Dict[str, Tuple[Optional[dict], Optional[str]]]     # app_id -> (adapter choices, or None + why)


def game_of(entry: TestEntry) -> Optional[Tuple[str, str]]:
    """(Steam app id, name) of a game test (a Steam built-in benchmark or a game script), else None."""
    if entry.source == "steam" and isinstance(entry.ref, dict):
        app_id = entry.ref.get("app_id", entry.ref.get("appid"))
        return (str(app_id), entry.ref.get("name") or entry.name) if app_id is not None else None
    script = getattr(entry.ref, "script", None)
    if entry.source == "workload" and isinstance(script, dict) and script.get("steam_app") is not None:
        return str(script["steam_app"]), entry.name
    return None


def read_offered(games: List[Tuple[str, str]], data_dir) -> Offered:
    """What each game's settings allow (read now, so values the game was seen using are learned)."""
    from app.games.settings_file import adapter_for, read_for_dialog
    out: Offered = {}
    for app_id, _ in games:
        if adapter_for(app_id) is None:
            out[app_id] = (None, "the app cannot set this game's settings")
            continue
        try:
            out[app_id] = (read_for_dialog(app_id, data_dir)[0], None)
        except (OSError, ValueError) as e:
            out[app_id] = (None, str(e))
    return out


def save_preset(data_dir, games: List[Tuple[str, str]], preset: "presets.GamePreset", offered: Offered) -> List[str]:
    """Store the preset and, per game, the settings it becomes (what every game benchmark then forces).
    Returns the lines that describe it."""
    from app.games.settings_file import SAVED_KEY
    from app.paths import load_settings, save_setting
    settings, _ = load_settings(data_dir)
    every = settings.get(SAVED_KEY)
    every = dict(every) if isinstance(every, dict) else {}
    lines = []
    for app_id, name in games:
        choices, problem = offered.get(app_id, (None, None))
        lines += presets.summary_lines(app_id, name, preset, choices, problem)
        if choices is not None:
            every[app_id] = presets.plan_preset(app_id, preset, choices)[0]
    save_setting(data_dir, SAVED_KEY, every)
    save_setting(data_dir, PRESET_KEY, preset.to_dict())
    return lines


def last_preset(data_dir) -> "presets.GamePreset":
    from app.paths import load_settings
    return presets.GamePreset.from_dict(load_settings(data_dir)[0].get(PRESET_KEY))


class PresetBox(QGroupBox):
    """Resolution, upscaler, ray tracing and quality for the selected games, with what each game will get."""

    def __init__(self, games: List[Tuple[str, str]], offered: Offered, initial: "presets.GamePreset", parent=None):
        super().__init__("Game settings (one preset for every selected game)", parent)
        self.games, self.offered = games, offered
        form = QFormLayout(self)
        self.combos: Dict[str, QComboBox] = {}
        values = {"resolution": presets.resolutions_offered([o for o, _ in offered.values() if o]),
                  "upscaler": presets.UPSCALERS, "ray_tracing": presets.RAY_TRACING,
                  "frame_gen": presets.FRAME_GEN, "quality": presets.QUALITIES}
        for key, options in values.items():
            combo = QComboBox()
            for value in options:
                combo.addItem(presets.LABELS.get(value, value), value)
            combo.setCurrentIndex(max(0, combo.findData(getattr(initial, key))))
            combo.currentIndexChanged.connect(self.update_summary)
            form.addRow(presets.CHOICE_NAMES[key] + ":", combo)
            self.combos[key] = combo
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        note = QLabel("Before each game starts, the app copies its settings, writes these values and checks them; "
                      "after the run it puts your own settings back.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        form.addRow(note)
        self.update_summary()

    def preset(self) -> "presets.GamePreset":
        return presets.GamePreset(**{k: c.currentData() for k, c in self.combos.items()})

    def lines(self) -> List[str]:
        p = self.preset()
        out = []
        for app_id, name in self.games:
            choices, problem = self.offered.get(app_id, (None, None))
            out += presets.summary_lines(app_id, name, p, choices, problem)
        return out

    def update_summary(self, *_):
        self.summary.setText("\n".join(self.lines()))


def split_entries(entries: List[TestEntry]):
    """(automatic workloads for one plan, other entries that keep their own flow)."""
    runnable = [e for e in entries if e.can_run()]
    return ([e for e in runnable if e.source == "workload"], [e for e in runnable if e.source != "workload"])


class RunTestsDialog(QDialog):
    def __init__(self, entries: List[TestEntry], profile, others: List[TestEntry], parent=None,
                 games: Optional[List[Tuple[str, str]]] = None, offered: Optional[Offered] = None,
                 initial: Optional["presets.GamePreset"] = None):
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
        self.stress_minutes = None
        if any(e.id.startswith("stress_") for e in entries):
            from app.stress.workload import MINUTE_CHOICES, stress_minutes
            self.stress_minutes = QComboBox()
            for m in MINUTE_CHOICES:
                self.stress_minutes.addItem(f"{m} min", m)
            current = stress_minutes()
            self.stress_minutes.setCurrentIndex(max(0, self.stress_minutes.findData(current)))
            form.addRow("Stress test length (each run):", self.stress_minutes)
        layout.addLayout(form)
        self.preset_box = None
        if games:
            self.preset_box = PresetBox(games, offered or {}, initial or presets.GamePreset())
            layout.addWidget(self.preset_box)
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
        return RunChoice(self.repetitions.value(), self.warmup.value(), self.cooldown.value(),
                         self.preset_box.preset() if self.preset_box is not None else None,
                         self.stress_minutes.currentData() if self.stress_minutes is not None else None)


def ask_choice(window, entries, profile, others, games=None, offered=None, initial=None) -> Optional[RunChoice]:
    dialog = RunTestsDialog(entries, profile, others, window, games, offered, initial)
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
    """Open the HTML file in the default browser (on Windows the file's own handler, as a double-click does)."""
    if sys.platform == "win32":
        try:
            os.startfile(str(path.resolve()))
            return True
        except OSError:
            logger.exception("Could not open the report")
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
    if getattr(panel, "asking", False):
        # the start question comes after the sensors are ready; the screen polls while it is open
        return RunState(True, 0, None, "Waiting for you to confirm the run...")
    panel.on_plan_finished = None          # it never started: a later plan must not open this report
    return RunState(False, 0, None, "Not started.")


def start_tests(window, entries: List[TestEntry], ask=ask_choice, opener=open_report) -> bool:
    automatic, others = split_entries(entries)
    lone_game = not automatic and len(others) == 1 and others[0].source == "steam"
    if not automatic and len(others) == 1 and not lone_game:
        return start_other(window, others[0])
    if not automatic and not lone_game:
        QMessageBox.information(window, "Run tests", "These tests need you; start them one at a time.")
        return False
    panel = window.automation_panel
    if panel.is_running() or window.frame_panel.is_running():
        QMessageBox.information(window, "Run tests", "A run is already going on; wait for it or stop it first.")
        return False
    data_dir = window.frame_panel.data_dir
    games = [g for g in (game_of(e) for e in automatic + others) if g]
    offered = read_offered(games, data_dir) if games else {}
    choice = ask(window, automatic or others, window.selected_profile(), [] if lone_game else others,
                 games, offered, last_preset(data_dir) if games else None)
    if choice is None:
        return False
    if games and choice.preset is not None:
        try:
            lines = save_preset(data_dir, games, choice.preset, offered)
            window.status_label.setText(" ".join(line.strip() for line in lines)[:300])
        except (OSError, ValueError) as e:
            QMessageBox.warning(window, "Run tests", f"The game settings could not be saved: {e}")
            return False
    if choice.stress_minutes is not None:
        from app.paths import save_setting
        from app.stress.workload import MINUTES_KEY
        try:
            save_setting(data_dir, MINUTES_KEY, int(choice.stress_minutes))
        except (OSError, ValueError) as e:
            QMessageBox.warning(window, "Run tests", f"The stress test length could not be saved: {e}")
            return False
    if lone_game:
        return start_other(window, others[0], choice)
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
        window.tests_page.offer_report(lambda: opener(path))
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


def start_other(window, entry: TestEntry, choice: Optional[RunChoice] = None) -> bool:
    """One assisted or manual test: hand it to the flow that already runs it. With a choice from the run
    dialog, a Steam game uses its counts and the preset saved for it (no settings window)."""
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
        if choice is None:
            frame.benchmark_game()
        else:
            frame.benchmark_game(ask_settings=False, counts=(choice.warmup_runs, choice.repetitions))
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

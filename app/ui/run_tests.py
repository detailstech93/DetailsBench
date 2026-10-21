"""Tests screen -> run: ask how many times (and, for games, a preset), run the automatic tests as one plan,
then open the report.

Automatic tests (tool specs, storage, AI, simulation scripts) run together in the automation runner.
Assisted and manual tests (Steam built-in benchmarks, library entries) keep their own flows: one of them
alone goes straight there; mixed with automatic tests they are listed to start after the plan.
"""

import dataclasses
import logging
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel,
                               QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from app.catalog import TestEntry
from app.games import presets
from app.ui.tests_page import RunState, elapsed_text

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RunChoice:
    repetitions: int
    warmup_runs: int
    cooldown_seconds: int
    preset: Optional[presets.GamePreset] = None      # None: no game selected (or the dialog had no preset)
    stress_minutes: Optional[int] = None             # None: no stress test selected
    extra_runs: int = 0                              # extra runs allowed when the scores vary a lot
    game_presets: Optional[Dict[str, presets.GamePreset]] = None     # {app id: preset}; None: `preset` for all
    shutdown_after: bool = False                     # shut the computer down when the run ends (app/ui/shutdown_after.py)


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


def save_preset(data_dir, games: List[Tuple[str, str]], preset: "presets.GamePreset", offered: Offered,
                per_game: Optional[Dict[str, "presets.GamePreset"]] = None) -> List[str]:
    """Store the preset and, per game, the settings it becomes (what every game benchmark then forces).
    per_game: a preset of its own for some games. Returns the lines that describe it."""
    from app.games.settings_file import SAVED_KEY
    from app.paths import load_settings, save_setting
    settings, _ = load_settings(data_dir)
    every = settings.get(SAVED_KEY)
    every = dict(every) if isinstance(every, dict) else {}
    lines = []
    for app_id, name in games:
        choices, problem = offered.get(app_id, (None, None))
        mine = (per_game or {}).get(app_id, preset)
        lines += presets.summary_lines(app_id, name, mine, choices, problem)
        if choices is not None:
            every[app_id] = presets.plan_preset(app_id, mine, choices)[0]
    save_setting(data_dir, SAVED_KEY, every)
    save_setting(data_dir, PRESET_KEY, preset.to_dict())
    return lines


def last_preset(data_dir) -> "presets.GamePreset":
    from app.paths import load_settings
    return presets.GamePreset.from_dict(load_settings(data_dir)[0].get(PRESET_KEY))


class PresetBox(QGroupBox):
    """The games' preset (owner, 2026-10-20): mode (raw / upscaling / upscaling + frame generation), the upscaler
    and frame generation this PC's card runs (the best one marked "recommended", the others not offered), quality,
    resolution and ray tracing; one preset for every game or one per game, with what each game will get."""

    KEYS = ("mode", "upscaler", "frame_gen", "quality", "resolution", "ray_tracing")
    NAMES = {"mode": "Mode", "upscaler": "Upscaler", "frame_gen": "Frame generation", "quality": "Quality",
             "resolution": "Resolution", "ray_tracing": "Ray tracing"}

    def __init__(self, games: List[Tuple[str, str]], offered: Offered, initial: "presets.GamePreset",
                 gpu: str = "", parent=None):
        super().__init__("Game settings", parent)
        self.games, self.offered, self.gpu = games, offered, gpu
        self.per_game: Dict[str, presets.GamePreset] = {app_id: initial for app_id, _ in games}
        self.all_preset = initial
        form = QFormLayout(self)
        self.same = QCheckBox("One preset for every game")
        self.same.setChecked(True)
        self.which = QComboBox()
        for app_id, name in games:
            self.which.addItem(name, app_id)
        if len(games) > 1:
            form.addRow(self.same)
            form.addRow("Preset of:", self.which)
        self.same.toggled.connect(self._target_changed)
        self.which.currentIndexChanged.connect(self._target_changed)
        self.combos: Dict[str, QComboBox] = {}
        self.rows: Dict[str, QLabel] = {}
        for key in self.KEYS:
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda _=0, k=key: self._changed(k))
            label = QLabel(self.NAMES[key] + ":")
            form.addRow(label, combo)
            self.combos[key], self.rows[key] = combo, label
        if gpu:
            card = QLabel(f"Graphics card: {gpu}. Only what it can run is offered.")
            card.setStyleSheet("color: palette(placeholder-text);")
            form.addRow(card)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        note = QLabel("Before each game starts, the app copies its settings, writes these values and checks them; "
                      "after the run it puts your own settings back. A value a game does not have is not set "
                      "(listed above), never guessed.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        form.addRow(note)
        self._load(initial)
        self._target_changed()

    # ---- what the card runs ------------------------------------------------------------------------------
    def _upscalers(self, with_frame_gen: bool) -> List[str]:
        out = presets.upscalers_for(self.gpu) if self.gpu else ["dlss", "fsr", "xess"]
        return [u for u in out if not with_frame_gen or self._frame_gens(u)]

    def _frame_gens(self, upscaler: str) -> List[str]:
        if not self.gpu:
            return ["x2", "x3", "x4"] if upscaler == "dlss" else ["x2"]
        return presets.frame_gens_for(self.gpu, upscaler)

    def _fill(self, key: str, pairs: List[Tuple[str, str]], value: str):
        combo = self.combos[key]
        combo.blockSignals(True)
        combo.clear()
        for data, text in pairs:
            combo.addItem(text, data)
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _load(self, p: "presets.GamePreset"):
        """Show a preset in the boxes (the lists follow the mode and the upscaler)."""
        mode = presets.KEEP if p.upscaler == presets.KEEP and p.frame_gen == presets.KEEP else presets.mode_of(p)
        modes = [m for m in presets.MODES if m != "upscaling_fg" or self._upscalers(True)]
        self._fill("mode", [(presets.KEEP, "Keep the game's own")] +
                   [(m, presets.MODE_LABELS[m]) for m in modes], mode)
        mode = self.combos["mode"].currentData()
        ups = self._upscalers(mode == "upscaling_fg")
        best = presets.recommended_upscaler(self.gpu) if self.gpu else None
        best = best if best in ups else (ups[0] if ups else None)
        self._fill("upscaler", presets.choice_labels(ups, best), p.upscaler if p.upscaler in ups else best)
        gens = self._frame_gens(self.combos["upscaler"].currentData() or "")
        self._fill("frame_gen", presets.choice_labels(gens, "x2" if "x2" in gens else None),
                   p.frame_gen if p.frame_gen in gens else "x2")
        self._fill("quality", [(q, presets.LABELS[q]) for q in presets.QUALITIES[1:]] +
                   [(presets.KEEP, "Keep the game's own")], p.quality)
        res = presets.resolutions_offered([o for o, _ in self.offered.values() if o])
        self._fill("resolution", [(r, presets.LABELS.get(r, r)) for r in res[1:]] +
                   [(presets.KEEP, "Keep the game's own")], p.resolution)
        self._fill("ray_tracing", [("off", "Off"), ("on", "On"), (presets.KEEP, "Keep the game's own")],
                   p.ray_tracing)
        for key, shown in (("upscaler", mode in ("upscaling", "upscaling_fg")), ("frame_gen", mode == "upscaling_fg")):
            self.combos[key].setVisible(shown)
            self.rows[key].setVisible(shown)

    def _read(self) -> "presets.GamePreset":
        c = {k: self.combos[k].currentData() for k in self.KEYS}
        if c["mode"] == presets.KEEP:
            return presets.GamePreset(resolution=c["resolution"], quality=c["quality"], ray_tracing=c["ray_tracing"])
        return presets.game_preset(c["mode"], c["upscaler"] or "off", c["frame_gen"] or "off", c["quality"],
                                   c["resolution"], c["ray_tracing"])

    # ---- one preset for all, or one per game --------------------------------------------------------------
    def _target_changed(self, *_):
        one = self.same.isChecked() or len(self.games) <= 1
        self.which.setEnabled(not one)
        if one:
            self._load(self.all_preset)
        elif self.which.currentData() is not None:
            self._load(self.per_game[self.which.currentData()])
        self.update_summary()

    def _changed(self, key: str):
        p = self._read()
        if key in ("mode", "upscaler"):
            self._load(p)                       # the upscaler / frame generation lists follow
            p = self._read()
        if self.same.isChecked() or len(self.games) <= 1:
            self.all_preset = p
            self.per_game = {app_id: p for app_id, _ in self.games}
        elif self.which.currentData() is not None:
            self.per_game[self.which.currentData()] = p
        self.update_summary()

    def preset(self) -> "presets.GamePreset":
        """One preset for every game (or the first game's when each has its own)."""
        if self.same.isChecked() or len(self.games) <= 1:
            return self.all_preset
        return self.per_game[self.games[0][0]]

    def presets(self) -> Dict[str, "presets.GamePreset"]:
        """{app id: its preset}."""
        if self.same.isChecked() or len(self.games) <= 1:
            return {app_id: self.all_preset for app_id, _ in self.games}
        return dict(self.per_game)

    def lines(self) -> List[str]:
        out = []
        chosen = self.presets()
        for app_id, name in self.games:
            choices, problem = self.offered.get(app_id, (None, None))
            out += presets.summary_lines(app_id, name, chosen[app_id], choices, problem)
        return out

    def update_summary(self, *_):
        self.summary.setText("\n".join(self.lines()))


def leave_out_typed(entries: List[TestEntry]) -> Tuple[List[TestEntry], List[TestEntry]]:
    """(entries to run, tests left out). A test that waits for a typed score only runs alone, never in a run of
    several tests (a test selected by mistake must not stop the others waiting for someone to type)."""
    if len(entries) <= 1:
        return list(entries), []
    return ([e for e in entries if not e.waits_for_typed_score()], [e for e in entries if e.waits_for_typed_score()])


def split_entries(entries: List[TestEntry]):
    """(automatic workloads for one plan, other entries that keep their own flow)."""
    runnable = [e for e in entries if e.can_run()]
    return ([e for e in runnable if e.source == "workload"], [e for e in runnable if e.source != "workload"])


class RunTestsDialog(QDialog):
    def __init__(self, entries: List[TestEntry], profile, others: List[TestEntry], parent=None,
                 games: Optional[List[Tuple[str, str]]] = None, offered: Optional[Offered] = None,
                 initial: Optional["presets.GamePreset"] = None, saved: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Run tests" if saved is None else "Run a saved plan")
        outer = QVBoxLayout(self)
        # everything but the buttons scrolls: with many tests and games the dialog is taller than the screen
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        names = "\n".join(f"  • {e.name}" for e in entries)
        layout.addWidget(QLabel(f"{len(entries)} test(s):\n{names}"))
        if saved is not None:
            layout.addWidget(QLabel(f"Numbers from the saved plan ({saved.get('created') or 'no date'}); you can "
                                    "still change them."))
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
        from app.automation.runner import CV_LIMIT_PERCENT
        from app.ui.automation_panel import EXTRA_RUNS
        self.extra_runs = QCheckBox(f"Up to {EXTRA_RUNS} more runs when the scores vary more than "
                                    f"{CV_LIMIT_PERCENT:g}% (needs 3 runs or more)")
        self.extra_runs.setChecked(True if saved is None else saved.get("extra_runs", 0) > 0)
        self.extra_runs_count = EXTRA_RUNS
        form.addRow("Uneven scores:", self.extra_runs)
        form.addRow("", QLabel("A run that fails is stored as failed and run once more by itself."))
        from app.ui import shutdown_after
        self.shutdown_after = QCheckBox("Shut down the computer when the run ends (only once every result and "
                                        "report is saved; 60 s to cancel)")
        not_offered = shutdown_after.why_not_offered()
        if not_offered:
            self.shutdown_after.setEnabled(False)
            self.shutdown_after.setToolTip(not_offered.capitalize() + ".")
        form.addRow("When it ends:", self.shutdown_after)
        self.stress_minutes = None
        if any(e.id.startswith("stress_") for e in entries):
            from app.stress.workload import MINUTE_CHOICES, stress_minutes
            self.stress_minutes = QComboBox()
            for m in MINUTE_CHOICES:
                self.stress_minutes.addItem(f"{m} min", m)
            from app.stress.workload import DEFAULT_MINUTES
            # not chosen for this plan yet: 10 minutes in a run of several tests (owner, 2026-10-20), else the last
            current = (saved or {}).get("stress_minutes") or (DEFAULT_MINUTES if len(entries) > 1 else stress_minutes())
            self.stress_minutes.setCurrentIndex(max(0, self.stress_minutes.findData(current)))
            form.addRow("Stress test length (each run):", self.stress_minutes)
        layout.addLayout(form)
        self.preset_box = None
        if games:
            info = getattr(parent, "system_info", None) or {}
            gpu = presets.main_gpu(str(info.get("gpu_name") or "").split(", "))
            self.preset_box = PresetBox(games, offered or {}, initial or presets.GamePreset(), gpu)
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
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll)
        outer.addWidget(buttons)
        self._fit_screen(body)

    def _fit_screen(self, body: QWidget):
        """As tall as the content, but never taller than the screen (then the content scrolls)."""
        screen = (self.parent().screen() if self.parent() is not None else None) or QApplication.primaryScreen()
        room = screen.availableGeometry().height() * 0.85 if screen is not None else 800
        want = body.sizeHint().height() + 80
        self.scroll.setMinimumHeight(int(min(want, room) - 80))
        self.resize(max(self.sizeHint().width(), body.sizeHint().width() + 40), int(min(want, room)))

    def choice(self) -> RunChoice:
        return RunChoice(self.repetitions.value(), self.warmup.value(), self.cooldown.value(),
                         self.preset_box.preset() if self.preset_box is not None else None,
                         self.stress_minutes.currentData() if self.stress_minutes is not None else None,
                         self.extra_runs_count if self.extra_runs.isChecked() else 0,
                         self.preset_box.presets() if self.preset_box is not None else None,
                         self.shutdown_after.isChecked() and self.shutdown_after.isEnabled())


def ask_choice(window, entries, profile, others, games=None, offered=None, initial=None,
               saved=None) -> Optional[RunChoice]:
    dialog = RunTestsDialog(entries, profile, others, window, games, offered, initial, saved)
    return dialog.choice() if dialog.exec() == QDialog.Accepted else None


def plan_profile(profile, choice: RunChoice):
    """The selected profile with the counts chosen here (its name is kept, so the manifest says where they came from)."""
    return dataclasses.replace(profile, repetitions=choice.repetitions, warmup_runs=choice.warmup_runs,
                               cooldown_seconds=choice.cooldown_seconds,
                               name=f"{profile.name} (Tests screen: {choice.repetitions}x)", builtin=False)


def write_report(db, session_id: int, folder: Path, only: Optional[str] = None, at: Optional[str] = None) -> Path:
    """One report per test, named after the test and the run's start time; several tests go into a folder named by
    that time with a final report (app/report_set.py). Returns the report to open."""
    from app.export import build_session_export
    from app.report_set import write_report_set
    return write_report_set(build_session_export(db, session_id), folder, only, at)


def open_report(path: Path) -> bool:
    """Open the HTML file in the default browser (on Windows the file's own handler, as a double-click does)."""
    if sys.platform == "win32":
        try:
            os.startfile(str(path.resolve()))
            return True
        except OSError:
            logger.exception("Could not open the report")
    # WSL with no Linux browser: Python still "opens" it through gio, which only says it found no application
    # (owner, 2026-10-20), so Windows' browser is used first there
    if sys.platform.startswith("linux") and not linux_html_app() and open_in_windows(path):
        return True
    if webbrowser.open(path.resolve().as_uri()):
        return True
    return open_in_windows(path)


LINUX_BROWSERS = ("firefox", "google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge",
                  "epiphany", "konqueror", "falkon")


def linux_html_app(run=subprocess.run, which=shutil.which) -> bool:
    """A Linux program opens HTML files here (a default for text/html, or a known browser installed)."""
    if any(which(b) for b in LINUX_BROWSERS):
        return True
    xdg = which("xdg-mime")
    if not xdg:
        return False
    try:
        out = run([xdg, "query", "default", "text/html"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.strip())


def on_wsl(version_file: str = "/proc/version") -> bool:
    """Linux inside Windows (WSL): its kernel name says Microsoft."""
    try:
        return "microsoft" in Path(version_file).read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False


def open_in_windows(path: Path, platform: Optional[str] = None, wsl=on_wsl, run=subprocess.run,
                    which=shutil.which) -> bool:
    """WSL with no Linux browser (owner, 2026-10-20): the report opens in Windows' own browser through explorer.exe
    with the file's Windows path. Anywhere else, or without wslpath and explorer.exe, nothing is opened."""
    if not (platform or sys.platform).startswith("linux") or not wsl():
        return False
    wslpath, explorer = which("wslpath"), which("explorer.exe")
    if not (wslpath and explorer):
        return False
    try:
        win = run([wslpath, "-w", str(path)], capture_output=True, text=True, timeout=10).stdout.strip()
        if not win:
            return False
        run([explorer, win], capture_output=True, timeout=30)     # explorer.exe answers 1 even when it opened
        return True
    except (OSError, subprocess.SubprocessError):
        logger.exception("Could not open the report in Windows")
        return False


def plan_state(panel, result: dict) -> RunState:
    """Progress of the automation plan (also while LibreHardwareMonitor is being prepared before it)."""
    if "final" in result:
        return RunState(False, *result["final"])
    if panel.is_running():
        result["started"] = True
        done, total, text = panel.step
        progress = panel.run_progress() if hasattr(panel, "run_progress") else None
        if progress is not None:
            fraction, left = progress
            return RunState(True, done, total, f"{text} ({elapsed_text(left)} left)", fraction)
        return RunState(True, done, total, text)
    if result.get("started"):
        # the thread has ended but its "finished" signal is not delivered yet: the report comes next
        done, total, _ = panel.step
        return RunState(True, done, total, "Writing the report...")
    if panel.lhm.active():
        return RunState(True, 0, None, "Preparing the temperature sensors (LibreHardwareMonitor)...")
    if getattr(panel, "asking", False):
        # the start question comes after the sensors are ready; the screen polls while it is open
        return RunState(True, 0, None, "Waiting for you to confirm the run...")
    panel.on_plan_finished = None          # it never started: a later plan must not open this report
    return RunState(False, 0, None, "Not started.")


def _ask_license(window, product: str, text: str) -> Optional[str]:
    from app.ui.long_question import ask_buttons
    return ask_buttons(window, f"{product}: license agreement", text,
                       [("Open the agreement", "open"), ("I accept", "yes"), ("I do not accept", "no")], default=0)


def check_licenses(window, entries: List[TestEntry], ask=_ask_license, open_url=None) -> List[TestEntry]:
    """Tests whose maker's license must be accepted in the app first (a workload's `license`): ask once per
    license; the tests of a license not accepted are left out of the run."""
    open_url = open_url or webbrowser.open
    refused, decided = set(), set()
    for e in entries:
        ref = e.ref
        lic = getattr(ref, "license", None)
        if not lic or lic[0] in decided or ref.license_ok():
            continue
        key, product, question = lic
        decided.add(key)
        text = question()
        while True:
            answer = ask(window, product, text)
            if answer == "open":
                open_url(re.search(r"https://\S+", text).group(0))
                continue
            break
        if answer == "yes":
            try:
                ref.accept_license()
            except (OSError, ValueError) as err:
                QMessageBox.warning(window, "Run tests", f"Your answer could not be saved: {err}")
                refused.add(key)
        else:
            refused.add(key)
    return [e for e in entries if not (getattr(e.ref, "license", None) and e.ref.license[0] in refused)]


def _save_plan(data_dir, entries: List[TestEntry], choice: RunChoice) -> Optional[Path]:
    """The plan file for "Run a saved plan..." (app/experiment.py); a failure is logged, the run goes on."""
    from app import experiment
    try:
        return experiment.save(data_dir, [e.id for e in entries], choice)
    except (OSError, ValueError, TypeError):
        logger.exception("Could not save the plan file")
        return None


def start_tests(window, entries: List[TestEntry], ask=ask_choice, opener=open_report, licenses=check_licenses,
                saved: Optional[dict] = None) -> bool:
    """`saved`: a plan read by app/experiment.py; its numbers fill the dialog (the owner still confirms)."""
    asked = licenses(window, entries)
    if len(asked) < len(entries):
        if not asked:
            window.status_label.setText("Nothing was run: the license agreement was not accepted.")
            return False
        window.status_label.setText("Tests whose license agreement was not accepted are left out.")
    entries = asked
    if any(e.group == "game" for e in entries) and any(e.group != "game" for e in entries):
        # owner, 2026-10-20: games and tests never run together (a saved plan from before could mix them)
        QMessageBox.information(window, "Run tests", "Games and tests do not run together: run the tests from "
                                "Tests and the games from Games.")
        return False
    entries, typed = leave_out_typed(entries)
    if typed:
        names = ", ".join(e.name for e in typed)
        if not entries:
            QMessageBox.information(window, "Run tests", f"Not started: {names} wait for a score typed by you, "
                                    "so they only run one at a time. Select one of them alone.")
            return False
        window.status_label.setText(f"Left out (they wait for a score typed by you; run each alone): {names}")
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
    from app.ui import reports_folder
    reports_folder.choose(window)                  # the first time: where all reports go
    data_dir = window.frame_panel.data_dir
    games = [g for g in (game_of(e) for e in automatic + others) if g]
    offered = read_offered(games, data_dir) if games else {}
    profile, initial = window.selected_profile(), last_preset(data_dir) if games else None
    extra = {}
    if saved is not None:
        profile = dataclasses.replace(profile, repetitions=saved["repetitions"], warmup_runs=saved["warmup_runs"],
                                      cooldown_seconds=saved["cooldown_seconds"])
        if games and saved.get("game_preset"):
            initial = presets.GamePreset.from_dict(saved["game_preset"])
        extra = {"saved": saved}
    if len(automatic) == 1 and automatic[0].waits_for_typed_score():
        choice = RunChoice(1, 0, 0)         # one run: its page opens and the owner types its score
    else:
        choice = ask(window, automatic or others, profile, [] if lone_game else others, games, offered, initial,
                     **extra)
    if choice is None:
        return False
    _save_plan(data_dir, automatic + others, choice)
    if games and choice.preset is not None:
        try:
            lines = save_preset(data_dir, games, choice.preset, offered, choice.game_presets)
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
    return launch(window, automatic, choice, opener)


def launch(window, automatic: List[TestEntry], choice: RunChoice, opener=open_report,
           confirm=None, tools_ready: bool = False) -> bool:
    """Start the plan of automatic tests; the report is written (and opened) when it ends."""
    panel = window.automation_panel
    from app.ui import reports_folder
    folder = reports_folder.folder(window)
    result: dict = {}

    written, seen = set(), {"now": None}

    def step_report(done: int, total: int, text: str):
        """Several tests: each test's report is saved in the run's folder once it is over (not opened; only the
        final report opens at the end)."""
        from app.export import build_session_export
        from app.report_set import finished_tests, test_of_step, write_test_report
        now = test_of_step(text)
        if folder is None or panel.last_session_id is None or now == seen["now"] or text.startswith("Pause"):
            return                              # the same test still going on: nothing new is over
        seen["now"] = now
        export = build_session_export(window.db, panel.last_session_id)
        for name in finished_tests(export, text):
            if name not in written:
                write_test_report(export, folder, name)
                written.add(name)

    def finished(session_id: int, text: str):
        report = written_report(session_id, text)
        if len(automatic) == 1 and automatic[0].waits_for_typed_score():
            typed_followup(window, automatic[0], session_id, opener)
        if getattr(choice, "shutdown_after", False):
            from app.ui import shutdown_after
            shutdown_after.after_run(window, session_id, panel.last_summary, report)

    def written_report(session_id: int, text: str) -> Optional[Path]:
        panel.on_step_report = None
        done, total, _ = panel.step
        first = text.splitlines()[0]
        result["final"] = (done, total, first)
        window.status_label.setText(first)
        if folder is None:
            return None
        try:
            path = write_report(window.db, session_id, folder)
        except Exception as e:
            logger.exception("Could not write the report")
            result["final"] = (done, total, f"{first} The report could not be written: {e}")
            window.status_label.setText(f"Session #{session_id} done; the report could not be written: {e}")
            return None
        result["final"] = (done, total, f"{first} Report opened: {path.name}")
        window.last_report_path = path
        window.status_label.setText(f"Session #{session_id} done. Report: {path}")
        window.tests_page.offer_report(lambda: opener(path))
        opener(path)
        return path

    panel.on_plan_finished = finished
    panel.on_step_report = step_report if len(automatic) > 1 else None
    started = panel.start_plan(tools_ready=tools_ready, workloads=[e.ref for e in automatic],
                               profile=plan_profile(window.selected_profile(), choice), extra_runs=choice.extra_runs,
                               confirm=confirm)
    if started or panel.lhm.active():
        # stay on the Tests screen: green bar, percent and Stop there (Ctrl+Shift+X still works)
        window.tests_page.watch(lambda: plan_state(panel, result), panel.emergency_stop,
                                skip=panel.skip_test if len(automatic) > 1 else None)
    else:
        panel.on_plan_finished = None          # cancelled; a later plan must not open this report
        panel.on_step_report = None
    return started


RESULT_PAGE = re.compile(r"https://browser\.geekbench\.com/v\d+/(?:cpu|gpu|compute)/\d+(?![\w/?])")


def _ask_typed(window, name: str, session_id: int) -> Optional[dict]:
    from app.ui.record_dialog import RecordResultDialog
    dialog = RecordResultDialog(name, f"#{session_id}", "points", window)
    return dialog.values if dialog.exec() == QDialog.Accepted else None


def typed_followup(window, entry: TestEntry, session_id: int, opener=None, ask=_ask_typed) -> Optional[str]:
    """A workload whose score is typed (Geekbench 6 on Linux): its result page opens, then "Enter the score..."
    stores the typed score in the same session, as a manual entry next to the run, and writes the report again.
    The result's page, or None when the run left none."""
    try:
        rows = window.db.get_runs(session_id, entry.id, include_test=True)
    except Exception:
        logger.exception("Could not read the run back")
        rows = []
    pages = [m for r in rows for m in RESULT_PAGE.findall(" ".join(str(v) for v in dict(r).values() if v))]
    if not pages:
        window.status_label.setText(f"{entry.name} left no result page to read its score from.")
        return None
    page = pages[-1]
    window.tests_page.open_url(page)

    def record():
        values = ask(window, entry.name, session_id)
        if values is None:
            return None
        try:
            window.db.create_result(session_id, entry.id, getattr(entry.ref, "benchmark_type", "cpu"),
                                    values["score"], values["unit"], benchmark_version=entry.ref.version(),
                                    is_manual=True, status=values["status"], is_warmup=values["is_warmup"],
                                    run_index=len(rows), note="; ".join(x for x in (values["note"],
                                                                                   f"typed from {page}") if x))
        except Exception as e:
            logger.exception("Could not save the typed score")
            window.status_label.setText(f"The score was NOT saved: {e}")
            return None
        from app.ui import reports_folder
        folder = reports_folder.folder(window)
        if folder is None:
            return None
        from datetime import datetime
        path = write_report(window.db, session_id, folder, only=entry.id, at=datetime.now().isoformat(timespec="seconds"))
        window.status_label.setText(f"Score saved in session #{session_id}. Report: {path}")
        window.tests_page.offer_report(lambda: (opener or open_report)(path))
        (opener or open_report)(path)
        return path
    window.tests_page.offer_record(f"{entry.name} finished: its score is on the page that opened ({page}). "
                                   "Enter it.", record)
    return page


def record_and_report(window, entry: TestEntry, opener=None) -> Optional[Path]:
    """The typed score is stored, then the session's report is written and opened (PugetBench gets its own
    section there)."""
    from app.ui import reports_folder
    reports_folder.choose(window)                  # the first time: where all reports go
    sid = window._on_record_clicked(entry.ref)
    folder = reports_folder.folder(window)
    if sid is None or folder is None:
        return None
    try:
        from datetime import datetime
        runs = window.db.get_runs(sid, include_test=True)
        latest = max(runs, key=lambda r: r["id"])["benchmark_name"] if runs else None
        path = write_report(window.db, sid, folder, only=latest, at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:
        logger.exception("Could not write the report")
        window.status_label.setText(f"The score was saved; the report could not be written: {e}")
        return None
    opener = opener or open_report
    window.status_label.setText(f"Score saved in session #{sid}. Report: {path}")
    window.tests_page.offer_report(lambda: opener(path))
    opener(path)
    return path


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
        if frame.is_running() or frame.lhm.active():
            window.tests_page.watch(
                lambda: RunState(frame.is_running() or frame.lhm.active(), 0, None, frame.status_label.text()),
                frame.emergency_stop)
        return True
    if entry.source == "library":
        window._on_launch_clicked(entry.ref)
        window.tests_page.offer_record(f"{entry.name} was started. Run its benchmark, then enter the score.",
                                       lambda: record_and_report(window, entry))
        return True
    return False

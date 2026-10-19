"""Run tab section for frame capture with PresentMon (Phase 6).

The user selects the official PresentMon once (kept in settings.json), starts the
game themselves, picks its process, and confirms. Each capture run shows a UAC
prompt; the app stays non-elevated. Runs go through AutomationWorker (QThread) into a new
session: score = average FPS, 1% low and the other numbers in result_metrics.

The process list is read in a worker thread and shown only; just the chosen
process name (and pid for the capture) is used, nothing else is stored.

"Benchmark game..." does it all for one installed Steam game (app/frames/game_benchmark.py): one
UAC prompt for PresentMon, the game is launched through Steam, its process is found by itself, and
the warmup and measured windows are captured and stored without any further step.
"""

import dataclasses
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout,
)

from app.automation.runner import PlanItem, RunSummary
from app.automation.workload import WorkloadResult
from app.frames import provision
from app.frames.presentmon import (
    CAPTURE_FLAGS_VERIFIED, SETTING_KEY, FrameCaptureWorkload, check_presentmon_path,
)
from app.paths import get_data_dir, load_settings, save_setting
from app.telemetry import sources as telemetry_sources
from data.game_routes import route_for
from data.game_start_keys import menu_steps_for, start_keys_for
from app.ui.automation_panel import STOP_SHORTCUT, TELEMETRY_TIP, AutomationWorker

logger = logging.getLogger(__name__)


REMOTE_WARNING = ("WARNING: this is a Remote Desktop session. PresentMon recorded no frames in one on the "
                  "test machine; run the capture at the computer's own screen.")
# asked once, the first time a game is benchmarked on Linux (the owner's choice of 2026-10-19)
LINUX_CONSENT_TEXT = ("Game benchmarks on Linux measure with MangoHud. For that, the app starts Steam itself, "
                      "with MangoHud switched on for that Steam only (the environment variable MANGOHUD=1 and the "
                      "app's own MangoHud settings). Nothing in Steam's settings or your MangoHud settings is "
                      "changed, and nothing stays switched on after that Steam exits.\n\n"
                      "Several games in a row use that same Steam. If Steam is already open without MangoHud "
                      "(opened by you), the app asks it to exit by itself (steam -shutdown, like Steam menu > "
                      "Exit) and starts it again with MangoHud; close your own games first. While that Steam is "
                      "open, other Vulkan games you start from it also load MangoHud, without its overlay.\n\n"
                      "Allow this? You are asked only this once.")


def is_remote_session() -> bool:
    """True inside a Remote Desktop session (GetSystemMetrics SM_REMOTESESSION); False elsewhere or on error."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetSystemMetrics(0x1000))
    except (OSError, AttributeError):
        return False


def read_os_release(path: str = "/etc/os-release") -> dict:
    """KEY=value pairs of /etc/os-release ({} when missing)."""
    data = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return data


def run_commands(commands: List[List[str]], runner: Callable = subprocess.run) -> str:
    """Run each argv in order (no shell); stop at the first failure. pkexec 126/127 = prompt declined."""
    for argv in commands:
        try:
            res = runner(argv, capture_output=True, text=True, timeout=900)
        except Exception as e:
            return f"failed: {e}"
        if res.returncode in (126, 127) and argv and argv[0].endswith("pkexec"):
            return "cancelled (password prompt declined)"
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-1:] or [""]
            return f"failed (exit code {res.returncode}): {tail[0]}"
    return "done"


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


class TaskWorker(QThread):
    """Runs one slow call (network, download, signature check, package install) off the GUI thread."""
    done = Signal(object)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:
            logger.exception("Background task failed")
            self.done.emit(e)


def game_launch_args(app_id, forced: Optional[Dict[str, object]] = None) -> List[str]:
    """Fixed launch flags from the game's settings adapter and its menu steps, plus the forced values for
    a game forced with launch arguments (an unknown value adds nothing; ForcedSettings.apply refuses it)."""
    from app.games.settings_file import adapter_for
    from data.game_start_keys import menu_launch_args
    adapter = adapter_for(app_id)
    if adapter is None:
        return menu_launch_args(app_id)
    args = list(getattr(adapter.module, "LAUNCH_ARGS", []))
    if adapter.base == "launch" and forced:
        args += adapter.module.launch_args(forced)
    return args + [a for a in menu_launch_args(app_id) if a not in args]


def launch_url_text(spec) -> str:
    if spec.launch_args:
        return f"steam://run/{spec.app_id}//{' '.join(spec.launch_args)}/"
    return f"steam://rungameid/{spec.app_id}"


def settings_problem(rec) -> Optional[str]:
    """Why a benchmark must not start with these settings (refused or not written as asked), or None."""
    if rec.refused:
        return "settings not forced: " + "; ".join(f"{k}: {why}" for k, why in rec.refused.items())
    if rec.mismatch_before_launch:
        return "settings not as written: " + ", ".join(
            f"{k} is {a!r}, not {e!r}" for k, (e, a) in rec.mismatch_before_launch.items())
    return None


def mark_unverified(capture, rec):
    """Runs measured with settings the game did not keep are not valid results (no score)."""
    if getattr(rec, "unchecked", None):
        capture.message += "; not checked after the run: " + ", ".join(rec.unchecked)
    if rec.mismatch_after_run is None:
        why = "the game settings could not be checked after the run"
    elif rec.mismatch_after_run:
        why = "the game did not keep the forced settings: " + ", ".join(
            f"{k} was {a!r}, not {e!r}" for k, (e, a) in rec.mismatch_after_run.items())
    else:
        return
    for w in getattr(capture, "windows", []):
        if w.result.status == "completed":
            w.result = WorkloadResult(status="invalid", error=why, command=w.result.command)
    capture.message += "; " + why


class GameBenchmarkWorker(QThread):
    """Records one game benchmark, then stores its windows through AutomationRunner (no GUI access)."""
    progress = Signal(str)
    finished_plan = Signal(object)              # RunSummary, or an error str

    def __init__(self, spec, run_dir: Path, create_session: Callable, db, output_root: Path,
                 telemetry: Optional[Callable] = None, benchmark_factory: Optional[Callable] = None,
                 forced=None):
        super().__init__()
        self.forced = forced                    # app.games.settings_file.ForcedSettings, or None
        import threading
        self.stop_event = threading.Event()
        self.spec = spec
        self.run_dir = run_dir
        self.create_session = create_session
        self.db = db
        self.output_root = output_root
        self.telemetry = telemetry
        self.benchmark_factory = benchmark_factory
        self.session_id: Optional[int] = None
        self.capture_message = ""

    def stop(self):
        self.stop_event.set()

    def run(self):
        from app.automation.runner import AutomationRunner
        from app.frames.game_benchmark import GameBenchmark, plan_for
        try:
            if self.forced is not None:
                self.progress.emit("Writing the forced game settings...")
                try:
                    rec = self.forced.apply()
                except (OSError, ValueError) as e:
                    self.forced.finish()                 # puts back whatever was written before the error
                    self.finished_plan.emit(f"failed: the game settings could not be forced ({e}); "
                                            "the game was not started")
                    return
                problem = settings_problem(rec)
                if problem:
                    self.forced.finish()
                    self.finished_plan.emit(f"failed: {problem}; your settings were put back and the game "
                                            "was not started")
                    return
            factory = self.benchmark_factory or GameBenchmark
            try:
                capture = factory(self.spec, self.run_dir, self.stop_event, self.progress.emit,
                                  sampler_factory=self.telemetry).run()
            finally:
                if self.forced is not None:          # the game is closed by now: check, then put the file back
                    self.forced.finish()
            if self.forced is not None:
                mark_unverified(capture, self.forced.record)
            if capture.status != "recorded":
                self.finished_plan.emit(f"{capture.status}: {capture.message}; nothing was stored")
                return
            self.capture_message = capture.message
            self.progress.emit(capture.message + "; storing the runs...")
            self.session_id = self.create_session(capture)
            runner = AutomationRunner(self.db, self.session_id, plan_for(capture, self.spec), self.output_root,
                                      on_progress=self.progress.emit)
            self.finished_plan.emit(runner.run())
        except Exception as e:
            logger.exception("Game benchmark failed")
            self.finished_plan.emit(f"failed: {e}")


class FrameCapturePanel(QGroupBox):
    def __init__(self, db, get_profile: Callable, get_environment: Callable[[], Optional[dict]],
                 on_session_changed: Callable[[], None] = lambda: None, data_dir: Optional[Path] = None,
                 lister: Callable[[], List[Tuple[str, int]]] = list_processes,
                 output_root: Optional[Path] = None, parent=None,
                 detector: Optional[Callable] = None, planner: Optional[Callable] = None,
                 downloader: Optional[Callable] = None, arch: Optional[str] = None,
                 platform_name: str = sys.platform, remote_check: Optional[Callable[[], bool]] = None,
                 games: Callable[[], List[dict]] = lambda: []):
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
        self.platform_name = platform_name
        self.remote_check = remote_check or is_remote_session
        self.arch = arch or provision.native_arch()
        self.detector = detector or (lambda: provision.find_local(self.arch, self.data_dir,
                                                                  self._setting_path()))
        self.planner = planner or (lambda: provision.plan_download(self.arch))
        self.downloader = downloader or (lambda choice: provision.download_presentmon(choice, self.data_dir))
        self.detected: Optional[provision.Found] = None
        self._task: Optional[TaskWorker] = None
        self.command_runner = run_commands
        self.worker: Optional[AutomationWorker] = None
        self._list_worker: Optional[ProcessListWorker] = None
        self.last_session_id: Optional[int] = None
        self.last_summary = None
        self.games = games
        self._after_tool: Optional[Callable[[], object]] = None
        self.benchmark_factory = None                    # tests swap in a fake GameBenchmark
        self.linux_which = None                          # tests swap in a fake shutil.which (Linux games)
        self.forced_factory = None                       # tests swap in a fake ForcedSettings
        self.settings_reader = None                      # tests swap in a fake read_for_dialog
        from app.ui.game_settings_dialog import GameSettingsDialog
        self.settings_dialog_factory = lambda *a: GameSettingsDialog(*a, parent=self)
        from app.ui.game_settings_dialog import DECLARED_NOTE, NOT_RECORDED
        self.declared_dialog_factory = lambda *a: GameSettingsDialog(*a, parent=self, note=DECLARED_NOTE,
                                                                     keep=NOT_RECORDED)

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
        self.telemetry_box = QCheckBox("Record telemetry")
        self.telemetry_box.setChecked(True)
        self.telemetry_box.setToolTip(TELEMETRY_TIP)
        row2.addWidget(self.telemetry_box)
        from app.ui.lhm_setup import LhmPreflight
        self.lhm = LhmPreflight(lambda text: self.status_label.setText(text), self, self.data_dir,
                                arch=self.arch, platform_name=self.platform_name)
        self.capture_btn = QPushButton("Capture frames...")
        self.capture_btn.clicked.connect(lambda: self.start_capture())
        row2.addWidget(self.capture_btn)
        self.stop_btn = QPushButton(f"Emergency stop ({STOP_SHORTCUT})")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("QPushButton:enabled { color: white; background: #b91c1c; }")
        self.stop_btn.clicked.connect(self.emergency_stop)
        row2.addWidget(self.stop_btn)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Game (Steam):"))
        self.game_combo = QComboBox()
        self.game_combo.setMinimumWidth(260)
        row3.addWidget(self.game_combo, 1)
        self.game_combo.currentIndexChanged.connect(lambda _i: self._show_game_settings_state())
        row3.addWidget(QLabel("Skip first (s):"))
        self.settle_spin = QSpinBox()
        self.settle_spin.setRange(0, 600)
        self.settle_spin.setValue(30)
        self.settle_spin.setToolTip("Loading screens and menus at the start are not measured.")
        row3.addWidget(self.settle_spin)
        self.game_btn = QPushButton("Benchmark game...")
        self.game_btn.setToolTip("Starts PresentMon (one UAC prompt), launches the game through Steam, finds its "
                                 "process and captures every run by itself.")
        self.game_btn.clicked.connect(lambda: self.benchmark_game())
        self.game_settings_btn = QPushButton("Game settings...")
        self.game_settings_btn.setToolTip("Graphics settings forced and checked for every benchmark of this game "
                                          "(resolution, preset, DLSS, Frame Generation, ray tracing).")
        self.game_settings_btn.clicked.connect(lambda: self.edit_game_settings())
        row3.addWidget(self.game_settings_btn)
        row3.addWidget(self.game_btn)
        layout.addLayout(row3)

        self.status_label = QLabel("Benchmark game... does everything for a Steam game. For any other program: "
                                   "start it, refresh the list, pick its process and press Capture frames....")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.show_path()
        self.refresh_games()
        self._show_game_settings_state()
        self.auto_detect()

    # ---- PresentMon path ------------------------------------------------------
    def is_windows(self) -> bool:
        return self.platform_name == "win32"

    def _setting_path(self) -> Optional[str]:
        settings, _ = load_settings(self.data_dir)
        value = settings.get(SETTING_KEY)
        return value if isinstance(value, str) else None

    def presentmon_path(self) -> Optional[str]:
        """The file chosen by the user, else the one found automatically (Intel install or our download)."""
        chosen = self._setting_path()
        if chosen or self.detected is None:
            return chosen
        return self.detected.path

    def show_path(self):
        if not self.is_windows():
            self.path_label.setText("PresentMon is Windows-only. On Linux the frame tool is MangoHud "
                                    "(capture with MangoHud is not implemented yet).")
            return
        path = self.presentmon_path()
        problem = check_presentmon_path(path)
        if problem:
            if self._setting_path():
                self.path_label.setText(f"PresentMon: {problem}")
            else:
                self.path_label.setText("PresentMon: not on this PC yet; Capture frames... offers to download it.")
            return
        source = "" if self._setting_path() else f" [{self.detected.source}]"
        emulated = ("  x64 build under ARM64 emulation (unverified)"
                    if self.detected is not None and self.detected.emulated else "")
        flags = "" if CAPTURE_FLAGS_VERIFIED else "  (capture flags not yet verified on this machine)"
        if self.remote_check():
            flags += "\n" + REMOTE_WARNING
        self.path_label.setText(f"PresentMon: {path}{source}{emulated}{flags}")

    # ---- automatic detection, download (Windows) and install (Linux) ------------
    def _run_task(self, fn, on_done) -> bool:
        if self._task is not None and self._task.isRunning():
            return False
        self._task = TaskWorker(fn)
        self._task.done.connect(on_done)
        self._task.start()
        return True

    def auto_detect(self):
        if self.is_windows():
            self._run_task(self.detector, self._on_detected)

    @Slot(object)
    def _on_detected(self, found):
        if isinstance(found, provision.Found) and found.path:
            self.detected = found
        self.show_path()

    def confirm_download(self, text: str) -> bool:
        return QMessageBox.question(self, "Download frame capture tool?", text,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def download_tool(self) -> bool:
        """Look again for PresentMon, else offer the download; on success the capture continues."""
        if not self.is_windows():
            self.install_mangohud()
            return False
        self.status_label.setText("PresentMon is not on this PC; looking again, then reading the official "
                                  "PresentMon release list (GitHub)...")

        def detect_or_plan():
            found = self.detector()
            if isinstance(found, provision.Found) and found.path:
                return found
            return self.planner()
        return self._run_task(detect_or_plan, self._on_planned)

    @Slot(object)
    def _on_planned(self, choice):
        if isinstance(choice, provision.Found):
            self.detected = choice
            self.show_path()
            self._continue_after_tool()
            return
        if not isinstance(choice, provision.AssetChoice) or choice.error:
            self.status_label.setText(f"Download not possible: {getattr(choice, 'error', choice)}")
            return
        text = (f"Frame capture needs PresentMon, which is not on this PC.\n\n"
                f"Download {choice.name} (PresentMon {choice.version}, {choice.size / 1048576:.1f} MB)\n"
                f"from {choice.url}\nSHA-256 {choice.sha256}\n\n"
                f"Kept only if the size, the SHA-256 published by GitHub and the Intel signature all match. "
                f"Saved under {provision.tools_dir(self.data_dir)}.")
        if choice.emulated:
            text += ("\n\nThis PC is ARM64. Intel publishes no native ARM64 build, so the x64 build runs "
                     "under Windows x64 emulation. This is not verified on ARM hardware.")
        text += "\n\nNo: nothing is downloaded and nothing is captured."
        if not self.confirm_download(text):
            self.status_label.setText("Download cancelled; nothing was captured.")
            return
        self.status_label.setText(f"Downloading {choice.name}...")
        self._run_task(lambda: self.downloader(choice), self._on_downloaded)

    @Slot(object)
    def _on_downloaded(self, found):
        if not isinstance(found, provision.Found) or found.error:
            self.status_label.setText(f"Download failed, nothing kept: {getattr(found, 'error', found)}")
            return
        self.detected = found
        self.show_path()
        self.status_label.setText(f"PresentMon {found.version} ready: " + "; ".join(found.notes or ["verified"]))
        self._continue_after_tool()

    def _continue_after_tool(self):
        after, self._after_tool = self._after_tool, None
        (after or self.start_capture)()

    def install_mangohud(self, os_release: Optional[dict] = None, which: Callable = shutil.which):
        from app.frames.linux_install import mangohud_install_plan
        plan = mangohud_install_plan(read_os_release() if os_release is None else os_release, which, self.arch)
        if plan.error:
            self.status_label.setText(f"MangoHud: {plan.error}")
            return
        if not plan.command:
            self.status_label.setText(plan.note or "MangoHud already installed.")
            return
        commands = ([plan.update_command] if plan.needs_update_first else []) + [plan.command]
        text = ("Install MangoHud with the system package manager. The system asks for your password "
                "(pkexec).\n\n" + "\n".join(subprocess.list2cmdline(c) for c in commands))
        if plan.note:
            text += "\n\n" + plan.note
        if not self.confirm_download(text):
            self.status_label.setText("Install cancelled.")
            return
        self.status_label.setText("Installing MangoHud...")
        self._run_task(lambda: self.command_runner(commands), self._on_installed)

    @Slot(object)
    def _on_installed(self, result):
        self.status_label.setText(f"MangoHud install: {result}")

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
        from app.ui.long_question import ask_yes_no
        return ask_yes_no(self, "Capture frames?", text)

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

    def start_capture(self, tools_ready: bool = False) -> bool:
        if self.is_running() or self.lhm.busy():
            return False
        if self.db is None or self.output_root is None:
            self.status_label.setText("No database available; nothing was captured.")
            return False
        if not self.is_windows():
            self.status_label.setText("Frame capture on Linux (MangoHud) is not implemented yet; nothing was captured.")
            return False
        target = self.process_combo.currentData()
        if not target:
            self.status_label.setText("Pick the game process first (Refresh list).")
            return False
        path = self.presentmon_path()
        problem = check_presentmon_path(path)
        if problem:
            if self._setting_path():
                self.status_label.setText(f"{problem}. Use 'Select PresentMon...' to choose it again.")
            elif not self._download_then(lambda: self.start_capture()):  # asks first; continues when ready
                self.status_label.setText("Still looking for PresentMon; press Capture frames... again in a moment.")
            return False
        if self.telemetry_box.isChecked() and not tools_ready:
            # LibreHardwareMonitor for CPU sensors: asks to download/start it only when it is not running.
            started = []
            self.lhm.ensure(lambda: started.append(self.start_capture(tools_ready=True)))
            return bool(started and started[0])      # empty: it asks or downloads first, the capture starts later
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
        if self.remote_check():
            text += "\n\n" + REMOTE_WARNING
        if not self.confirm(text):
            self.status_label.setText("Cancelled; nothing was captured.")
            return False
        sid = self._create_session(workload, profile)
        self.last_session_id, self.last_summary = sid, None
        plan = [PlanItem(workload, repetitions=profile.repetitions, warmup_runs=profile.warmup_runs)]
        self.worker = AutomationWorker(self.db, sid, plan, self.output_root, profile.cooldown_seconds, False,
                                       telemetry_sources.new_sampler if self.telemetry_box.isChecked() else None)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_plan.connect(self._on_finished)
        self._set_running(True)
        self.status_label.setText(f"Capturing in session #{sid}...")
        self.worker.start()
        return True

    def _download_then(self, after: Callable[[], object]) -> bool:
        self._after_tool = after
        if self.download_tool():
            return True
        self._after_tool = None
        return False

    # ---- whole game benchmark ---------------------------------------------------
    def refresh_games(self):
        """Installed Steam games (not Steam tools) from the latest discovery."""
        current = self.game_combo.currentData()
        self.game_combo.clear()
        try:
            games = [g for g in (self.games() or []) if g.get("installed") is True and g.get("kind") != "tool"]
        except Exception:
            logger.exception("Could not read the game list")
            games = []
        for game in sorted(games, key=lambda g: str(g.get("name", "")).lower()):
            self.game_combo.addItem(f"{game.get('name')} ({game.get('app_id')})", game)
        if current is not None:
            index = next((i for i in range(self.game_combo.count())
                          if (self.game_combo.itemData(i) or {}).get("app_id") == current.get("app_id")), -1)
            if index >= 0:
                self.game_combo.setCurrentIndex(index)

    def _show_game_settings_state(self):
        game = self.game_combo.currentData()
        ok = bool(game) and self.can_force(game.get("app_id"))
        self.game_settings_btn.setEnabled(bool(game) and not self.is_running())
        if not ok:
            self.game_settings_btn.setToolTip("Records the settings you set in this game (declared, not checked). "
                                              "Forcing settings is supported for: Cyberpunk 2077, Borderlands 3, Rise of the Tomb Raider, Red Dead Redemption 2, The Witcher 3, Counter-Strike 2, DOOM (2016).")

    def can_force(self, app_id) -> bool:
        """The game's settings can be forced here (Linux: through its Proton prefix; not the registry ones)."""
        from app.games.proton import supported
        from app.games.settings_file import adapter_for
        return supported(adapter_for(app_id), self.platform_name)

    def declared_settings_for(self, app_id) -> Dict[str, object]:
        from app.games.settings_file import DECLARED_KEY
        if self.can_force(app_id):
            return {}
        settings, _ = load_settings(self.data_dir)
        every = settings.get(DECLARED_KEY)
        chosen = every.get(str(app_id)) if isinstance(every, dict) else None
        return dict(chosen) if isinstance(chosen, dict) else {}

    def _save_choice(self, key: str, app_id: str, selection: dict) -> bool:
        settings, _ = load_settings(self.data_dir)
        every = settings.get(key)
        every = dict(every) if isinstance(every, dict) else {}
        every[app_id] = selection
        try:
            save_setting(self.data_dir, key, every)
        except (OSError, ValueError) as e:
            self.status_label.setText(f"Game settings not saved: {e}")
            return False
        return True

    def forced_settings_for(self, app_id) -> Dict[str, object]:
        from app.games.settings_file import saved_choice
        if not self.can_force(app_id):
            return {}
        settings, _ = load_settings(self.data_dir)
        return saved_choice(settings, app_id)

    def edit_game_settings(self) -> bool:
        return self._ask_game_settings() == "saved"

    def _ask_game_settings(self) -> str:
        """Show the settings window for the selected game: "saved", "cancelled" or "unavailable"."""
        from app.games.settings_file import SAVED_KEY, read_for_dialog
        from app.ui.game_settings_dialog import describe
        game = self.game_combo.currentData()
        if not game:
            return "unavailable"
        app_id = str(game.get("app_id"))
        from app.games.settings_file import DECLARED_KEY
        if not self.can_force(app_id):
            from app.ui.game_settings_dialog import DECLARED_CHOICES
            dialog = self.declared_dialog_factory(str(game.get("name")), DECLARED_CHOICES, {},
                                                  self.declared_settings_for(app_id))
            if not dialog.exec():
                return "cancelled"
            selection = dialog.selection()
            if not self._save_choice(DECLARED_KEY, app_id, selection):
                return "unavailable"
            self.status_label.setText(f"{game.get('name')}: declared settings: {describe(selection) or 'none'}")
            return "saved"
        try:
            choices, current = (self.settings_reader or read_for_dialog)(app_id, self.data_dir)
        except (OSError, ValueError) as e:
            self.status_label.setText(f"Game settings: {e}")
            return "unavailable"
        dialog = self.settings_dialog_factory(str(game.get("name")), choices, current,
                                              self.forced_settings_for(app_id))
        if not dialog.exec():
            return "cancelled"
        selection = dialog.selection()
        if not self._save_choice(SAVED_KEY, app_id, selection):
            return "unavailable"
        self.status_label.setText(f"{game.get('name')}: forced settings: {describe(selection) or 'none'}")
        return "saved"

    def select_game(self, app_id) -> bool:
        for i in range(self.game_combo.count()):
            if str((self.game_combo.itemData(i) or {}).get("app_id")) == str(app_id):
                self.game_combo.setCurrentIndex(i)
                return True
        return False

    def benchmark_game(self, tools_ready: bool = False, ask_settings: bool = True,
                       counts: Optional[Tuple[int, int]] = None) -> bool:
        """ask_settings False: the settings already chosen (Tests screen preset) are used without the settings
        window; counts (warm-up, measured) replace the profile's."""
        """PresentMon and (with telemetry) LibreHardwareMonitor first, then one confirmation; the rest is automatic."""
        if self.is_running() or self.lhm.busy():
            return False
        if self.db is None or self.output_root is None:
            self.status_label.setText("No database available; nothing was captured.")
            return False
        linux = self.platform_name.startswith("linux")
        if not self.is_windows() and not linux:
            self.status_label.setText("Game benchmarks run on Windows (PresentMon) and Linux (MangoHud).")
            return False
        game = self.game_combo.currentData()
        if not game:
            self.status_label.setText("No installed Steam game found (Steam tab, or rescan).")
            return False
        if linux:
            path = self.linux_ready()
            if path is None:
                return False
        else:
            path = self.presentmon_path()
            problem = check_presentmon_path(path)
            if problem:
                if self._setting_path():
                    self.status_label.setText(f"{problem}. Use 'Select PresentMon...' to choose it again.")
                elif not self._download_then(lambda: self.benchmark_game()):
                    self.status_label.setText("Still looking for PresentMon; press Benchmark game... again in a "
                                              "moment.")
                return False
        if self.is_windows() and self.telemetry_box.isChecked() and not tools_ready:
            started = []
            self.lhm.ensure(lambda: started.append(self.benchmark_game(tools_ready=True, ask_settings=ask_settings,
                                                                       counts=counts)))
            return bool(started and started[0])
        from app.frames.game_benchmark import GameBenchmarkSpec
        profile = self.get_profile()
        if counts is not None:
            profile = dataclasses.replace(profile, warmup_runs=counts[0], repetitions=counts[1])
        appids = [str(g.get("app_id")) for g in (self.games() or [])]
        spec = GameBenchmarkSpec(game_name=str(game.get("name")), app_id=str(game.get("app_id")),
                                 install_path=str(game.get("install_path")), discovered_appids=appids,
                                 presentmon_path=path, seconds=self.seconds_spin.value(),
                                 warmup_runs=profile.warmup_runs, repetitions=profile.repetitions,
                                 settle_seconds=self.settle_spin.value(),
                                 launch_args=game_launch_args(game.get("app_id")),
                                 start_keys=start_keys_for(game.get("app_id")),
                                 version=str(game["version"]) if game.get("version") else None)
        steps = menu_steps_for(spec.app_id)
        if steps:
            from app.frames.menu_steps import step_problems
            if step_problems(steps):
                logger.warning("Menu steps for %s not used: %s", spec.app_id, step_problems(steps))
            else:
                spec.steps = steps
                from data.game_start_keys import benchmark_seconds_for
                spec.benchmark_seconds = benchmark_seconds_for(spec.app_id)
        route = route_for(spec.app_id)
        if route:
            from app.frames.route import route_problems
            if route_problems(route):
                logger.warning("Route for %s not used: %s", spec.app_id, route_problems(route))
            else:
                spec.route = route
                # nothing before the route starts is measured
                spec.settle_seconds = max(spec.settle_seconds, int(route["loop_from"]))
        if linux and (spec.steps or spec.start_keys or spec.route):
            self.status_label.setText(f"{spec.game_name}'s benchmark needs menu steps, keys or a route, which work "
                                      "on Windows only so far; nothing was started.")
            return False
        fitted = None
        if spec.steps and spec.benchmark_seconds:
            from app.frames.game_benchmark import fit_to_benchmark
            fitted = fit_to_benchmark(spec, spec.benchmark_seconds)
        total = spec.settle_seconds + spec.seconds * spec.count
        from app.games.settings_file import adapter_for
        from app.ui.game_settings_dialog import describe
        if (ask_settings and self.can_force(spec.app_id)
                and self._ask_game_settings() == "cancelled"):
            self.status_label.setText("Cancelled; nothing was started.")
            return False
        # Linux: a Proton game's settings are read and written inside its Wine prefix (app/games/proton.py)
        forced_values = self.forced_settings_for(spec.app_id)
        declared = self.declared_settings_for(spec.app_id)
        spec.launch_args = game_launch_args(spec.app_id, forced_values)
        by_launch = getattr(adapter_for(spec.app_id), "base", None) == "launch"
        from app.frames.game_benchmark import is_remote_session
        remote = ("WARNING: this is a Remote Desktop session. The game draws to the remote screen (for example "
                  "32 Hz), so its FPS is not this PC's: the runs are recorded but stored as invalid, without a "
                  "score. Run it at the PC itself (or after disconnecting Remote Desktop) for real results.\n\n"
                  if is_remote_session() else "")
        text = (remote + f"Benchmark {spec.game_name} automatically:\n\n"
                f"1. PresentMon starts elevated: ONE UAC prompt for the whole benchmark. It records only the "
                f"programs (.exe) in the game's folder:\n   {spec.install_path}\n"
                f"2. {launch_url_text(spec)} is opened."
                + (" The arguments apply to this launch only; Steam may ask you to allow them."
                   if spec.launch_args else "") + "\n"
                f"3. The app waits about {spec.wait_seconds():.0f} s. The first {spec.settle_seconds} s of the "
                f"game (loading, menus) are not measured, then {spec.warmup_runs} warmup + {spec.repetitions} "
                f"measured runs of {spec.seconds} s each (profile {profile.name!r}), {total} s of game time.\n"
                + ("   The app goes through the game's menus to its built-in benchmark by itself: before each key "
                   "it reads the screen (Windows' own text recognition) and presses only when the expected word "
                   "is there, and only into the game. Measuring starts when the benchmark starts; the "
                   f"{spec.settle_seconds} s above count from then. If a word does not come, nothing is guessed: "
                   "the run stops and the screenshot is kept. Keep the game in front and use borderless window "
                   "or windowed mode (an exclusive full screen may read as black). These steps are not checked "
                   "on a real run yet.\n"
                   + (f"   The game's benchmark runs about {spec.benchmark_seconds} s, then shows its results; "
                      "every window ends before that."
                      + (f" Changed for this game: {fitted}." if fitted else "") + "\n"
                      if spec.benchmark_seconds else "")
                   if spec.steps else
                   f"   The app presses {', '.join(c for _, c in spec.start_keys)} in the game to reach the scene "
                   f"(only while the game is in front; these keys are not checked on a real run yet). Keep the "
                   f"game in front.\n" if spec.start_keys else
                   f"   Get to the scene you want to measure during the first {spec.settle_seconds} s and keep the "
                   f"game in front.\n") +
                f"4. Then the app closes the game: first like clicking its close button, by force after 30 s. "
                f"Unsaved progress in the game is lost. Only processes started inside the game's folder are "
                f"closed, never Steam or anything else. PresentMon then ends by itself and the runs are stored.\n\n"
                f"PresentMon: {path}")
        if forced_values and by_launch:
            text += ("\n\nForced game settings: " + describe(forced_values) + ".\nThey are passed as the launch "
                     "arguments above; nothing is written before the launch. After the run the app reads the "
                     "game's settings files to check them (runs are not valid if the game used other values; "
                     "values the files do not hold are recorded as not checked)."
                     + (" The game may save them when it closes, so its settings files are copied first and put "
                        "back afterwards." if getattr(adapter_for(spec.app_id).module, "RESTORE", False) else
                        " The app only reads that game's settings file; if the game saves the values, set them "
                        "back in its menu."))
        elif forced_values:
            text += ("\n\nForced game settings: " + describe(forced_values) + ".\nBefore the launch the app copies "
                     "the game's settings file into the run folder, writes these values and checks them; it does "
                     "not start the game if one cannot be written. After the run it checks them again (runs are "
                     "not valid if the game changed them) and puts your own file back.")
        elif declared:
            text += ("\n\nGame settings declared by you (not checked; this game's settings cannot be forced): "
                     + describe(declared) + ".")
        else:
            text += "\n\nNo game settings are forced (Game settings... to choose them); the game's own are used."
        if spec.route:
            from app.frames.route import describe_route
            text += ("\n\nRoute (the app plays the game while it measures): "
                     + describe_route(spec.route) + ".\nBefore that: " + spec.route.get("start", "load your save")
                     + ". Keys and mouse go only to the game while it is in front; held keys are released when "
                     "another window comes to the front. Do not touch the mouse or keyboard while it runs."
                     + ("" if spec.route.get("verified") else
                        "\nThis route is a draft not yet watched on a real run: watch it and stop the run if the "
                        "character does something unexpected."))
        if linux:
            text = self.linux_text(spec, profile, total, declared, forced_values, by_launch)
        if self.remote_check():
            text += "\n\n" + REMOTE_WARNING
        from app.environment import conditions as pc_now
        try:
            now = pc_now.timed_check()
        except Exception:                 # a check that fails must never stop a run
            logger.exception("Could not read the PC's conditions")
            now = None
        if now and now["items"]:
            text += "\n\n" + pc_now.text(now["items"])
        if not self.confirm(text):
            self.status_label.setText("Cancelled; nothing was started.")
            return False
        environment = self.get_environment()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.output_root / f"game_{spec.app_id}_{stamp}"

        forced = None
        if forced_values:
            from app.games.settings_file import ForcedSettings
            forced = (self.forced_factory or ForcedSettings)(spec.app_id, forced_values, run_dir, self.data_dir)

        def create_session(capture) -> int:
            return self._create_game_session(spec, profile, environment, capture, forced, declared, now)
        self.worker = GameBenchmarkWorker(spec, run_dir, create_session, self.db, self.output_root,
                                          telemetry_sources.new_sampler if self.telemetry_box.isChecked() else None,
                                          self.benchmark_factory, forced)
        self.last_session_id, self.last_summary = None, None
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_plan.connect(self._on_finished)
        self._set_running(True)
        self.status_label.setText(f"Benchmarking {spec.game_name}...")
        self.worker.start()
        return True

    def linux_ready(self) -> Optional[str]:
        """MangoHud's path when a Linux game run can start, else None (the status says why). The first time, the
        owner is asked once whether the app may start Steam itself with MangoHud (app/frames/mangohud_run.py)."""
        from app.frames import mangohud_run as mh
        which = self.linux_which or shutil.which
        mangohud = which("mangohud")
        if not mangohud:
            self.status_label.setText("Game benchmarks on Linux measure with MangoHud, which is not installed. "
                                      "Install it, then press Benchmark game... again.")
            self.install_mangohud(which=which)
            return None
        if not load_settings(self.data_dir)[0].get(mh.CONSENT_KEY):
            if not self.confirm(LINUX_CONSENT_TEXT):
                self.status_label.setText("Not allowed; nothing was started. Game benchmarks on Linux need the app "
                                          "to start Steam with MangoHud.")
                return None
            try:
                save_setting(self.data_dir, mh.CONSENT_KEY, True)
            except (OSError, ValueError) as e:
                logger.warning("Could not save the MangoHud choice: %s", e)
        return mangohud

    def linux_text(self, spec, profile, total: int, declared, forced_values=None, by_launch=False) -> str:
        from app.ui.game_settings_dialog import describe
        return (f"Benchmark {spec.game_name} automatically (Linux, MangoHud):\n\n"
                f"1. The app uses the Steam it started with MangoHud (MANGOHUD=1 and the app's own MangoHud "
                f"settings; nothing in Steam's settings is changed): if Steam is closed it starts it; if Steam is "
                f"open without MangoHud it asks it to exit first (steam -shutdown). Then it asks Steam to "
                f"launch the game (steam -applaunch {spec.app_id}"
                + (" " + " ".join(spec.launch_args) if spec.launch_args else "") + ").\n"
                f"2. MangoHud logs every frame for about {spec.wait_seconds():.0f} s from the game's first frames. "
                f"The first {spec.settle_seconds} s (loading, menus) are not measured, then {spec.warmup_runs} "
                f"warmup + {spec.repetitions} measured runs of {spec.seconds} s each (profile {profile.name!r}), "
                f"{total} s of game time. Get to the scene you want to measure during the first "
                f"{spec.settle_seconds} s.\n"
                f"3. Then the app closes the game: only processes started from the game's folder (for Proton: the "
                f"ones whose command line names it), politely first, by force after 30 s. Unsaved progress is "
                f"lost. Steam stays open for the next game.\n\n"
                "MangoHud works with Vulkan games: Proton games and native Vulkan ones. A native OpenGL game writes "
                "no log (the run then says so). Not yet checked on a real Linux PC.\n\n"
                + (("Forced game settings: " + describe(forced_values) + ".\n"
                    + ("They are passed as the launch arguments above; after the run the app reads the game's "
                       "settings files to check them." if by_launch else
                       "Before the launch the app copies the game's settings file (for a Proton game: inside its "
                       "own Wine prefix, steamapps/compatdata/" + str(spec.app_id) + "/pfx) into the run folder, "
                       "writes these values and checks them; after the run it checks them again and puts your own "
                       "file back. Not yet checked on a real Linux PC.")) if forced_values else
                   "Game settings declared by you (not checked): " + describe(declared) + "." if declared else
                   "No game settings are forced (Game settings... to choose them); the game's own are used."))

    def _create_game_session(self, spec, profile, environment, capture, forced=None, declared=None,
                             conditions=None) -> int:
        from app.games.settings_file import declared_record
        from app.manifest import build_manifest, git_commit
        name = f"{spec.game_name} {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        process = capture.process[0] if capture.process else "?"
        mangohud = os.path.basename(spec.presentmon_path) == "mangohud"       # Linux
        tool = "MangoHud" if mangohud else "PresentMon"
        sid = self.db.create_session(name, f"Game benchmark of {spec.game_name} (Steam {spec.app_id}), "
                                           f"{tool} capture of {process}", is_manual=False)
        settings = dict(profile.settings(), capture_seconds=spec.seconds, settle_seconds=spec.settle_seconds,
                        steam_app_id=spec.app_id, game_process=process,
                        game_settings=forced.record.to_dict() if forced is not None else
                        declared_record(declared) if declared else None)
        version = None
        try:
            from app.benchmark_manager import get_pe_version
            version = None if mangohud else get_pe_version(spec.presentmon_path)
        except Exception:
            pass
        manifest = build_manifest(
            {"id": sid, "name": name, "is_test": False, "is_manual": False},
            run_settings=settings, environment=environment,
            benchmarks_used=[{"name": tool.lower(), "display_name": tool, "version": version,
                              "executable_path": spec.presentmon_path, "detection_method": "found or downloaded"}],
            commit=git_commit(), conditions=conditions)
        try:
            self.db.save_session_manifest(sid, manifest)
        except Exception:
            logger.exception("Could not save session manifest")
        return sid

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
        if self._task is not None and self._task.isRunning():
            self._task.wait(60000)          # a download or signature check; never cut a file write short

    def _set_running(self, running: bool):
        for w in (self.capture_btn, self.select_btn, self.refresh_btn, self.process_combo, self.seconds_spin,
                  self.telemetry_box, self.game_combo, self.settle_spin, self.game_btn, self.game_settings_btn):
            w.setEnabled(not running)
        if not running:
            self._show_game_settings_state()
        self.stop_btn.setEnabled(running)

    @Slot(str)
    def _on_progress(self, message: str):
        self.status_label.setText(message)

    @Slot(object)
    def _on_finished(self, summary):
        self._set_running(False)
        self.last_summary = summary
        if isinstance(self.worker, GameBenchmarkWorker):
            self.last_session_id = self.worker.session_id
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
            if getattr(self.worker, "capture_message", ""):
                text += f". {self.worker.capture_message}"
        else:
            text = (f"Session #{self.last_session_id}: {summary}" if self.last_session_id is not None
                    else str(summary))
        self.status_label.setText(text)
        self.on_session_changed()

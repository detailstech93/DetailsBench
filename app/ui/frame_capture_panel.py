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

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout,
)

from app.automation.runner import PlanItem, RunSummary
from app.frames import provision
from app.frames.presentmon import (
    CAPTURE_FLAGS_VERIFIED, SETTING_KEY, FrameCaptureWorkload, check_presentmon_path,
)
from app.paths import get_data_dir, load_settings, save_setting
from app.telemetry import sources as telemetry_sources
from app.ui.automation_panel import STOP_SHORTCUT, TELEMETRY_TIP, AutomationWorker

logger = logging.getLogger(__name__)


REMOTE_WARNING = ("WARNING: this is a Remote Desktop session. PresentMon recorded no frames in one on the "
                  "test machine; run the capture at the computer's own screen.")


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


class GameBenchmarkWorker(QThread):
    """Records one game benchmark, then stores its windows through AutomationRunner (no GUI access)."""
    progress = Signal(str)
    finished_plan = Signal(object)              # RunSummary, or an error str

    def __init__(self, spec, run_dir: Path, create_session: Callable, db, output_root: Path,
                 telemetry: Optional[Callable] = None, benchmark_factory: Optional[Callable] = None):
        super().__init__()
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

    def stop(self):
        self.stop_event.set()

    def run(self):
        from app.automation.runner import AutomationRunner
        from app.frames.game_benchmark import GameBenchmark, plan_for
        try:
            factory = self.benchmark_factory or GameBenchmark
            capture = factory(self.spec, self.run_dir, self.stop_event, self.progress.emit,
                              sampler_factory=self.telemetry).run()
            if capture.status != "recorded":
                self.finished_plan.emit(f"{capture.status}: {capture.message}; nothing was stored")
                return
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
        row3.addWidget(self.game_btn)
        layout.addLayout(row3)

        self.status_label = QLabel("Benchmark game... does everything for a Steam game. For any other program: "
                                   "start it, refresh the list, pick its process and press Capture frames....")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.show_path()
        self.refresh_games()
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

    def select_game(self, app_id) -> bool:
        for i in range(self.game_combo.count()):
            if str((self.game_combo.itemData(i) or {}).get("app_id")) == str(app_id):
                self.game_combo.setCurrentIndex(i)
                return True
        return False

    def benchmark_game(self, tools_ready: bool = False) -> bool:
        """PresentMon and (with telemetry) LibreHardwareMonitor first, then one confirmation; the rest is automatic."""
        if self.is_running() or self.lhm.busy():
            return False
        if self.db is None or self.output_root is None:
            self.status_label.setText("No database available; nothing was captured.")
            return False
        if not self.is_windows():
            self.status_label.setText("Game benchmarks capture frames with PresentMon, which is Windows-only; "
                                      "MangoHud capture on Linux is not implemented yet.")
            return False
        game = self.game_combo.currentData()
        if not game:
            self.status_label.setText("No installed Steam game found (Steam tab, or rescan).")
            return False
        path = self.presentmon_path()
        problem = check_presentmon_path(path)
        if problem:
            if self._setting_path():
                self.status_label.setText(f"{problem}. Use 'Select PresentMon...' to choose it again.")
            elif not self._download_then(lambda: self.benchmark_game()):
                self.status_label.setText("Still looking for PresentMon; press Benchmark game... again in a moment.")
            return False
        if self.telemetry_box.isChecked() and not tools_ready:
            started = []
            self.lhm.ensure(lambda: started.append(self.benchmark_game(tools_ready=True)))
            return bool(started and started[0])
        from app.frames.game_benchmark import GameBenchmarkSpec
        from app.frames.presentmon import capture_all_args
        profile = self.get_profile()
        appids = [str(g.get("app_id")) for g in (self.games() or [])]
        spec = GameBenchmarkSpec(game_name=str(game.get("name")), app_id=str(game.get("app_id")),
                                 install_path=str(game.get("install_path")), discovered_appids=appids,
                                 presentmon_path=path, seconds=self.seconds_spin.value(),
                                 warmup_runs=profile.warmup_runs, repetitions=profile.repetitions,
                                 settle_seconds=self.settle_spin.value())
        total = spec.settle_seconds + spec.seconds * spec.count
        text = (f"Benchmark {spec.game_name} automatically:\n\n"
                f"1. PresentMon starts elevated: ONE UAC prompt for the whole benchmark. It records every "
                f"process until enough of the game is recorded (at most {spec.max_record_seconds()} s).\n"
                f"2. steam://rungameid/{spec.app_id} is opened; the game's process is found in\n"
                f"   {spec.install_path}\n"
                f"3. The first {spec.settle_seconds} s of the game (loading, menus) are skipped, then "
                f"{spec.warmup_runs} warmup + {spec.repetitions} measured windows of {spec.seconds} s each "
                f"are cut from the recording (profile {profile.name!r}); about {total} s of game time.\n\n"
                f"Get to the scene you want to measure during the first {spec.settle_seconds} s and keep the "
                f"game in front. The app never closes the game; close it yourself afterwards.\n\n"
                f"Command (runs elevated):\n"
                f"{subprocess.list2cmdline([path] + capture_all_args('<run folder>' + os.sep + 'presentmon.csv', spec.max_record_seconds()))}")
        if self.remote_check():
            text += "\n\n" + REMOTE_WARNING
        if not self.confirm(text):
            self.status_label.setText("Cancelled; nothing was started.")
            return False
        environment = self.get_environment()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.output_root / f"game_{spec.app_id}_{stamp}"

        def create_session(capture) -> int:
            return self._create_game_session(spec, profile, environment, capture)
        self.worker = GameBenchmarkWorker(spec, run_dir, create_session, self.db, self.output_root,
                                          telemetry_sources.new_sampler if self.telemetry_box.isChecked() else None,
                                          self.benchmark_factory)
        self.last_session_id, self.last_summary = None, None
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_plan.connect(self._on_finished)
        self._set_running(True)
        self.status_label.setText(f"Benchmarking {spec.game_name}...")
        self.worker.start()
        return True

    def _create_game_session(self, spec, profile, environment, capture) -> int:
        from app.manifest import build_manifest, git_commit
        name = f"{spec.game_name} {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        process = capture.process[0] if capture.process else "?"
        sid = self.db.create_session(name, f"Game benchmark of {spec.game_name} (Steam {spec.app_id}), "
                                           f"PresentMon capture of {process}", is_manual=False)
        settings = dict(profile.settings(), capture_seconds=spec.seconds, settle_seconds=spec.settle_seconds,
                        steam_app_id=spec.app_id, game_process=process)
        version = None
        try:
            from app.benchmark_manager import get_pe_version
            version = get_pe_version(spec.presentmon_path)
        except Exception:
            pass
        manifest = build_manifest(
            {"id": sid, "name": name, "is_test": False, "is_manual": False},
            run_settings=settings, environment=environment,
            benchmarks_used=[{"name": "presentmon", "display_name": "PresentMon", "version": version,
                              "executable_path": spec.presentmon_path, "detection_method": "found or downloaded"}],
            commit=git_commit())
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
                  self.telemetry_box, self.game_combo, self.settle_spin, self.game_btn):
            w.setEnabled(not running)
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
        else:
            text = (f"Session #{self.last_session_id}: {summary}" if self.last_session_id is not None
                    else str(summary))
        self.status_label.setText(text)
        self.on_session_changed()

"""Main window and background scanning worker for PC Benchmark Manager."""

import os
import sys
import platform
import subprocess
import webbrowser
import logging
from typing import Optional, List, Dict, Any

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSplitter, QTextEdit,
    QMessageBox, QListWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QCheckBox, QLineEdit, QSpinBox,
    QDialog, QDialogButtonBox, QFormLayout, QComboBox, QTabWidget,
    QProgressBar, QFrame, QStackedWidget, QFileDialog
)
from PySide6.QtCore import Qt, Signal, Slot, QThread
from PySide6.QtGui import QFont, QColor

from app.benchmark_manager import (
    BenchmarkManager, BenchmarkExecutor, BenchmarkInfo,
    BenchmarkStatus, BenchmarkType, launch_assisted_benchmark
)
from app.discovery.result import DiscoveryResult
from app.steam.launch import launch_and_confirm
from app.discovery.runner import discover_installed
from app.environment.adapters import select_adapter
from app.environment.profile import DETECTED, UNVERIFIED, EnvironmentProfile
from data.benchmark_definitions import (
    DEFINITIONS_BY_ID,
    get_default_page_version,
    get_all_page_urls,
)

logger = logging.getLogger(__name__)


class BenchmarkWorker(QThread):
    """Background worker thread for running BenchmarkManager.scan().

    Moved out of methods into its own class as required.
    Emits Qt signals for thread-safe UI updates.
    """
    scan_started = Signal()
    scan_finished = Signal(list)
    scan_error = Signal(str)
    progress = Signal(str)
    profile_ready = Signal(object)   # EnvironmentProfile
    discovery_ready = Signal(object) # DiscoveryResult

    def __init__(self, benchmark_manager: BenchmarkManager):
        super().__init__()
        self.benchmark_manager = benchmark_manager
        self._is_running = False

    def run(self):
        """Execute the benchmark scan in the background thread."""
        self._is_running = True
        self.scan_started.emit()
        try:
            self.progress.emit("Building environment profile...")
            profile = None
            try:
                profile = select_adapter().build_profile()
                self.profile_ready.emit(profile)
            except Exception:
                logger.exception("Environment profile failed; scanning without it")
            self.progress.emit("Looking for known tools and games...")
            try:
                self.discovery_ready.emit(discover_installed())
            except Exception:
                logger.exception("Installed-software discovery failed")
            self.progress.emit("Scanning system capabilities and benchmarks...")
            benchmarks = self.benchmark_manager.scan(profile)
            self.scan_finished.emit(benchmarks)
        except Exception as e:
            logger.exception("Background benchmark scan failed")
            self.scan_error.emit(str(e))
        finally:
            self._is_running = False

    def is_running(self) -> bool:
        """Check if worker is actively running."""
        return self._is_running


class LaunchWorker(QThread):
    """Background worker thread for launching benchmarks.

    Runs the entire launch pipeline (subprocess.Popen / os.startfile,
    UAC wait, and process polling) off the GUI thread.  Emits a single
    ``finished`` signal with the result dict.

    Attributes
    ----------
    finished : Signal(dict)
        Emitted with {status, message, elevation_used} when the launch
        attempt completes (success, failure, cancellation, or timeout).
    progress : Signal(str)
        Emitted with human-readable progress messages.
    """
    finished = Signal(dict)
    progress = Signal(str)

    def __init__(self, benchmark_info: BenchmarkInfo, timeout: int = 5):
        super().__init__()
        self.benchmark_info = benchmark_info
        self.timeout = timeout

    def run(self):
        """Execute the launch in the background thread."""
        try:
            self.progress.emit(f"Launching {self.benchmark_info.display_name}...")
            result = launch_assisted_benchmark(
                self.benchmark_info, confirm=True, timeout=self.timeout
            )
        except Exception as e:
            logger.exception("Launch worker failed for %s", self.benchmark_info.display_name)
            result = {
                'status': 'error',
                'message': f"Failed to launch {self.benchmark_info.display_name}: {e}",
                'elevation_used': False,
            }
        self.finished.emit(result)


class _WebLaunchWorker(QThread):
    """Lightweight worker for spawning a browser to a web benchmark URL.

    Runs subprocess.Popen / webbrowser.open off the GUI thread.

    Results are defined as:
     - launch call returned without exception  -> status "launched",
       message "Launch command accepted; page load not verified"
     - launch call raised an exception         -> status "error" with exception text
     - user cancelled before launch            -> status "cancelled"

    A process snapshot is taken only as a diagnostic note in the log;
    it is never used as proof of a successful launch because browsers
    spawn child processes (renderers) and a process-count increase does
    not prove our launch worked.
    """
    finished = Signal(dict)
    progress = Signal(str)

    def __init__(
        self, bm: BenchmarkInfo, url: str, version_str: str,
        browser_path: Optional[str] = None, browser_name: Optional[str] = None,
    ):
        super().__init__()
        self.bm = bm
        self.url = url
        self.version_str = version_str
        self.browser_path = browser_path
        self.browser_name = browser_name
        self._cancelled = False

    def cancel(self):
        """Signal that the user cancelled before the launch call."""
        self._cancelled = True

    def run(self):
        if self._cancelled:
            self.finished.emit({
                'status': 'cancelled',
                'message': f"Launch of {self.bm.display_name} was cancelled by user.",
                'elevation_used': False,
            })
            return

        try:
            self.progress.emit(f"Launching {self.bm.display_name}...")

            # Diagnostic-only process snapshot (never used as proof, and a
            # failure here must never change the launch result).
            from app.benchmark_manager import _get_process_snapshot, _has_new_process
            before = None
            try:
                before = _get_process_snapshot()
            except Exception as exc:
                logger.debug("[diagnostic] pre-launch snapshot failed: %s", exc)

            if self.browser_path:
                subprocess.Popen([self.browser_path, self.url])
            elif not webbrowser.open(self.url):
                self.finished.emit({
                    'status': 'error',
                    'message': (
                        f"Could not launch {self.bm.display_name}: "
                        f"no browser could be opened for {self.url}"
                    ),
                    'elevation_used': False,
                })
                return

            try:
                browser_exe = os.path.basename(self.browser_path) if self.browser_path else None
                if before is not None and browser_exe:
                    after = _get_process_snapshot()
                    if _has_new_process(before, after, browser_exe):
                        logger.debug(
                            "[diagnostic] new %s process seen after launch of %s",
                            browser_exe, self.bm.display_name,
                        )
                    else:
                        logger.debug(
                            "[diagnostic] no new %s process seen after launch of %s "
                            "(browser may have reused an existing process)",
                            browser_exe, self.bm.display_name,
                        )
            except Exception as exc:
                logger.debug("[diagnostic] post-launch snapshot check failed: %s", exc)

            self.finished.emit({
                'status': 'launched',
                'message': (
                    f"{self.bm.display_name} v{self.version_str} "
                    f"Launch command accepted; page load not verified: {self.url}"
                ),
                'elevation_used': False,
            })
        except Exception as e:
            self.finished.emit({
                'status': 'error',
                'message': f"Could not launch {self.bm.display_name}: {e}",
                'elevation_used': False,
            })


def _status_label(bm: BenchmarkInfo) -> str:
    """Text shown for a benchmark's status in the Library table."""
    if bm.status == BenchmarkStatus.SCAN_ERROR:
        return f"could not check: {bm.reason or 'unknown error'}"
    if bm.status == BenchmarkStatus.READY_MANUAL:
        return "assisted/manual"
    if bm.status == BenchmarkStatus.UNSUPPORTED:
        return "Unsupported on this system"
    return bm.status.value


class SteamLaunchWorker(QThread):
    """Launch one discovered Steam game off the GUI thread and confirm it.

    Emits finished({status, message, url}). stop() ends the confirmation
    wait early (reported as "unconfirmed"); processes are never killed.
    """
    finished = Signal(dict)

    def __init__(self, game: dict, discovered_appids: List[str], timeout: float = 60.0):
        super().__init__()
        self.game = game
        self.discovered_appids = discovered_appids
        self.timeout = timeout
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import time
        from app.benchmark_manager import _get_process_snapshot
        try:
            result = launch_and_confirm(
                self.game["app_id"], self.game["install_path"], self.discovered_appids,
                snapshot=_get_process_snapshot, timeout=self.timeout, poll=1.0,
                clock=lambda: float("inf") if self._stop else time.monotonic(),
            )
        except Exception as exc:
            logger.exception("Steam launch worker failed")
            result = {"status": "error", "message": str(exc), "url": None}
        self.finished.emit(result)


ENV_FIELD_LABELS = [
    ("os_name", "OS"), ("os_version", "OS version"), ("os_build", "OS build"),
    ("native_arch", "Native architecture"), ("process_arch", "App architecture"),
    ("x64_emulated", "x64 runs emulated"), ("app_is_elevated", "App runs as administrator"),
    ("is_wsl", "Inside WSL"), ("is_virtual_machine", "Virtual machine"),
    ("cpu", "CPU"), ("ram_gb", "Memory (GB)"), ("gpu", "GPUs"),
    ("directx", "DirectX"), ("vulkan", "Vulkan"), ("opengl", "OpenGL"),
    ("display_server", "Display server"),
]


def _fmt(value: Optional[float], digits: int = 2) -> str:
    """Statistics cell text; "-" when not computable (e.g. stdev of one run)."""
    return "-" if value is None else f"{value:,.{digits}f}"


def _format_env_value(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


class MainWindow(QMainWindow):
    """Main application window for PC Benchmark Manager."""
    
    def __init__(self, db: Any, system_info: dict):
        super().__init__()
        
        self.db = db
        self.system_info = system_info or {}
        self.current_session_id: Optional[int] = None
        self.environment_profile: Optional[EnvironmentProfile] = None
        self.environment_snapshot_id: Optional[int] = None
        self.discovery: Optional[DiscoveryResult] = None
        self._steam_worker: Optional[SteamLaunchWorker] = None
        
        # One shared BenchmarkManager instance attribute
        self.benchmark_manager = BenchmarkManager()
        self.executor = BenchmarkExecutor(self.db)
        
        # Keep background scan worker referenced as an attribute
        self._scan_worker: Optional[BenchmarkWorker] = None

        # Keep launch worker referenced; prevents concurrent launches
        self._launch_worker: Optional[QThread] = None
        
        # Window properties
        self.setWindowTitle("PC Benchmark Manager")
        self.setMinimumSize(1080, 720)
        
        # Build UI layout
        self._init_ui()
        
        # Automatically trigger initial background scan on startup
        self.start_scan()
    
    def _init_ui(self):
        """Initialize UI layout and components."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)
        
        # --- Top Header Bar ---
        header_frame = QFrame()
        header_frame.setFrameShape(QFrame.StyledPanel)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(10, 8, 10, 8)
        
        title_label = QLabel("PC Benchmark Manager")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        
        self.status_label = QLabel("Initializing...")
        self.status_label.setStyleSheet("color: #555555; font-style: italic;")
        
        self.rescan_btn = QPushButton("Rescan Benchmarks")
        self.rescan_btn.setFixedWidth(160)
        self.rescan_btn.clicked.connect(self.start_scan)
        
        header_layout.addWidget(title_label)
        header_layout.addSpacing(20)
        header_layout.addWidget(self.status_label, 1)
        header_layout.addWidget(self.rescan_btn)
        
        main_layout.addWidget(header_frame)
        
        # --- Main Tabbed Views ---
        self.tabs = QTabWidget()
        
        # 1. Dashboard View
        self.dashboard_tab = self._create_dashboard_tab()
        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        
        # 2. Benchmark Library View
        self.library_tab = self._create_library_tab()
        self.tabs.addTab(self.library_tab, "Benchmark Library")
        
        # 3. Run Benchmarks View
        self.run_tab = self._create_run_tab()
        self.tabs.addTab(self.run_tab, "Run Benchmarks")
        
        # 4. Session History View
        self.history_tab = self._create_history_tab()
        self.tabs.addTab(self.history_tab, "Session History")

        # 5. Compare Results View
        self.compare_tab = self._create_compare_tab()
        self.tabs.addTab(self.compare_tab, "Compare Results")

        # 6. Steam View
        self.steam_tab = self._create_steam_tab()
        self.tabs.addTab(self.steam_tab, "Steam")
        
        main_layout.addWidget(self.tabs, 1)
    
    def _create_dashboard_tab(self) -> QWidget:
        """Create Dashboard tab with system specs."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        group = QGroupBox("System Specifications")
        group_layout = QFormLayout(group)
        group_layout.setLabelAlignment(Qt.AlignRight)
        
        cpu_val = str(self.system_info.get('cpu_brand', 'Unknown'))
        cores_val = f"{self.system_info.get('core_count', 'N/A')} cores"
        ram_val = f"{self.system_info.get('ram_size_gb', 'N/A')} GB"
        gpu_val = str(self.system_info.get('gpu_name', 'Unknown'))
        os_val = str(self.system_info.get('operating_system', platform.platform()))
        
        group_layout.addRow("CPU:", QLabel(cpu_val))
        group_layout.addRow("Cores:", QLabel(cores_val))
        group_layout.addRow("Memory (RAM):", QLabel(ram_val))
        group_layout.addRow("GPU:", QLabel(gpu_val))
        group_layout.addRow("Operating System:", QLabel(os_val))
        
        layout.addWidget(group)

        env_group = QGroupBox("Environment Profile")
        env_group.setToolTip("Each value is marked detected, unverified or unavailable. "
                             "Hover a value to see where it came from.")
        self.env_form = QFormLayout(env_group)
        self.env_form.setLabelAlignment(Qt.AlignRight)
        self.env_value_labels: Dict[str, QLabel] = {}
        for name, title in ENV_FIELD_LABELS:
            label = QLabel("Scanning...")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.env_value_labels[name] = label
            self.env_form.addRow(f"{title}:", label)
        layout.addWidget(env_group)
        layout.addStretch(1)
        return tab

    def refresh_environment_view(self):
        """Show the current EnvironmentProfile on the Dashboard."""
        profile = self.environment_profile
        if profile is None:
            return
        for name, label in self.env_value_labels.items():
            f = getattr(profile, name)
            label.setText(f"{_format_env_value(f.value)}   [{f.status}]")
            label.setToolTip(f"source: {f.source or 'n/a'}")
            color = {DETECTED: "", UNVERIFIED: "color: #B26A00;"}.get(f.status, "color: #757575;")
            label.setStyleSheet(color)
    
    def _create_library_tab(self) -> QWidget:
        """Create Benchmark Library tab showing all detected benchmarks."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        info_label = QLabel("All supported benchmarks and their detection status on this machine:")
        layout.addWidget(info_label)
        
        self.library_table = QTableWidget()
        self.library_table.setColumnCount(7)
        self.library_table.setHorizontalHeaderLabels([
            "Benchmark", "Type", "Status", "Version", "Arch", "Detection Method", "Executable / Target"
        ])
        header = self.library_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        
        layout.addWidget(self.library_table, 1)

        tools_group = QGroupBox("Detected tools and games")
        tools_group.setToolTip("Only known benchmark tools, browsers, launchers and games are listed; "
                               "other installed software is never stored or shown.")
        tools_layout = QVBoxLayout(tools_group)
        self.stores_label = QLabel("Scanning...")
        tools_layout.addWidget(self.stores_label)
        self.tools_table = QTableWidget()
        self.tools_table.setColumnCount(5)
        self.tools_table.setHorizontalHeaderLabels(["Kind", "Name", "Version", "Location", "Source"])
        theader = self.tools_table.horizontalHeader()
        for col in range(4):
            theader.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        theader.setSectionResizeMode(4, QHeaderView.Stretch)
        tools_layout.addWidget(self.tools_table)
        layout.addWidget(tools_group, 1)
        return tab

    def refresh_tools_view(self):
        """Show matched software, games and store status in the Library tab."""
        result = self.discovery
        if result is None:
            return
        stores = ", ".join(f"{name}: {info.get('status')}" for name, info in sorted(result.stores.items()))
        diag = f"  |  problems: {'; '.join(result.diagnostics)}" if result.diagnostics else ""
        self.stores_label.setText(f"Stores / clients: {stores or 'none checked'}{diag}")
        rows = [(s["kind"], s["name"], s.get("version"), s.get("path"), s.get("source"))
                for s in result.software]
        rows += [(f"game ({g['store']})", g["name"], g.get("version"), g.get("install_path"), g["store"])
                 for g in result.games]
        self.tools_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self.tools_table.setItem(r, c, QTableWidgetItem(value if value else "-"))
    
    def _create_compare_tab(self) -> QWidget:
        """Pick two sessions and compare every benchmark, with reasons when not comparable."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.compare_a = QComboBox()
        self.compare_b = QComboBox()
        self.compare_reload_btn = QPushButton("Reload sessions")
        self.compare_reload_btn.clicked.connect(self.reload_compare_sessions)
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.clicked.connect(self.run_comparison)
        for label, widget in (("A (baseline):", self.compare_a), ("B:", self.compare_b)):
            row.addWidget(QLabel(label))
            row.addWidget(widget, 1)
        row.addWidget(self.compare_reload_btn)
        row.addWidget(self.compare_btn)
        layout.addLayout(row)
        self.compare_table = QTableWidget()
        self.compare_table.setColumnCount(7)
        self.compare_table.setHorizontalHeaderLabels(
            ["Benchmark", "Mean A", "Mean B", "Delta", "Delta %", "Status", "Why"])
        cheader = self.compare_table.horizontalHeader()
        for col in range(6):
            cheader.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        cheader.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.compare_table, 1)
        self.compare_note = QLabel("Delta % is relative to A. Rows marked not comparable keep their numbers "
                                   "for reference only.")
        self.compare_note.setWordWrap(True)
        layout.addWidget(self.compare_note)
        return tab

    def reload_compare_sessions(self):
        if not self.db:
            return
        try:
            include_test = self.show_test_data_cb.isChecked()
            sessions = self.db.search_sessions(include_test=include_test)
        except Exception as e:
            logger.debug(f"Could not load sessions for comparison: {e}")
            return
        for combo in (self.compare_a, self.compare_b):
            current = combo.currentData()
            combo.clear()
            for s in sessions:
                flag = " [TEST]" if s["is_test"] else ""
                combo.addItem(f"#{s['id']} {s['name']} ({s['created_at'][:10]}){flag}", s)
            index = next((i for i, s in enumerate(sessions) if current and s["id"] == current["id"]), -1)
            if index >= 0:
                combo.setCurrentIndex(index)
        if len(sessions) >= 2 and self.compare_a.currentIndex() == self.compare_b.currentIndex():
            self.compare_a.setCurrentIndex(1)   # default: older session as baseline A, newest as B

    def run_comparison(self):
        from app.compare import compare_sessions
        a, b = self.compare_a.currentData(), self.compare_b.currentData()
        if not self.db or not a or not b:
            self.compare_note.setText("Pick two sessions first.")
            return
        rows = compare_sessions(
            self.db.get_runs(a["id"], include_test=True), self.db.get_runs(b["id"], include_test=True),
            self.db.get_session_manifest(a["id"]), self.db.get_session_manifest(b["id"]),
            a["is_test"], b["is_test"])
        colors = {"ok": "#2E7D32", "warning": "#B26A00", "not_comparable": "#C62828"}
        self.compare_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            why = "; ".join(f"{i['level']}: {i['message']}" for i in row["issues"]) or "-"
            delta_pct = "-" if row["delta_percent"] is None else f"{row['delta_percent']:+.2f} %"
            delta = "-" if row["delta"] is None else f"{row['delta']:+,.2f}"
            cells = [row["benchmark"], _fmt(row["mean_a"]), _fmt(row["mean_b"]), delta, delta_pct,
                     row["status"].replace("_", " "), why]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 5:
                    item.setForeground(QColor(colors[row["status"]]))
                elif row["status"] == "not_comparable" and c in (3, 4):
                    item.setForeground(QColor("#757575"))
                self.compare_table.setItem(r, c, item)
        same = " (the same session on both sides)" if a["id"] == b["id"] else ""
        self.compare_note.setText(f"Compared #{a['id']} (A) with #{b['id']} (B){same}. "
                                  "Delta % is relative to A.")

    def _create_steam_tab(self) -> QWidget:
        """Steam client, libraries and installed games with a guarded Launch button."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.steam_status_label = QLabel("Scanning...")
        self.steam_status_label.setWordWrap(True)
        layout.addWidget(self.steam_status_label)
        self.steam_table = QTableWidget()
        self.steam_table.setColumnCount(7)
        self.steam_table.setHorizontalHeaderLabels(
            ["AppID", "Name", "Installed", "Built-in benchmark", "Runtime", "Library", "Action"])
        sheader = self.steam_table.horizontalHeader()
        for col in (0, 2, 3, 4, 5, 6):
            sheader.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        sheader.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.steam_table, 1)
        self.steam_log = QLabel("")
        self.steam_log.setWordWrap(True)
        self.steam_log.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.steam_log)
        return tab

    def steam_games(self) -> List[dict]:
        if self.discovery is None:
            return []
        return [g for g in self.discovery.games if g.get("store") == "steam"]

    def refresh_steam_view(self):
        """Show the Steam client, libraries and games from the latest discovery."""
        if self.discovery is None:
            return
        client = self.discovery.stores.get("steam") or {}
        if client.get("status") != "detected":
            self.steam_status_label.setText(f"Steam client: {client.get('status', 'not checked')}")
        else:
            libs = ", ".join(client.get("libraries") or []) or "none reachable"
            notes = "; ".join(client.get("notes") or [])
            text = f"Steam client: {client.get('path')}  |  Libraries: {libs}"
            if notes:
                text += f"  |  Notes: {notes}"
            self.steam_status_label.setText(text)
        games = sorted(self.steam_games(), key=lambda g: g["name"].lower())
        self.steam_table.setRowCount(len(games))
        busy = self._steam_worker is not None and self._steam_worker.isRunning()
        for row, game in enumerate(games):
            installed = {True: "yes", False: "no (partial/updating)", None: "unknown"}[game.get("installed")]
            values = [game["app_id"], game["name"], installed, game.get("builtin_benchmark") or "-",
                      game.get("runtime") or "-", game.get("library") or "-"]
            for col, value in enumerate(values):
                self.steam_table.setItem(row, col, QTableWidgetItem(str(value)))
            is_tool = game.get("kind") == "tool"
            button = QPushButton("Steam tool" if is_tool else "Launch...")
            button.setEnabled(not busy and not is_tool and game.get("installed") is True)
            button.clicked.connect(lambda checked=False, g=game: self._on_steam_launch_clicked(g))
            self.steam_table.setCellWidget(row, 6, button)

    def _confirm_steam_launch(self, game: dict) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Confirm Steam Launch")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Launch {game['name']} through Steam?")
        box.setInformativeText(
            f"steam://rungameid/{game['app_id']} will be opened. Steam may update the game or show "
            "its own dialogs first. Nothing is changed in Steam settings, and no process is closed.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Yes

    def _on_steam_launch_clicked(self, game: dict):
        if self._steam_worker is not None and self._steam_worker.isRunning():
            self.steam_log.setText("A Steam launch is already in progress.")
            return
        if not self._confirm_steam_launch(game):
            self.steam_log.setText(f"Launch of {game['name']} was cancelled by user.")
            return
        appids = [g["app_id"] for g in self.steam_games()]
        self._steam_worker = SteamLaunchWorker(game, appids)
        self._steam_worker.finished.connect(self._on_steam_launch_finished)
        self.steam_log.setText(f"Launching {game['name']} through Steam, waiting for its process...")
        self._steam_worker.start()
        self.refresh_steam_view()

    @Slot(dict)
    def _on_steam_launch_finished(self, result: dict):
        self.steam_log.setText(f"{result.get('status')}: {result.get('message')}")
        self.refresh_steam_view()

    # ── Sessions, manual results and manifests (Phase 4A) ───────────────────

    def reload_profiles(self, select: Optional[str] = None):
        """Fill the profile picker: built-ins, then saved profiles."""
        from app.profiles import BUILTIN_PROFILES
        try:
            profiles = self.db.list_run_profiles() if self.db is not None else list(BUILTIN_PROFILES)
            profiles = list(profiles)
        except Exception:
            logger.exception("Could not load run profiles")
            profiles = list(BUILTIN_PROFILES)
        current = select or self.profile_combo.currentText()
        self.profile_combo.clear()
        for profile in profiles:
            label = profile.name + ("" if profile.builtin else " (saved)")
            self.profile_combo.addItem(label, profile)
        index = max(0, next((i for i, prof in enumerate(profiles)
                             if prof.name == current or current.startswith(prof.name + " ")), 0))
        self.profile_combo.setCurrentIndex(index)

    def selected_profile(self):
        from app.profiles import BUILTIN_PROFILES
        return self.profile_combo.currentData() or BUILTIN_PROFILES[0]

    def _ask_profile(self):
        from app.ui.profile_dialog import ProfileDialog
        dialog = ProfileDialog(self.selected_profile(), self)
        return dialog.profile if dialog.exec() == QDialog.Accepted else None

    def _on_save_profile_clicked(self):
        profile = self._ask_profile()
        if profile is None or self.db is None:
            return
        try:
            self.db.save_run_profile(profile)
        except ValueError as exc:
            self.run_log.append(f"Profile NOT saved: {exc}")
            return
        self.reload_profiles(select=profile.name)
        self.run_log.append(f"Saved run profile {profile.name!r}.")

    def _start_new_session(self):
        """The next recorded result starts a new session."""
        self.current_session_id = None
        self.session_label.setText("Session: none yet (created with the first recorded result)")

    def _ensure_session(self) -> Optional[int]:
        """Create the session and its manifest on first use; None if no database."""
        if self.current_session_id is not None or self.db is None:
            return self.current_session_id
        from dataclasses import asdict
        from datetime import datetime
        from app.manifest import build_manifest, git_commit
        name = f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        sid = self.db.create_session(name, "Manual results entered in the app", is_manual=True)
        manifest = build_manifest(
            {"id": sid, "name": name, "is_test": False, "is_manual": True},
            run_settings=self.selected_profile().settings(),
            environment=self.environment_profile.to_dict() if self.environment_profile else None,
            # Benchmarks ready when the session started (versions, paths, compatibility)
            benchmarks_used=[asdict(b) for b in self.benchmark_manager.benchmarks if b.is_runnable()],
            commit=git_commit(),
        )
        try:
            self.db.save_session_manifest(sid, manifest)
        except Exception:
            logger.exception("Could not save session manifest")
        self.current_session_id = sid
        self.session_label.setText(f"Session: #{sid} {name}  (manifest saved)")
        return sid

    def _ask_result(self, bm: BenchmarkInfo, session_label: str) -> Optional[dict]:
        from app.ui.record_dialog import RecordResultDialog
        unit = "score" if bm.type == BenchmarkType.WEB else "points"
        dialog = RecordResultDialog(bm.display_name, session_label, unit, self)
        return dialog.values if dialog.exec() == QDialog.Accepted else None

    def _on_record_clicked(self, bm: BenchmarkInfo):
        label = f"#{self.current_session_id}" if self.current_session_id else "new session"
        values = self._ask_result(bm, label)
        if values is None:
            return
        sid = self._ensure_session()
        if sid is None:
            self.run_log.append("No database available; result not saved.")
            return
        try:
            run_index = len(self.db.get_runs(sid, bm.name, include_test=True))
            self.db.create_result(
                sid, bm.name, bm.type.value, values["score"], values["unit"],
                benchmark_version=bm.version, is_manual=True, status=values["status"],
                is_warmup=values["is_warmup"], run_index=run_index, note=values["note"])
        except Exception as exc:
            logger.exception("Could not save result")
            self.run_log.append(f"Result NOT saved: {exc}")
            return
        kind = "warmup" if values["is_warmup"] else "measured"
        score = values["score"] if values["score"] is not None else "no score"
        self.run_log.append(f"Saved {kind} run #{run_index} of {bm.display_name} in session #{sid}: "
                            f"{values['status']}, {score} [MANUAL]")
        self.refresh_history_screen()

    def _create_run_tab(self) -> QWidget:
        """Create Run Benchmarks tab for launching and assisted execution."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        desc = QLabel("Select an installed benchmark to launch or record scores:")
        layout.addWidget(desc)
        
        session_row = QHBoxLayout()
        self.session_label = QLabel("Session: none yet (created with the first recorded result)")
        self.new_session_btn = QPushButton("Start new session")
        self.new_session_btn.clicked.connect(self._start_new_session)
        session_row.addWidget(self.session_label, 1)
        session_row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip("Run settings recorded in the manifest of the next new session")
        session_row.addWidget(self.profile_combo)
        self.save_profile_btn = QPushButton("Save profile...")
        self.save_profile_btn.clicked.connect(self._on_save_profile_clicked)
        session_row.addWidget(self.save_profile_btn)
        session_row.addWidget(self.new_session_btn)
        layout.addLayout(session_row)
        self.reload_profiles()

        self.run_table = QTableWidget()
        self.run_table.setColumnCount(6)
        self.run_table.setHorizontalHeaderLabels([
            "Benchmark", "Version", "Status", "Execution Type", "Action", "Results"
        ])
        header = self.run_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        layout.addWidget(self.run_table, 1)

        from app.ui.automation_panel import AutomationPanel
        self.automation_panel = AutomationPanel(
            self.db, self.selected_profile,
            lambda: self.environment_profile.to_dict() if self.environment_profile else None,
            on_session_changed=self.refresh_history_screen, parent=tab)
        layout.addWidget(self.automation_panel)
        
        # Status / Feedback area
        self.run_log = QTextEdit()
        self.run_log.setReadOnly(True)
        self.run_log.setMaximumHeight(140)
        self.run_log.setPlaceholderText("Execution notes and launch confirmations will appear here...")
        layout.addWidget(self.run_log)
        
        return tab
    
    def _create_history_tab(self) -> QWidget:
        """Create Session History tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        search_row = QHBoxLayout()
        self.search_text = QLineEdit()
        self.search_text.setPlaceholderText("Search session name or description")
        self.search_benchmark = QComboBox()
        self.search_benchmark.addItem("All benchmarks", None)
        for definition in DEFINITIONS_BY_ID.values():
            self.search_benchmark.addItem(definition.display_name, definition.id)
        self.search_from = QLineEdit()
        self.search_from.setPlaceholderText("From YYYY-MM-DD")
        self.search_to = QLineEdit()
        self.search_to.setPlaceholderText("To YYYY-MM-DD")
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.refresh_history_screen)
        self.search_text.returnPressed.connect(self.refresh_history_screen)
        for widget, stretch in ((self.search_text, 2), (self.search_benchmark, 1),
                                (self.search_from, 1), (self.search_to, 1), (self.search_btn, 0)):
            search_row.addWidget(widget, stretch)
        layout.addLayout(search_row)
        self.search_error = QLabel("")
        self.search_error.setStyleSheet("color: #C62828;")
        layout.addWidget(self.search_error)

        self.sessions_table = QTableWidget()
        self.sessions_table.setColumnCount(7)
        self.sessions_table.setHorizontalHeaderLabels(
            ["Session", "Name", "Created", "Benchmarks", "Runs", "Manifest", "Flags"])
        self.sessions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.sessions_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.sessions_table, 1)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(7)
        self.history_table.setHorizontalHeaderLabels([
            "Session ID", "Session Name", "Benchmark", "Version", "Score", "Timestamp", "Flags"
        ])
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.history_table, 1)

        layout.addWidget(QLabel("Statistics per session and benchmark "
                                "(warmup, not-completed and test runs are excluded):"))
        self.stats_table = QTableWidget()
        self.stats_table.setColumnCount(11)
        self.stats_table.setHorizontalHeaderLabels([
            "Session", "Benchmark", "Runs", "Mean", "Median", "Min", "Max", "Std dev", "CV %",
            "Excluded", "Source"])
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.stats_table, 1)

        # Controls row: refresh button + show test data checkbox
        ctrl_layout = QHBoxLayout()
        self.refresh_history_btn = QPushButton("Refresh History")
        self.refresh_history_btn.clicked.connect(self.refresh_history_screen)
        ctrl_layout.addWidget(self.refresh_history_btn)

        self.show_test_data_cb = QCheckBox("Show test data")
        self.show_test_data_cb.setToolTip(
            "When unchecked (default), rows marked as test data are hidden. "
            "Test data entries are labeled [TEST] and/or [MANUAL]."
        )
        ctrl_layout.addWidget(self.show_test_data_cb)
        ctrl_layout.addStretch()
        self.export_buttons = {}
        for fmt, label in (("html", "HTML report..."), ("json", "Export JSON..."), ("csv", "Export CSV...")):
            btn = QPushButton(label)
            btn.setToolTip("Export the session selected in the sessions table")
            btn.clicked.connect(lambda _checked=False, f=fmt: self.export_selected_session(f))
            ctrl_layout.addWidget(btn)
            self.export_buttons[fmt] = btn
        self.backup_btn = QPushButton("Back up database")
        self.backup_btn.setToolTip("Copy the database into the backups folder next to it "
                                   "(existing backups are never overwritten)")
        self.backup_btn.clicked.connect(self.backup_now)
        ctrl_layout.addWidget(self.backup_btn)
        layout.addLayout(ctrl_layout)
        self.export_note = QLabel("")
        self.export_note.setWordWrap(True)
        self.export_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.export_note)

        return tab

    def selected_session_id(self) -> Optional[int]:
        row = self.sessions_table.currentRow()
        item = self.sessions_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def export_selected_session(self, fmt: str):
        """Ask for a file name and write the selected session as html / json / csv."""
        from app.export import build_session_export, default_filename, write_export
        session_id = self.selected_session_id()
        if not self.db or session_id is None:
            self.export_note.setText("Select a session in the sessions table first.")
            return
        try:
            export = build_session_export(self.db, session_id)
        except Exception as e:
            self.export_note.setText(f"Export failed: {e}")
            return
        filters = {"html": "HTML report (*.html)", "json": "JSON (*.json)", "csv": "CSV (*.csv)"}
        start = os.path.join(os.path.expanduser("~"), "Documents", default_filename(export, fmt))
        path, _ = QFileDialog.getSaveFileName(self, "Export session", start, filters[fmt])
        if not path:
            return
        try:
            written = write_export(export, path, fmt)
        except Exception as e:
            self.export_note.setText(f"Export failed: {e}")
            return
        self.export_note.setText(f"Session #{session_id} exported to {written}")
        if fmt == "html" and self._confirm("Open report",
                                           f"Report saved to:\n{written}\n\nOpen it in the browser now?"):
            if not webbrowser.open(written.resolve().as_uri()):
                self.export_note.setText(f"Report saved to {written} (could not open a browser)")

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def backup_now(self):
        from app.backup import backup_database
        if not self.db:
            return
        try:
            info = backup_database(self.db.db_path)
        except Exception as e:
            self.export_note.setText(f"Backup failed: {e}")
            return
        counts = ", ".join(f"{k} {v}" for k, v in info["counts"].items())
        self.export_note.setText(f"Backup saved: {info['path']} (integrity {info['integrity']}, {counts}, "
                                 f"sha256 {info['sha256'][:16]}...)")
    
    def start_scan(self):
        """Trigger benchmark scan using background worker thread."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            logger.info("Scan already in progress.")
            return
        
        # Update "Scanning..." UI state
        self.rescan_btn.setEnabled(False)
        self.rescan_btn.setText("Scanning...")
        self.status_label.setText("Scanning benchmarks and system capabilities...")
        
        # Instantiate worker and keep referenced on self._scan_worker
        self._scan_worker = BenchmarkWorker(self.benchmark_manager)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.scan_finished.connect(self._on_scan_finished)
        self._scan_worker.scan_error.connect(self._on_scan_error)
        self._scan_worker.profile_ready.connect(self._on_profile_ready)
        self._scan_worker.discovery_ready.connect(self._on_discovery_ready)
        
        self._scan_worker.start()

    def closeEvent(self, event):
        """Clean shutdown: wait for or cancel workers before closing."""
        self.automation_panel.shutdown()   # emergency stop; only our own child processes end
        if self._scan_worker is not None and self._scan_worker.isRunning():
            logger.info("Waiting for scan worker to finish before shutdown...")
            self._scan_worker.wait(5000)
            if self._scan_worker.isRunning():
                logger.warning("Scan worker did not finish in time; aborting.")
                self._scan_worker.terminate()
                self._scan_worker.wait(1000)
        if self._launch_worker is not None and self._launch_worker.isRunning():
            logger.info("Waiting for launch worker to finish before shutdown...")
            self._launch_worker.wait(5000)
            if self._launch_worker.isRunning():
                logger.warning("Launch worker did not finish in time; aborting.")
                self._launch_worker.terminate()
                self._launch_worker.wait(1000)
        if self._steam_worker is not None and self._steam_worker.isRunning():
            self._steam_worker.stop()   # ends the confirmation wait; the game keeps running
            self._steam_worker.wait(5000)
        event.accept()
    
    @Slot(object)
    def _on_profile_ready(self, profile: EnvironmentProfile):
        """Store and show the profile, and save it as an environment snapshot."""
        self.environment_profile = profile
        self.refresh_environment_view()
        if self.db is not None:
            try:
                self.environment_snapshot_id = self.db.save_environment_snapshot(profile.to_dict())
            except Exception:
                logger.exception("Could not save environment snapshot")

    @Slot(object)
    def _on_discovery_ready(self, result: DiscoveryResult):
        """Store and show discovery, saved with a link to the environment snapshot."""
        self.discovery = result
        self.refresh_tools_view()
        self.refresh_steam_view()
        if self.db is not None:
            try:
                self.db.save_discovery_snapshot(result.to_dict(), self.environment_snapshot_id)
            except Exception:
                logger.exception("Could not save discovery snapshot")

    @Slot(str)
    def _on_scan_progress(self, msg: str):
        """Handle worker progress signal."""
        self.status_label.setText(msg)
    
    @Slot(list)
    def _on_scan_finished(self, benchmarks: List[BenchmarkInfo]):
        """Handle completed benchmark scan."""
        # Restore UI controls
        self.rescan_btn.setEnabled(True)
        self.rescan_btn.setText("Rescan Benchmarks")
        self.status_label.setText(f"Scan complete: {len(benchmarks)} benchmarks evaluated")
        
        # Refresh Library and Run screens
        self.refresh_library_screen()
        self.refresh_run_screen()
        self.refresh_history_screen()
    
    @Slot(str)
    def _on_scan_error(self, err_msg: str):
        """Handle worker scan error."""
        self.rescan_btn.setEnabled(True)
        self.rescan_btn.setText("Rescan Benchmarks")
        self.status_label.setText(f"Scan error: {err_msg}")
        QMessageBox.warning(self, "Scan Error", f"An error occurred during benchmark scan:\n{err_msg}")
    
    def refresh_library_screen(self):
        """Refresh the Benchmark Library table with current benchmarks.
        
        Consolidates all detected Geekbench majors into one 'Geekbench' card/row showing
        every detected major version, each with its own status, version, and path.
        """
        benchmarks = self.benchmark_manager.benchmarks
        
        # Partition into Geekbench and other benchmarks
        geekbenches = [bm for bm in benchmarks if bm.name.startswith('geekbench')]
        others = [bm for bm in benchmarks if not bm.name.startswith('geekbench')]
        
        display_rows = []
        
        if geekbenches:
            # Combine all detected Geekbench majors into one card/row
            status_lines = []
            ver_lines = []
            arch_lines = []
            method_lines = []
            path_lines = []
            
            any_ready = False
            any_error = False
            for gb in geekbenches:
                st = _status_label(gb)
                if gb.status == BenchmarkStatus.SCAN_ERROR:
                    any_error = True
                if gb.status in (BenchmarkStatus.READY_MANUAL, BenchmarkStatus.READY_AUTO):
                    any_ready = True
                status_lines.append(f"{gb.display_name}: {st}")
                ver_lines.append(f"{gb.display_name}: {gb.version or 'None'}")
                arch_lines.append(f"{gb.display_name}: {gb.architecture or 'None'}")
                method_lines.append(f"{gb.display_name}: {gb.detection_method or 'not_found'}")
                
                path_desc = gb.executable_path or "None"
                if gb.additional_installs:
                    extra_paths = ", ".join(ai['path'] for ai in gb.additional_installs)
                    path_desc += f" (also: {extra_paths})"
                path_lines.append(f"{gb.display_name}: {path_desc}")
            
            display_rows.append({
                'name': "Geekbench",
                'type': "CPU",
                'status': "\n".join(status_lines),
                'status_ready': any_ready,
                'status_is_error': any_error,
                'version': "\n".join(ver_lines),
                'arch': "\n".join(arch_lines),
                'method': "\n".join(method_lines),
                'path': "\n".join(path_lines)
            })
            
        for bm in others:
            st = _status_label(bm)
            status_is_error = bm.status == BenchmarkStatus.SCAN_ERROR
            ver_text = bm.version or "None"
            arch_text = bm.architecture or "None"
            method_text = bm.detection_method or "not_found"
            path_text = bm.executable_path or (bm.appx_package.get('app_id', 'Store App') if bm.appx_package else "None")
            display_rows.append({
                'name': bm.display_name,
                'type': bm.type.value.upper(),
                'status': st,
                'status_ready': bm.status in (BenchmarkStatus.READY_MANUAL, BenchmarkStatus.READY_AUTO),
                'status_is_error': status_is_error,
                'version': ver_text,
                'arch': arch_text,
                'method': method_text,
                'path': path_text
            })
            
        self.library_table.setRowCount(len(display_rows))
        for row, item in enumerate(display_rows):
            self.library_table.setItem(row, 0, QTableWidgetItem(item['name']))
            self.library_table.setItem(row, 1, QTableWidgetItem(item['type']))
            
            status_item = QTableWidgetItem(item['status'])
            if item.get('status_is_error'):
                status_item.setForeground(QColor("#C62828"))
            elif item['status_ready']:
                status_item.setForeground(QColor("#2E7D32"))
            else:
                status_item.setForeground(QColor("#757575"))
            self.library_table.setItem(row, 2, status_item)
            
            self.library_table.setItem(row, 3, QTableWidgetItem(item['version']))
            self.library_table.setItem(row, 4, QTableWidgetItem(item['arch']))
            self.library_table.setItem(row, 5, QTableWidgetItem(item['method']))
            self.library_table.setItem(row, 6, QTableWidgetItem(item['path']))
            
        self.library_table.resizeRowsToContents()
    
    def refresh_run_screen(self):
        """Refresh the Run Benchmarks table with runnable benchmarks."""
        runnable = [bm for bm in self.benchmark_manager.benchmarks if bm.is_runnable()]
        self.run_table.setRowCount(len(runnable))
        
        for row, bm in enumerate(runnable):
            self.run_table.setItem(row, 0, QTableWidgetItem(bm.display_name))
            self.run_table.setItem(row, 1, QTableWidgetItem(bm.version or "N/A"))
            self.run_table.setItem(row, 2, QTableWidgetItem("assisted/manual"))
            
            exec_type = "Browser" if bm.type == BenchmarkType.WEB else ("Store App" if bm.appx_package else "Desktop GUI")
            self.run_table.setItem(row, 3, QTableWidgetItem(exec_type))
            
            # Action button
            action_btn = QPushButton(f"Launch {bm.display_name}")
            # Bind current benchmark to click handler
            action_btn.clicked.connect(lambda checked, b=bm: self._on_launch_clicked(b))
            self.run_table.setCellWidget(row, 4, action_btn)
            record_btn = QPushButton("Record result...")
            record_btn.clicked.connect(lambda checked=False, b=bm: self._on_record_clicked(b))
            self.run_table.setCellWidget(row, 5, record_btn)
    
    def _on_launch_clicked(self, bm: BenchmarkInfo):
        """Handle user clicking launch on a benchmark with explicit confirmation dialog.

        The confirmation dialog is modal (runs on GUI thread).  After the user
        confirms, the actual launch (subprocess.Popen, UAC wait, process polling)
        runs in a LaunchWorker (QThread) so the GUI thread is never blocked.
        """
        # Guard: prevent concurrent launches
        if self._launch_worker is not None and self._launch_worker.isRunning():
            self.run_log.append("[INFO] A launch is already in progress. Please wait.")
            return

        # ── Web benchmarks: version + browser selection ──────────────────────
        if bm.type == BenchmarkType.WEB:
            definition = DEFINITIONS_BY_ID.get(bm.name)
            if definition and definition.benchmark_page_versions:
                self._launch_web_benchmark(bm, definition)
            elif bm.official_url:
                self._launch_web_with_url(bm, bm.official_url)
            return

        # ── Non-web benchmarks: show confirmation, then launch via worker ──
        confirm_box = QMessageBox(self)
        confirm_box.setIcon(QMessageBox.Question)
        confirm_box.setWindowTitle("Confirm Benchmark Launch")

        if bm.name == 'cinebench':
            confirm_box.setText(f"Launch {bm.display_name} via Windows Store AppID?")
            confirm_box.setInformativeText("This will launch the Cinebench application from the Start menu.")
        elif bm.name.startswith('geekbench'):
            confirm_box.setText(f"Launch {bm.display_name} GUI executable?")
            confirm_box.setInformativeText(
                f"Path: {bm.executable_path}\n\n"
                "IMPORTANT: The GUI will be launched with NO arguments.\n"
                "Automation requires a Pro license (not verified for this version).\n\n"
                "Note: This benchmark executable requests administrator rights.\n"
                "Windows will show a UAC prompt — click 'Yes' to proceed.\n"
                "The app itself remains non-elevated."
            )
        else:
            confirm_box.setText(f"Launch {bm.display_name}?")
            if bm.executable_path:
                confirm_box.setInformativeText(
                    f"Path: {bm.executable_path}\n\n"
                    "Note: If this benchmark requires administrator rights,\n"
                    "Windows will show a UAC prompt. The app itself remains non-elevated."
                )

        confirm_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm_box.setDefaultButton(QMessageBox.No)

        if confirm_box.exec() != QMessageBox.Yes:
            self.run_log.append(f"Launch of {bm.display_name} was cancelled by user.")
            return

        # ── Launch via background worker (GUI thread never blocks) ──────────
        self._launch_worker = LaunchWorker(bm, timeout=5)
        self._launch_worker.progress.connect(self._on_launch_progress)
        self._launch_worker.finished.connect(self._on_launch_finished)
        self._launch_worker.start()

    @Slot(str)
    def _on_launch_progress(self, msg: str):
        """Handle launch worker progress signal."""
        self.run_log.append(msg)

    @Slot(dict)
    def _on_launch_finished(self, result: dict):
        """Handle launch worker finished signal."""
        status_line = f"[{result.get('status', 'info').upper()}] {result.get('message', '')}"
        if result.get('elevation_used'):
            status_line += " (elevation was required)"
        self.run_log.append(status_line)
        self._launch_worker = None

    def _launch_web_benchmark(self, bm: BenchmarkInfo, definition):
        """Launch a web benchmark with version and browser selection.

        Shows version choice (defaulting to current), superseded warnings,
        browser choice from detected browsers, and explicit confirmation.
        """
        versions = definition.benchmark_page_versions

        # Build version labels
        version_labels = []
        default_idx = 0
        for i, v in enumerate(versions):
            label = f"{v['version']}"
            if v.get('status') == 'superseded':
                label += " (superseded)"
            elif v.get('status') == 'current':
                default_idx = i
                label += " (current, default)"
            version_labels.append(label)

        # Get detected browsers
        browser_info = self.benchmark_manager.browser_info

        # Build confirmation dialog
        confirm_dialog = QDialog(self)
        confirm_dialog.setWindowTitle(f"Launch {bm.display_name}")
        confirm_dialog.setMinimumWidth(500)
        layout = QVBoxLayout(confirm_dialog)

        # Version selection
        version_group = QGroupBox("Benchmark Version")
        v_layout = QVBoxLayout(version_group)
        version_combo = QComboBox()
        for vl in version_labels:
            version_combo.addItem(vl)
        version_combo.setCurrentIndex(default_idx)
        v_layout.addWidget(version_combo)

        # Superseded warning label
        warning_label = QLabel("")
        warning_label.setStyleSheet("color: #C62828; font-weight: bold;")
        warning_label.setWordWrap(True)
        v_layout.addWidget(warning_label)

        def on_version_changed(idx):
            v = versions[idx]
            if v.get('status') == 'superseded':
                warning_label.setText(
                    f"WARNING: Version {v['version']} is superseded. "
                    "Scores are not comparable with the current version. "
                    "Each version is treated as a separate series."
                )
                warning_label.show()
            else:
                warning_label.hide()

        version_combo.currentIndexChanged.connect(on_version_changed)
        on_version_changed(default_idx)  # initial check
        layout.addWidget(version_group)

        # Browser selection
        if browser_info:
            browser_group = QGroupBox("Browser")
            b_layout = QVBoxLayout(browser_group)
            browser_combo = QComboBox()
            for bname, bdata in browser_info.items():
                ver = bdata.get('version', 'unknown')
                label = f"{bname} {ver}"
                if bdata.get('found_by_fallback'):
                    label += " (found by fallback path, unverified)"
                browser_combo.addItem(label)
            b_layout.addWidget(browser_combo)
            layout.addWidget(browser_group)
        else:
            no_browser_label = QLabel(
                "No compatible browsers detected. The system default browser will be used."
            )
            no_browser_label.setStyleSheet("color: #E65100;")
            layout.addWidget(no_browser_label)
            browser_combo = None

        # URL preview
        url_label = QLabel(f"URL: {versions[default_idx]['url']}")
        url_label.setWordWrap(True)
        url_label.setStyleSheet("font-style: italic; color: #555;")
        layout.addWidget(url_label)

        def on_url_version_changed(idx):
            url_label.setText(f"URL: {versions[idx]['url']}")

        version_combo.currentIndexChanged.connect(on_url_version_changed)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, confirm_dialog
        )
        btn_box.accepted.connect(confirm_dialog.accept)
        btn_box.rejected.connect(confirm_dialog.reject)
        btn_box.button(QDialogButtonBox.Ok).setText("Launch")
        layout.addWidget(btn_box)

        if confirm_dialog.exec() != QDialog.Accepted:
            self.run_log.append(f"Launch of {bm.display_name} was cancelled by user.")
            return

        # User confirmed — spawn browser via worker (off GUI thread)
        chosen_idx = version_combo.currentIndex()
        chosen_version = versions[chosen_idx]
        url = chosen_version['url']

        browser_path = None
        browser_name = None
        if browser_combo is not None:
            chosen_browser_name = list(browser_info.keys())[browser_combo.currentIndex()]
            browser_path = browser_info[chosen_browser_name]['path']
            browser_name = chosen_browser_name

        self._launch_web_worker(
            bm, url, chosen_version['version'],
            browser_path=browser_path, browser_name=browser_name,
        )

    def _launch_web_worker(
        self, bm: BenchmarkInfo, url: str, version_str: str,
        browser_path: Optional[str] = None, browser_name: Optional[str] = None,
    ):
        """Spawn a worker to open the browser for a web benchmark."""
        if self._launch_worker is not None and self._launch_worker.isRunning():
            self.run_log.append("[INFO] A launch is already in progress. Please wait.")
            return

        worker = _WebLaunchWorker(bm, url, version_str, browser_path, browser_name)
        worker.finished.connect(self._on_launch_finished)
        worker.progress.connect(self._on_launch_progress)
        self._launch_worker = worker  # type: ignore[assignment]
        self._launch_worker.start()

    def _launch_web_with_url(self, bm: BenchmarkInfo, url: str):
        """Fallback for web benchmarks without page version config.

        Shows a confirmation dialog, then spawns the browser via a worker
        (off the GUI thread).
        """
        if self._launch_worker is not None and self._launch_worker.isRunning():
            self.run_log.append("[INFO] A launch is already in progress. Please wait.")
            return

        confirm_box = QMessageBox(self)
        confirm_box.setIcon(QMessageBox.Question)
        confirm_box.setWindowTitle("Confirm Benchmark Launch")
        confirm_box.setText(f"Open {bm.display_name} in your default web browser?")
        confirm_box.setInformativeText(f"URL: {url}")
        confirm_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm_box.setDefaultButton(QMessageBox.No)

        if confirm_box.exec() != QMessageBox.Yes:
            self.run_log.append(f"Launch of {bm.display_name} was cancelled by user.")
            return

        worker = _WebLaunchWorker(bm, url, bm.version or 'unknown')
        worker.finished.connect(self._on_launch_finished)
        worker.progress.connect(self._on_launch_progress)
        self._launch_worker = worker
        self._launch_worker.start()
    
    def refresh_history_screen(self):
        """Refresh historical results table from database.

        Excludes test data by default. When "Show test data" is checked, test rows
        are shown with [TEST] and/or [MANUAL] flags in the Flags column.
        """
        if not self.db:
            return
        try:
            include_test = self.show_test_data_cb.isChecked()
            results = self.db.get_all_results(include_test=include_test)
            self.history_table.setRowCount(len(results))
            for row, r in enumerate(results):
                self.history_table.setItem(row, 0, QTableWidgetItem(str(r.get('id', ''))))
                self.history_table.setItem(row, 1, QTableWidgetItem(str(r.get('name', ''))))
                self.history_table.setItem(row, 2, QTableWidgetItem(str(r.get('benchmark_name', ''))))
                self.history_table.setItem(row, 3, QTableWidgetItem(str(r.get('benchmark_version') or 'N/A')))
                score_str = f"{r.get('score', 'N/A')} {r.get('unit', '')}"
                self.history_table.setItem(row, 4, QTableWidgetItem(score_str))
                self.history_table.setItem(row, 5, QTableWidgetItem(str(r.get('created_at', ''))))

                # Flags column
                flags = []
                if r.get('is_test') or r.get('r_is_test'):
                    flags.append("[TEST]")
                if r.get('is_manual') or r.get('r_is_manual'):
                    flags.append("[MANUAL]")
                flag_text = " ".join(flags) if flags else ""
                flag_item = QTableWidgetItem(flag_text)
                if flags:
                    flag_item.setForeground(QColor("#C62828"))
                self.history_table.setItem(row, 6, flag_item)
        except Exception as e:
            logger.debug(f"Could not refresh history table: {e}")
        self.refresh_stats_table()
        self.reload_compare_sessions()

    def search_filters(self) -> Optional[dict]:
        """Current search filters, or None (with a message) if a date is invalid."""
        from datetime import date
        filters = {"text": self.search_text.text().strip() or None,
                   "benchmark_name": self.search_benchmark.currentData(),
                   "date_from": self.search_from.text().strip() or None,
                   "date_to": self.search_to.text().strip() or None}
        for key in ("date_from", "date_to"):
            if filters[key]:
                try:
                    date.fromisoformat(filters[key])
                except ValueError:
                    self.search_error.setText(f"Invalid date {filters[key]!r}: use YYYY-MM-DD")
                    return None
        self.search_error.setText("")
        return filters

    def refresh_sessions_table(self) -> Optional[set]:
        """Fill the sessions table from the search; returns the matching session ids."""
        if not self.db:
            return None
        filters = self.search_filters()
        if filters is None:
            return None
        try:
            sessions = self.db.search_sessions(include_test=self.show_test_data_cb.isChecked(), **filters)
        except Exception as e:
            logger.debug(f"Could not search sessions: {e}")
            return None
        self.sessions_table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            flags = " ".join(f for f, on in (("[TEST]", s["is_test"]), ("[MANUAL]", s["is_manual"])) if on)
            cells = [f"#{s['id']}", s["name"], s["created_at"][:16].replace("T", " "),
                     ", ".join(s["benchmarks"]) or "-", str(s["run_count"]),
                     "yes" if s["has_manifest"] else "no", flags]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.UserRole, s["id"])
                self.sessions_table.setItem(row, col, item)
        return {s["id"] for s in sessions}

    def refresh_stats_table(self):
        """One row per (session, benchmark) with summarize_runs statistics,
        limited to the sessions matching the current search."""
        if not self.db:
            return
        from app.stats import summarize_runs
        matching = self.refresh_sessions_table()
        try:
            include_test = self.show_test_data_cb.isChecked()
            groups: Dict[tuple, list] = {}
            for run in self.db.get_runs(include_test=include_test):
                if matching is not None and run["session_id"] not in matching:
                    continue
                groups.setdefault((run["session_id"], run["benchmark_name"]), []).append(run)
        except Exception as e:
            logger.debug(f"Could not load runs for statistics: {e}")
            return
        self.stats_table.setRowCount(len(groups))
        for row, ((sid, name), runs) in enumerate(sorted(groups.items(), key=lambda kv: (-kv[0][0], kv[0][1]))):
            summary = summarize_runs(runs, include_test=include_test)
            s = summary["stats"]
            excluded = ", ".join(f"{k} {v}" for k, v in summary["excluded"].items() if v) or "-"
            source = "mixed manual + real" if summary["mixed_sources"] else (
                "MANUAL" if summary["manual_included"] else ("real" if summary["real_included"] else "-"))
            cells = [f"#{sid}", name, str(summary["included"]), _fmt(s["mean"]), _fmt(s["median"]),
                     _fmt(s["min"]), _fmt(s["max"]), _fmt(s["stdev"]), _fmt(s["cv_percent"], 1), excluded, source]
            for col, text in enumerate(cells):
                self.stats_table.setItem(row, col, QTableWidgetItem(text))
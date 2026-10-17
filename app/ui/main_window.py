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
    QProgressBar, QFrame, QStackedWidget
)
from PySide6.QtCore import Qt, Signal, Slot, QThread
from PySide6.QtGui import QFont, QColor

from app.benchmark_manager import (
    BenchmarkManager, BenchmarkExecutor, BenchmarkInfo,
    BenchmarkStatus, BenchmarkType, launch_assisted_benchmark
)
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

    def __init__(self, benchmark_manager: BenchmarkManager):
        super().__init__()
        self.benchmark_manager = benchmark_manager
        self._is_running = False

    def run(self):
        """Execute the benchmark scan in the background thread."""
        self._is_running = True
        self.scan_started.emit()
        try:
            self.progress.emit("Scanning system capabilities and benchmarks...")
            # Calls scan() only, as required
            benchmarks = self.benchmark_manager.scan()
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


class MainWindow(QMainWindow):
    """Main application window for PC Benchmark Manager."""
    
    def __init__(self, db: Any, system_info: dict):
        super().__init__()
        
        self.db = db
        self.system_info = system_info or {}
        self.current_session_id: Optional[int] = None
        
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
        layout.addStretch(1)
        return tab
    
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
        return tab
    
    def _create_run_tab(self) -> QWidget:
        """Create Run Benchmarks tab for launching and assisted execution."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        desc = QLabel("Select an installed benchmark to launch or record scores:")
        layout.addWidget(desc)
        
        self.run_table = QTableWidget()
        self.run_table.setColumnCount(5)
        self.run_table.setHorizontalHeaderLabels([
            "Benchmark", "Version", "Status", "Execution Type", "Action"
        ])
        header = self.run_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        
        layout.addWidget(self.run_table, 1)
        
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

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(7)
        self.history_table.setHorizontalHeaderLabels([
            "Session ID", "Session Name", "Benchmark", "Version", "Score", "Timestamp", "Flags"
        ])
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.history_table, 1)

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
        layout.addLayout(ctrl_layout)

        return tab
    
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
        
        self._scan_worker.start()

    def closeEvent(self, event):
        """Clean shutdown: wait for or cancel workers before closing."""
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
        event.accept()
    
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
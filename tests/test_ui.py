"""Tests for UI components, background BenchmarkWorker, and MainWindow."""

import sys
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.ui.main_window import BenchmarkWorker, MainWindow
from app.benchmark_manager import BenchmarkManager, BenchmarkInfo, BenchmarkStatus, BenchmarkType


@pytest.fixture(scope="session")
def qapp():
    """Ensure QApplication is initialized for UI tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestBenchmarkWorker:
    """Test BenchmarkWorker background thread functionality."""
    
    def test_worker_class_structure(self, qapp):
        """Verify BenchmarkWorker is a standalone QThread class at module level."""
        manager = BenchmarkManager()
        worker = BenchmarkWorker(manager)
        assert worker.benchmark_manager is manager
        assert hasattr(worker, 'scan_started')
        assert hasattr(worker, 'scan_finished')
        assert hasattr(worker, 'scan_error')
        assert hasattr(worker, 'progress')
        assert worker.is_running() is False
    
    def test_worker_calls_scan_only(self, qapp):
        """Verify worker calls scan() only on BenchmarkManager and emits signals."""
        mock_manager = Mock(spec=BenchmarkManager)
        mock_benchmarks = [
            BenchmarkInfo('geekbench_6', 'Geekbench 6', BenchmarkType.CPU, BenchmarkStatus.READY_MANUAL),
            BenchmarkInfo('geekbench_7', 'Geekbench 7', BenchmarkType.CPU, BenchmarkStatus.READY_MANUAL)
        ]
        mock_manager.scan.return_value = mock_benchmarks
        
        worker = BenchmarkWorker(mock_manager)
        
        finished_results = []
        worker.scan_finished.connect(lambda benches: finished_results.extend(benches))
        
        # Execute run() synchronously to verify logic
        worker.run()
        
        # Must call scan() only, never any other discovery method directly
        mock_manager.scan.assert_called_once()
        assert len(finished_results) == 2
        assert finished_results[0].name == 'geekbench_6'


class TestMainWindowUI:
    """Test MainWindow layout, worker referencing, and screen refresh behaviors."""
    
    @patch.object(MainWindow, 'start_scan')
    def test_main_window_attributes(self, mock_scan, qapp):
        """Verify shared BenchmarkManager instance and background worker attribute."""
        mock_db = Mock()
        mock_db.get_all_results.return_value = []
        sys_info = {'cpu_brand': 'Test CPU', 'core_count': 8, 'ram_size_gb': 16, 'gpu_name': 'Test GPU'}
        
        window = MainWindow(mock_db, sys_info)
        
        # Must have shared benchmark_manager instance attribute
        assert hasattr(window, 'benchmark_manager')
        assert isinstance(window.benchmark_manager, BenchmarkManager)
        
        # Must keep background scan worker referenced as an attribute
        assert hasattr(window, '_scan_worker')
        assert window._scan_worker is None
        
        # UI controls
        assert window.rescan_btn is not None
        assert window.status_label is not None
        assert window.library_table is not None
        assert window.run_table is not None
        
        mock_scan.assert_called_once()
        window.close()
    
    @patch('app.ui.main_window.BenchmarkWorker')
    def test_start_scan_scanning_state(self, mock_worker_cls, qapp):
        """Verify 'Scanning...' state and worker referencing when start_scan is called."""
        mock_db = Mock()
        mock_db.get_all_results.return_value = []
        
        # Mock worker
        mock_worker = Mock()
        mock_worker.isRunning.return_value = False
        mock_worker_cls.return_value = mock_worker
        
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})
        
        # Trigger real start_scan
        window.start_scan()
        
        assert window.rescan_btn.isEnabled() is False
        assert window.rescan_btn.text() == "Scanning..."
        assert "Scanning" in window.status_label.text()
        assert window._scan_worker is mock_worker
        mock_worker.start.assert_called_once()
        window.close()
    
    def test_library_screen_consolidates_geekbench(self, qapp):
        """Verify Library table groups all detected Geekbench majors into one 'Geekbench' card/row."""
        mock_db = Mock()
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})
        
        # Populate benchmark_manager with Geekbench 6, Geekbench 7, Cinebench, and Speedometer
        window.benchmark_manager.benchmarks = [
            BenchmarkInfo(
                name='geekbench_6',
                display_name='Geekbench 6',
                type=BenchmarkType.CPU,
                status=BenchmarkStatus.READY_MANUAL,
                executable_path=r'C:\Program Files (x86)\Geekbench 6\Geekbench 6.exe',
                version='6.7.1.0',
                architecture='x64',
                detection_method='registry, filesystem pattern'
            ),
            BenchmarkInfo(
                name='geekbench_7',
                display_name='Geekbench 7',
                type=BenchmarkType.CPU,
                status=BenchmarkStatus.READY_MANUAL,
                executable_path=r'C:\Program Files\Geekbench 7\Geekbench 7.exe',
                version='7.1.0.0',
                architecture='x64',
                detection_method='registry, filesystem pattern, Start Menu'
            ),
            BenchmarkInfo(
                name='cinebench',
                display_name='Cinebench',
                type=BenchmarkType.CPU,
                status=BenchmarkStatus.READY_MANUAL,
                executable_path=None,
                version='2026.1.3.0',
                architecture='X64',
                appx_package={'app_id': 'MAXONComputerGmbH.Cinebench_rsne5bsk8s7tj!Cinebench'},
                detection_method='Appx'
            ),
            BenchmarkInfo(
                name='speedometer',
                display_name='Speedometer 3.1',
                type=BenchmarkType.WEB,
                status=BenchmarkStatus.READY_MANUAL,
                version='3.1',
                detection_method='browser_detection'
            )
        ]
        
        window.refresh_library_screen()
        
        # Should have 3 rows: 1 consolidated Geekbench row, 1 Cinebench row, 1 Speedometer row
        assert window.library_table.rowCount() == 3
        
        # First row is consolidated Geekbench
        gb_name = window.library_table.item(0, 0).text()
        assert gb_name == "Geekbench"
        
        gb_status = window.library_table.item(0, 2).text()
        assert "Geekbench 6: assisted/manual" in gb_status
        assert "Geekbench 7: assisted/manual" in gb_status
        
        gb_ver = window.library_table.item(0, 3).text()
        assert "Geekbench 6: 6.7.1.0" in gb_ver
        assert "Geekbench 7: 7.1.0.0" in gb_ver
        
        gb_arch = window.library_table.item(0, 4).text()
        assert "Geekbench 6: x64" in gb_arch
        assert "Geekbench 7: x64" in gb_arch
        
        gb_path = window.library_table.item(0, 6).text()
        assert r"C:\Program Files (x86)\Geekbench 6\Geekbench 6.exe" in gb_path
        assert r"C:\Program Files\Geekbench 7\Geekbench 7.exe" in gb_path
        
        # Other rows are Cinebench and Speedometer
        assert window.library_table.item(1, 0).text() == "Cinebench"
        assert window.library_table.item(2, 0).text() == "Speedometer 3.1"
        
        window.close()
    
    def test_run_screen_refresh(self, qapp):
        """Verify Run screen lists runnable benchmarks with Launch buttons."""
        mock_db = Mock()
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})
        
        window.benchmark_manager.benchmarks = [
            BenchmarkInfo(
                name='geekbench_6',
                display_name='Geekbench 6',
                type=BenchmarkType.CPU,
                status=BenchmarkStatus.READY_MANUAL,
                executable_path=r'C:\Program Files (x86)\Geekbench 6\Geekbench 6.exe',
                version='6.7.1.0'
            ),
            BenchmarkInfo(
                name='geekbench_7',
                display_name='Geekbench 7',
                type=BenchmarkType.CPU,
                status=BenchmarkStatus.READY_MANUAL,
                executable_path=r'C:\Program Files\Geekbench 7\Geekbench 7.exe',
                version='7.1.0.0'
            ),
            BenchmarkInfo(
                name='d3dmark',
                display_name='3DMark',
                type=BenchmarkType.GPU,
                status=BenchmarkStatus.NOT_INSTALLED
            )
        ]
        
        window.refresh_run_screen()
        
        # Only runnable benchmarks (Geekbench 6 and Geekbench 7) appear
        assert window.run_table.rowCount() == 2
        assert window.run_table.item(0, 0).text() == "Geekbench 6"
        assert window.run_table.item(1, 0).text() == "Geekbench 7"
        
        # Launch buttons are embedded
        btn0 = window.run_table.cellWidget(0, 4)
        btn1 = window.run_table.cellWidget(1, 4)
        assert btn0 is not None
        assert "Launch Geekbench 6" in btn0.text()
        assert btn1 is not None
        assert "Launch Geekbench 7" in btn1.text()
        
        window.close()

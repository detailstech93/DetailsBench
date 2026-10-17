"""Tests for UI components, background BenchmarkWorker, and MainWindow."""

import sys
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.ui.main_window import BenchmarkWorker, MainWindow
from app.benchmark_manager import BenchmarkManager, BenchmarkInfo, BenchmarkStatus, BenchmarkType
from app.discovery.result import DiscoveryResult


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


class TestWebLaunchWorkerProcessSnapshot:
    """Test _WebLaunchWorker uses real _has_new_process / _get_process_snapshot
    comparison code with fake process snapshots."""

    def test_web_launch_worker_browser_already_running(self, qapp):
        """MOCK: browser already running -> unconfirmed, not failed."""
        from app.ui.main_window import _WebLaunchWorker

        bm = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
        )
        worker = _WebLaunchWorker(
            bm, 'https://example.com/benchmark-page/',
            '3.1',
            browser_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            browser_name='Chrome',
        )

        # Capture the finished signal via a connected callable
        finished_results = []
        worker.finished.connect(lambda result: finished_results.append(result))

        # Fake snapshots: chrome.exe already exists before AND after launch
        before_snapshot = [
            ('chrome.exe', r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
            ('svchost.exe', None),
        ]
        after_snapshot = [
            ('chrome.exe', r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
            ('svchost.exe', None),
        ]

        call_count = [0]
        def fake_snapshot():
            call_count[0] += 1
            return before_snapshot if call_count[0] == 1 else after_snapshot

        with patch('subprocess.Popen'):
            with patch('app.benchmark_manager._get_process_snapshot', side_effect=fake_snapshot):
                worker.run()

        # Launch call returned without exception -> status "launched"
        assert len(finished_results) == 1
        result = finished_results[0]
        assert result['status'] == 'launched'
        assert 'Launch command accepted; page load not verified' in result['message']
        assert result['elevation_used'] is False

    def test_web_launch_worker_launch_raises_exception(self, qapp):
        """Signal-level test: launch raises exception -> status 'error'."""
        from app.ui.main_window import _WebLaunchWorker

        bm = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
        )
        worker = _WebLaunchWorker(
            bm, 'https://example.com/benchmark-page/',
            '3.1',
            browser_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            browser_name='Chrome',
        )

        finished_results = []
        worker.finished.connect(lambda result: finished_results.append(result))

        with patch('subprocess.Popen', side_effect=OSError('access denied')):
            with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                worker.run()

        assert len(finished_results) == 1
        result = finished_results[0]
        assert result['status'] == 'error'
        assert 'Could not launch' in result['message']
        assert 'access denied' in result['message']
        assert result['elevation_used'] is False

    def test_web_launch_worker_user_cancelled(self, qapp):
        """Signal-level test: user cancelled -> status 'cancelled'."""
        from app.ui.main_window import _WebLaunchWorker

        bm = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
        )
        worker = _WebLaunchWorker(
            bm, 'https://example.com/benchmark-page/',
            '3.1',
        )

        finished_results = []
        worker.finished.connect(lambda result: finished_results.append(result))

        # User cancels before launch
        worker.cancel()
        worker.run()

        assert len(finished_results) == 1
        result = finished_results[0]
        assert result['status'] == 'cancelled'
        assert 'cancelled' in result['message'].lower()
        assert result['elevation_used'] is False

    def test_web_launch_worker_no_browser_path_launched(self, qapp):
        """Signal-level test: no browser_path -> still 'launched' (not 'unconfirmed')."""
        from app.ui.main_window import _WebLaunchWorker

        bm = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
        )
        worker = _WebLaunchWorker(
            bm, 'https://example.com/benchmark-page/',
            '3.1',
            browser_path=None,
            browser_name=None,
        )

        finished_results = []
        worker.finished.connect(lambda result: finished_results.append(result))

        with patch('webbrowser.open'):
            with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                worker.run()

        assert len(finished_results) == 1
        result = finished_results[0]
        assert result['status'] == 'launched'
        assert 'Launch command accepted; page load not verified' in result['message']

    def test_web_launch_worker_webbrowser_open_false(self, qapp):
        """Signal-level test: webbrowser.open False -> status 'error'."""
        from app.ui.main_window import _WebLaunchWorker

        bm = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
        )
        worker = _WebLaunchWorker(
            bm, 'https://example.com/benchmark-page/',
            '3.1',
            browser_path=None,
            browser_name=None,
        )

        finished_results = []
        worker.finished.connect(lambda result: finished_results.append(result))

        with patch('subprocess.Popen'), \
             patch('webbrowser.open', return_value=False), \
             patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
            worker.run()

        assert len(finished_results) == 1
        result = finished_results[0]
        assert result['status'] == 'error'
        assert 'no browser could be opened' in result['message']
        assert result['elevation_used'] is False

    def test_web_launch_worker_diagnostic_snapshot_failure_does_not_change_result(self, qapp):
        """Signal-level test: diagnostic snapshot failure -> status 'launched'."""
        from app.ui.main_window import _WebLaunchWorker

        bm = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
        )
        worker = _WebLaunchWorker(
            bm, 'https://example.com/benchmark-page/',
            '3.1',
            browser_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            browser_name='Chrome',
        )

        finished_results = []
        worker.finished.connect(lambda result: finished_results.append(result))

        with patch('subprocess.Popen'), \
             patch('webbrowser.open'), \
             patch('app.benchmark_manager._get_process_snapshot', side_effect=RuntimeError('boom')):
            worker.run()

        assert len(finished_results) == 1
        result = finished_results[0]
        assert result['status'] == 'launched'
        assert 'Launch command accepted' in result['message']
        assert result['elevation_used'] is False


class TestScanErrorUIDisplay:
    """Test that SCAN_ERROR benchmarks display correctly in the Library screen."""

    def test_scan_error_shows_could_not_check_text(self, qapp):
        """SCAN_ERROR benchmarks must show 'could not check: <reason>' in status column."""
        mock_db = Mock()
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})

        window.benchmark_manager.benchmarks = [
            BenchmarkInfo(
                name='speedometer',
                display_name='Speedometer',
                type=BenchmarkType.WEB,
                status=BenchmarkStatus.SCAN_ERROR,
                reason='browser check failed: connection timeout',
                version=None,
                detection_method='error',
            ),
        ]

        window.refresh_library_screen()

        # Status column must show 'could not check: browser check failed: connection timeout'
        status_text = window.library_table.item(0, 2).text()
        assert 'could not check:' in status_text
        assert 'browser check failed: connection timeout' in status_text

        # Must NOT show raw 'scan_error' value
        assert status_text != 'scan_error'

        # Must NOT be marked as ready
        assert window.library_table.item(0, 0).text() == 'Speedometer'

        window.close()

    def test_scan_error_status_color_is_red(self, qapp):
        """SCAN_ERROR status cell must have red foreground color."""
        mock_db = Mock()
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})

        window.benchmark_manager.benchmarks = [
            BenchmarkInfo(
                name='jetstream',
                display_name='JetStream',
                type=BenchmarkType.WEB,
                status=BenchmarkStatus.SCAN_ERROR,
                reason='browser check failed',
            ),
        ]

        window.refresh_library_screen()

        # Check the foreground color of the status cell
        status_item = window.library_table.item(0, 2)
        fg_color = status_item.foreground().color().name()
        # Should be red (#C62828), not gray (#757575) or green (#2E7D32)
        assert fg_color.lower() == '#c62828', f"Expected red, got {fg_color}"

        window.close()

    def test_scan_error_not_in_run_screen(self, qapp):
        """SCAN_ERROR benchmarks must not appear in the Run screen."""
        mock_db = Mock()
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})

        window.benchmark_manager.benchmarks = [
            BenchmarkInfo(
                name='speedometer',
                display_name='Speedometer',
                type=BenchmarkType.WEB,
                status=BenchmarkStatus.SCAN_ERROR,
                reason='browser check failed',
            ),
            BenchmarkInfo(
                name='geekbench_6',
                display_name='Geekbench 6',
                type=BenchmarkType.CPU,
                status=BenchmarkStatus.READY_MANUAL,
                executable_path=r'C:\Program Files\Geekbench 6\Geekbench 6.exe',
                version='6.7.1.0',
            ),
        ]

        window.refresh_run_screen()

        # Only READY_MANUAL should appear
        assert window.run_table.rowCount() == 1
        assert window.run_table.item(0, 0).text() == 'Geekbench 6'

        window.close()


class TestStatusLabel:
    """_status_label: text shown in the Library status column."""

    @pytest.mark.parametrize('status, reason, expected', [
        (BenchmarkStatus.UNSUPPORTED, 'WSL is present', 'Unsupported on this system'),
        (BenchmarkStatus.READY_MANUAL, None, 'assisted/manual'),
        (BenchmarkStatus.SCAN_ERROR, 'boom', 'could not check: boom'),
        (BenchmarkStatus.NOT_INSTALLED, None, 'not_installed'),
    ])
    def test_status_label(self, status, reason, expected):
        from app.ui.main_window import _status_label
        bm = BenchmarkInfo(name='glmark2', display_name='glmark2',
                           type=BenchmarkType.GPU, status=status, reason=reason)
        assert _status_label(bm) == expected


class TestEnvironmentProfileUI:
    """MOCK: fake profiles into the REAL worker, Dashboard and snapshot slot."""

    def _profile(self):
        from app.environment.profile import EnvironmentProfile, detected, unavailable, unverified
        p = EnvironmentProfile()
        p.os_name = detected('Windows 11 Professional', 'build')
        p.gpu = detected(['Intel(R) Graphics', 'NVIDIA GeForce RTX 5090 Laptop GPU'], 'cim')
        p.vulkan = unverified('Vulkan loader present', 'dll')
        p.app_is_elevated = detected(False, 'IsUserAnAdmin')
        p.display_server = unavailable('not Linux')
        return p

    def test_worker_builds_profile_and_passes_it_to_scan(self, qapp):
        manager = Mock()
        manager.scan.return_value = []
        worker = BenchmarkWorker(manager)
        profiles, finished = [], []
        worker.profile_ready.connect(profiles.append)
        worker.scan_finished.connect(finished.append)
        profile = self._profile()
        with patch('app.ui.main_window.select_adapter') as sel,                 patch('app.ui.main_window.discover_installed', return_value=DiscoveryResult()):
            sel.return_value.build_profile.return_value = profile
            worker.run()
        assert profiles == [profile]
        manager.scan.assert_called_once_with(profile)
        assert finished == [[]]

    def test_worker_scans_without_profile_when_profile_fails(self, qapp):
        manager = Mock()
        manager.scan.return_value = []
        worker = BenchmarkWorker(manager)
        profiles, finished = [], []
        worker.profile_ready.connect(profiles.append)
        worker.scan_finished.connect(finished.append)
        with patch('app.ui.main_window.select_adapter', side_effect=RuntimeError('boom')),                 patch('app.ui.main_window.discover_installed', side_effect=RuntimeError('boom')):
            worker.run()
        assert profiles == []
        manager.scan.assert_called_once_with(None)
        assert finished == [[]]

    def test_dashboard_shows_values_with_status(self, qapp):
        mock_db = Mock()
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})
        assert window.env_value_labels['os_name'].text() == 'Scanning...'
        window._on_profile_ready(self._profile())
        labels = window.env_value_labels
        assert labels['os_name'].text() == 'Windows 11 Professional   [detected]'
        assert labels['gpu'].text().startswith('Intel(R) Graphics, NVIDIA GeForce RTX 5090')
        assert labels['vulkan'].text().endswith('[unverified]')
        assert labels['app_is_elevated'].text() == 'no   [detected]'
        assert labels['display_server'].text() == 'not available   [unavailable]'
        assert labels['os_name'].toolTip() == 'source: build'
        mock_db.save_environment_snapshot.assert_called_once()
        assert mock_db.save_environment_snapshot.call_args[0][0]['os_name']['value'] == 'Windows 11 Professional'
        window.close()

    def test_snapshot_save_failure_does_not_break_ui(self, qapp):
        mock_db = Mock()
        mock_db.save_environment_snapshot.side_effect = RuntimeError('db locked')
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})
        window._on_profile_ready(self._profile())
        assert window.env_value_labels['os_name'].text().startswith('Windows 11')
        window.close()


class TestDetectedToolsUI:
    """MOCK: a fake DiscoveryResult into the REAL worker signal and Library view."""

    def _result(self):
        return DiscoveryResult(
            software=[{"id": "steam", "display_name": "Steam", "kind": "launcher", "name": "Steam",
                       "version": "2.10.91.91", "path": None, "source": "registry HKLM (32-bit)"}],
            games=[{"store": "epic", "app_id": "Fortnite", "name": "Fortnite",
                    "install_path": r"D:\Games\Fortnite", "version": None}],
            stores={"steam": {"status": "detected", "path": r"C:\Steam", "source": "registry"},
                    "epic": {"status": "not detected", "path": None, "source": "manifests"}},
        )

    def test_worker_emits_discovery(self, qapp):
        manager = Mock(); manager.scan.return_value = []
        worker = BenchmarkWorker(manager)
        got = []
        worker.discovery_ready.connect(got.append)
        result = self._result()
        with patch('app.ui.main_window.select_adapter', side_effect=RuntimeError('x')),                 patch('app.ui.main_window.discover_installed', return_value=result):
            worker.run()
        assert got == [result]

    def test_library_shows_tools_games_and_saves_with_env_link(self, qapp):
        mock_db = Mock()
        mock_db.save_environment_snapshot.return_value = 7
        with patch.object(MainWindow, 'start_scan'):
            window = MainWindow(mock_db, {})
        from app.environment.profile import EnvironmentProfile
        window._on_profile_ready(EnvironmentProfile())
        window._on_discovery_ready(self._result())
        table = window.tools_table
        assert table.rowCount() == 2
        assert [table.item(0, c).text() for c in range(5)] ==             ["launcher", "Steam", "2.10.91.91", "-", "registry HKLM (32-bit)"]
        assert table.item(1, 0).text() == "game (epic)"
        assert "epic: not detected" in window.stores_label.text()
        assert "steam: detected" in window.stores_label.text()
        mock_db.save_discovery_snapshot.assert_called_once()
        assert mock_db.save_discovery_snapshot.call_args[0][1] == 7
        window.close()

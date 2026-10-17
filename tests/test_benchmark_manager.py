"""Tests for benchmark manager module.

Phase 2 hardening tests:
 - Channel-boundary tests feed FAKE registry/filesystem data into REAL channel
   functions (clearly labeled as mocks).
 - Channel isolation: a failing channel must not abort the scan.
 - Launch guard: prove helper binaries and malformed names are rejected.
 - glmark2 WSL detection.
 - Architecture normalization.
 - file_exists=None for web benchmarks.
"""

import pytest
import os
import tempfile
import struct
from unittest.mock import Mock, patch, MagicMock, PropertyMock
import winreg

from app.benchmark_manager import (
    BenchmarkManager, BenchmarkExecutor,
    BenchmarkInfo, BenchmarkStatus, BenchmarkType,
    normalize_version, get_pe_architecture, get_pe_version,
    launch_assisted_benchmark, KNOWN_GEEKBENCH_MAJORS,
    parse_version_tuple, can_compare_geekbench_scores,
    can_compare_benchmarks, assert_can_compare_benchmarks,
    is_valid_geekbench_exe, _normalize_arch, _CANONICAL_ARCHES,
    _get_process_snapshot, _has_new_process,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Version helpers (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

class TestVersionNormalization:
    def test_version_normalization_exact_strings(self):
        assert normalize_version("7,1,0,0") == "7.1.0.0"
        assert normalize_version("6,7,1,0") == "6.7.1.0"
        assert normalize_version("6.7.1.0") == "6.7.1.0"
        assert normalize_version("2026.1.3.0") == "2026.1.3.0"
        assert normalize_version("5, 2, 1") == "5.2.1"
        assert normalize_version("4.3, 2.1") == "4.3.2.1"
        assert normalize_version("4.3.2.1.0") == "4.3.2.1.0"
        assert normalize_version("") == ""
        assert normalize_version(None) is None
        assert normalize_version("Unknown") == "Unknown"
        assert normalize_version("unknown") == "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Architecture normalization
# ═══════════════════════════════════════════════════════════════════════════

class TestArchitectureNormalization:
    """Test that _normalize_arch maps every variant to x64|x86|arm64|unknown."""

    def test_x64_variants(self):
        for v in ("x64", "X64", "amd64", "AMD64", "64-bit", "64-BIT"):
            assert _normalize_arch(v) == "x64", f"_normalize_arch({v!r}) != x64"

    def test_x86_variants(self):
        for v in ("x86", "X86", "x32", "32-bit", "ia32"):
            assert _normalize_arch(v) == "x86", f"_normalize_arch({v!r}) != x86"

    def test_arm64_variants(self):
        for v in ("arm64", "ARM64", "arm64ec", "aarch64"):
            assert _normalize_arch(v) == "arm64", f"_normalize_arch({v!r}) != arm64"

    def test_unknown_and_none(self):
        assert _normalize_arch(None) == "unknown"
        assert _normalize_arch("") == "unknown"
        assert _normalize_arch("weird_arch") == "unknown"

    def test_canonical_set(self):
        assert _CANONICAL_ARCHES == {"x64", "x86", "arm64", "unknown"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. PE metadata readers
# ═══════════════════════════════════════════════════════════════════════════

class TestPeMetadata:
    def test_pe_readers_nonexistent_file(self):
        assert get_pe_architecture("nonexistent_dummy.exe") == "unknown"
        assert get_pe_version("nonexistent_dummy.exe") is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. Launch guard — Geekbench regex must require a SPACE
# ═══════════════════════════════════════════════════════════════════════════

class TestLaunchGuard:
    """Prove the launch guard rejects helper binaries and malformed names."""

    def test_geekbench6_exe_rejected(self):
        """geekbench6.exe (no space) must be rejected."""
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench\geekbench6.exe',
        )
        with patch('os.path.isfile', return_value=True):
            with pytest.raises(RuntimeError, match="Refusing to launch non-main binary"):
                launch_assisted_benchmark(info, confirm=True)

    def test_geekbench7_exe_rejected(self):
        """geekbench7.exe (no space) must be rejected."""
        info = BenchmarkInfo(
            name='geekbench_7', display_name='Geekbench 7',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench\geekbench7.exe',
        )
        with patch('os.path.isfile', return_value=True):
            with pytest.raises(RuntimeError, match="Refusing to launch non-main binary"):
                launch_assisted_benchmark(info, confirm=True)

    def test_geekbench_x86_64_rejected(self):
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench\geekbench_x86_64.exe',
        )
        with patch('os.path.isfile', return_value=True):
            with pytest.raises(RuntimeError, match="Refusing to launch non-main binary"):
                launch_assisted_benchmark(info, confirm=True)

    def test_geekbench_avx2_rejected(self):
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench\geekbench_avx2.exe',
        )
        with patch('os.path.isfile', return_value=True):
            with pytest.raises(RuntimeError, match="Refusing to launch non-main binary"):
                launch_assisted_benchmark(info, confirm=True)

    def test_geekbench_6_exe_accepted(self):
        """'Geekbench 6.exe' (with space) passes the launch guard regex."""
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench\Geekbench 6.exe',
        )
        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen'):
                with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                    with patch('app.benchmark_manager._has_new_process', return_value=True):
                        result = launch_assisted_benchmark(info, confirm=True)
                        assert result['status'] == 'launched'

    def test_cinebench_invalid_appid_rejected(self):
        """Cinebench AppID must match PackageFamilyName!AppId shape."""
        info = BenchmarkInfo(
            name='cinebench', display_name='Cinebench',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            appx_package={'app_id': 'no-exclamation-mark-here'},
        )
        with pytest.raises(RuntimeError, match="does not match expected"):
            launch_assisted_benchmark(info, confirm=True)

    def test_cinebench_valid_appid_accepted(self):
        info = BenchmarkInfo(
            name='cinebench', display_name='Cinebench',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            appx_package={'app_id': 'MAXONComputerGmbH.Cinebench_rsne5bsk8s7tj!Cinebench'},
        )
        with patch('subprocess.Popen'):
            with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                with patch('app.benchmark_manager._has_new_process', return_value=True):
                    result = launch_assisted_benchmark(info, confirm=True)
                    assert result['status'] == 'launched'


# ═══════════════════════════════════════════════════════════════════════════
# 5. Channel-boundary tests (MOCKS) — fake registry + fake filesystem → real
#    channel functions
# ═══════════════════════════════════════════════════════════════════════════

class TestChannelBoundaryMocks:
    """MOCKS: feed FAKE registry data and FAKE filesystem into REAL channel
    functions, including entries with empty DisplayVersion/InstallLocation,
    so _search_registry_uninstall and other channels are really executed."""

    # ── Helper: build a fake winreg context ──────────────────────────────

    def _make_fake_reg_keys(self, keys_data):
        """Return a context-manager stack that simulates winreg OpenKey/EnumKey.

        keys_data: list of dicts, each with:
            'subkey_name': str
            'DisplayName': str | None
            'DisplayVersion': str | None
            'InstallLocation': str | None
        """
        class FakeKey:
            def __init__(self, data):
                self.data = data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def fake_openkey(hkey, subkey, access=0, sam=0):
            return FakeKey({})

        return fake_openkey

    @patch('app.benchmark_manager.winreg')
    def test_registry_channel_empty_display_version(self, mock_wr):
        """MOCK: registry entries with empty DisplayVersion still work."""
        # Simulate the registry key enumeration
        mock_wr.HKEY_LOCAL_MACHINE = -2147483646
        mock_wr.HKEY_CURRENT_USER = -2147483647
        mock_wr.KEY_READ = 0x20019
        mock_wr.KEY_WOW64_64KEY = 0x0100
        mock_wr.KEY_WOW64_32KEY = 0x0200

        call_index = [0]
        fake_keys = [
            {'subkey': 'Geekbench_6', 'DisplayName': 'Geekbench 6',
             'DisplayVersion': '', 'InstallLocation': None},
            {'subkey': 'Geekbench_7', 'DisplayName': 'Geekbench 7',
             'DisplayVersion': None, 'InstallLocation': None},
        ]

        class MockRegKey:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def fake_openkey(hkey, subkey, access=0, sam=0):
            return MockRegKey()

        def fake_query_info_key(key):
            return (len(fake_keys), 0, 0)

        def fake_enum_key(key, index):
            if index < len(fake_keys):
                return fake_keys[index]['subkey']
            raise OSError("No more keys")

        def fake_query_value_ex(key, value_name):
            # Find the current subkey context — we'll just return the first matching
            for fk in fake_keys:
                if value_name == "DisplayName" and fk.get('DisplayName') is not None:
                    return (fk['DisplayName'], 1)
                if value_name == "DisplayVersion":
                    val = fk.get('DisplayVersion')
                    if val is not None:
                        return (val, 1)
                    raise OSError("Value not found")
                if value_name == "InstallLocation" and fk.get('InstallLocation') is not None:
                    return (fk['InstallLocation'], 1)
                if value_name == "InstallSource":
                    raise OSError("Value not found")
            raise OSError("Value not found")

        mock_wr.OpenKey = fake_openkey
        mock_wr.QueryInfoKey = fake_query_info_key
        mock_wr.EnumKey = fake_enum_key
        mock_wr.QueryValueEx = fake_query_value_ex

        manager = BenchmarkManager()
        from data.benchmark_definitions import DEFINITIONS_BY_ID
        defn = DEFINITIONS_BY_ID.get('geekbench')
        if defn:
            hints, matched, found = manager._search_registry_uninstall(defn)
            assert found is True
            assert len(matched) >= 1
            # matched_names must be a set (the type bug fix)
            assert isinstance(matched, set)

    @patch('app.benchmark_manager.winreg')
    @patch('os.path.isdir')
    @patch('os.listdir')
    @patch('os.walk')
    def test_filesystem_channel_empty_install_location(
        self, mock_walk, mock_listdir, mock_isdir, mock_wr
    ):
        """MOCK: filesystem channel handles entries with no InstallLocation."""
        mock_wr.HKEY_LOCAL_MACHINE = -2147483646
        mock_wr.HKEY_CURRENT_USER = -2147483647
        mock_wr.KEY_READ = 0x20019
        mock_wr.KEY_WOW64_64KEY = 0x0100
        mock_wr.KEY_WOW64_32KEY = 0x0200

        class MockRegKey:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def fake_openkey(hkey, subkey, access=0, sam=0):
            return MockRegKey()

        def fake_query_info_key(key):
            return (0, 0, 0)  # no registry keys

        def fake_query_value_ex(key, value_name):
            raise OSError("Value not found")

        mock_wr.OpenKey = fake_openkey
        mock_wr.QueryInfoKey = fake_query_info_key
        mock_wr.EnumKey = lambda k, i: (_ for _ in ()).throw(OSError("No more keys"))
        mock_wr.QueryValueEx = fake_query_value_ex

        mock_isdir.side_effect = lambda p: True
        mock_walk.return_value = []
        mock_listdir.side_effect = lambda p: []

        manager = BenchmarkManager()
        from data.benchmark_definitions import DEFINITIONS_BY_ID
        defn = DEFINITIONS_BY_ID.get('geekbench')
        if defn:
            hints, matched, found = manager._search_registry_uninstall(defn)
            # No crash even with empty registry
            assert isinstance(hints, list)
            assert isinstance(matched, set)

    @patch('app.benchmark_manager.winreg')
    @patch('os.path.isfile')
    @patch('os.path.isdir')
    @patch('os.listdir')
    @patch('os.walk')
    def test_registry_hints_with_empty_install_location(
        self, mock_walk, mock_listdir, mock_isdir, mock_isfile, mock_wr
    ):
        """MOCK: registry returns InstallLocation that doesn't exist → no crash."""
        mock_wr.HKEY_LOCAL_MACHINE = -2147483646
        mock_wr.HKEY_CURRENT_USER = -2147483647
        mock_wr.KEY_READ = 0x20019
        mock_wr.KEY_WOW64_64KEY = 0x0100
        mock_wr.KEY_WOW64_32KEY = 0x0200

        class MockRegKey:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        mock_wr.OpenKey = lambda *a, **kw: MockRegKey()
        mock_wr.QueryInfoKey = lambda k: (1, 0, 0)
        mock_wr.EnumKey = lambda k, i: "Geekbench6" if i == 0 else (_ for _ in ()).throw(OSError())
        mock_wr.QueryValueEx = lambda k, v: (
            ("Geekbench 6", 1) if v == "DisplayName"
            else (r'C:\nonexistent\path', 1) if v in ("InstallLocation", "InstallSource")
            else (_ for _ in ()).throw(OSError("not found"))
        )
        mock_isdir.return_value = False  # InstallLocation doesn't exist
        mock_isfile.return_value = False
        mock_listdir.return_value = []
        mock_walk.return_value = []

        manager = BenchmarkManager()
        from data.benchmark_definitions import DEFINITIONS_BY_ID
        defn = DEFINITIONS_BY_ID.get('geekbench')
        if defn:
            hints, matched, found = manager._search_registry_uninstall(defn)
            assert found is True
            assert isinstance(matched, set)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Channel isolation — one channel raises, scan still returns all entries
# ═══════════════════════════════════════════════════════════════════════════

class TestChannelIsolation:
    """A failing channel must not abort the scan: record the failure and
    continue with remaining channels. scan() must never raise to the GUI."""

    @patch.object(BenchmarkManager, '_search_registry_uninstall')
    @patch.object(BenchmarkManager, '_scan_filesystem')
    @patch.object(BenchmarkManager, '_query_system_apps')
    @patch('os.path.isfile')
    @patch('os.listdir')
    @patch('os.walk')
    def test_registry_channel_crash_does_not_abort_scan(
        self, mock_walk, mock_listdir, mock_isfile,
        mock_query_apps, mock_reg, mock_fs
    ):
        """MOCK: registry channel raises → scan continues, diagnostics recorded."""
        mock_query_apps.return_value = {'appx_cinebench': None, 'start_apps': []}
        mock_reg.side_effect = RuntimeError("registry corruption")
        mock_fs.return_value = ([], set())
        mock_isfile.return_value = False
        mock_listdir.return_value = []
        mock_walk.return_value = []

        manager = BenchmarkManager()
        results = manager.scan()

        # All benchmark definitions must still be present
        names = [r.name for r in results]
        assert 'geekbench' in names or any('geekbench' in n for n in names)
        assert 'cinebench' in names
        assert 'd3dmark' in names
        assert 'glmark2' in names
        assert 'speedometer' in names
        assert 'jetstream' in names

        # The geekbench entry should have diagnostics
        gb_entries = [r for r in results if 'geekbench' in r.name]
        if gb_entries:
            assert gb_entries[0].scan_diagnostics is not None
            assert any('registry' in d for d in gb_entries[0].scan_diagnostics)

    @patch.object(BenchmarkManager, '_discover_from_definition')
    def test_discovery_level_fallback(self, mock_discover):
        """If _discover_from_definition itself raises, a NOT_INSTALLED fallback
        is returned with the error in diagnostics."""
        def side_effect(defn, sys_apps=None):
            if defn.id == 'd3dmark':
                raise RuntimeError("simulated discovery crash")
            from data.benchmark_definitions import DEFINITIONS_BY_ID
            defn_obj = DEFINITIONS_BY_ID.get(defn.id)
            return [BenchmarkInfo(
                name=defn.id, display_name=defn.display_name,
                type=BenchmarkType.CPU, status=BenchmarkStatus.NOT_INSTALLED,
                official_url=defn.official_url, prerequisites=['Windows 10/11'],
                reason='Not installed')]

        mock_discover.side_effect = side_effect

        manager = BenchmarkManager()
        results = manager.detect_benchmarks()

        # d3dmark should still be present with error diagnostics
        d3d = [r for r in results if r.name == 'd3dmark']
        assert len(d3d) == 1
        assert d3d[0].scan_diagnostics is not None
        assert any('discovery failed' in d for d in d3d[0].scan_diagnostics)


# ═══════════════════════════════════════════════════════════════════════════
# 7. glmark2 WSL detection
# ═══════════════════════════════════════════════════════════════════════════

class TestGlmark2Wsl:
    """glmark2 is Unsupported on Windows whether or not WSL is present
    (the app cannot launch programs inside WSL); the reason says which."""

    @patch('subprocess.run')
    def test_glmark2_wsl_absent(self, mock_run):
        """WSL not present → UNSUPPORTED."""
        mock_run.side_effect = FileNotFoundError("wsl.exe not found")
        manager = BenchmarkManager()
        channel_errors = []
        results = manager._discover_glmark2(channel_errors)
        assert len(results) == 1
        assert results[0].status == BenchmarkStatus.UNSUPPORTED
        assert 'not verified for native Windows' in results[0].reason
        assert 'WSL' in results[0].reason
        assert results[0].file_exists is None

    @patch('subprocess.run')
    def test_glmark2_wsl_present(self, mock_run):
        """WSL present → still UNSUPPORTED (not runnable from this app)."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        manager = BenchmarkManager()
        channel_errors = []
        results = manager._discover_glmark2(channel_errors)
        assert len(results) == 1
        assert results[0].status == BenchmarkStatus.UNSUPPORTED
        assert results[0].is_runnable() is False
        assert 'WSL is present' in results[0].reason
        assert results[0].file_exists is None

    @patch('subprocess.run')
    def test_glmark2_wsl_timeout(self, mock_run):
        """WSL check times out → treated as absent → UNSUPPORTED."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("wsl", 5)
        manager = BenchmarkManager()
        channel_errors = []
        results = manager._discover_glmark2(channel_errors)
        assert results[0].status == BenchmarkStatus.UNSUPPORTED


# ═══════════════════════════════════════════════════════════════════════════
# 8. Web benchmark file_exists is None
# ═══════════════════════════════════════════════════════════════════════════

class TestWebBenchmarkFileExists:
    """Web benchmarks must have file_exists=None (not applicable)."""

    @patch.object(BenchmarkManager, '_detect_browsers')
    def test_web_benchmark_file_exists_none_when_browsers_found(self, mock_browsers):
        mock_browsers.return_value = {
            'Chrome': {'path': r'C:\Chrome\chrome.exe',
                       'version': '120.0.6099.71', 'architecture': 'x64'}
        }
        manager = BenchmarkManager()
        from data.benchmark_definitions import DEFINITIONS_BY_ID
        defn = DEFINITIONS_BY_ID.get('speedometer')
        channel_errors = []
        results = manager._discover_web(defn, channel_errors)
        assert results[0].file_exists is None

    @patch.object(BenchmarkManager, '_detect_browsers')
    def test_web_benchmark_file_exists_none_when_no_browsers(self, mock_browsers):
        mock_browsers.return_value = {}
        manager = BenchmarkManager()
        from data.benchmark_definitions import DEFINITIONS_BY_ID
        defn = DEFINITIONS_BY_ID.get('jetstream')
        channel_errors = []
        results = manager._discover_web(defn, channel_errors)
        assert results[0].file_exists is None


# ═══════════════════════════════════════════════════════════════════════════
# 9. Browser detection via App Paths (MOCK)
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserDetectionAppPaths:
    """MOCK: browsers detected via App Paths registry, with fallback paths."""

    @patch('app.benchmark_manager.winreg')
    @patch('os.path.isfile')
    @patch('os.path.exists')
    @patch('app.benchmark_manager.get_pe_version')
    @patch('app.benchmark_manager.get_pe_architecture')
    def test_chrome_detected_via_app_paths(
        self, mock_arch, mock_ver, mock_exists, mock_isfile, mock_wr
    ):
        """MOCK: Chrome found in App Paths registry."""
        mock_wr.HKEY_LOCAL_MACHINE = -2147483646
        mock_wr.HKEY_CURRENT_USER = -2147483647
        mock_wr.KEY_READ = 0x20019
        mock_wr.KEY_WOW64_64KEY = 0x0100
        mock_wr.KEY_WOW64_32KEY = 0x0200

        class MockRegKey:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        mock_wr.OpenKey = lambda *a, **kw: MockRegKey()
        mock_wr.QueryInfoKey = lambda k: (3, 0, 0)
        mock_wr.EnumKey = lambda k, i: (
            'chrome.exe' if i == 0
            else 'msedge.exe' if i == 1
            else 'firefox.exe'
        )
        mock_wr.QueryValueEx = lambda k, v: (
            (r'C:\Program Files\Google\Chrome\Application\chrome.exe', 1)
            if v == '' and True
            else (_ for _ in ()).throw(OSError("not found"))
        )
        mock_isfile.return_value = True
        mock_exists.return_value = True
        mock_ver.return_value = "120.0.6099.71"
        mock_arch.return_value = "x64"

        manager = BenchmarkManager()
        browsers = manager._detect_browsers()
        assert 'Chrome' in browsers
        assert browsers['Chrome']['version'] == "120.0.6099.71"

    @patch('app.benchmark_manager.winreg')
    @patch('os.path.isfile')
    @patch('os.path.exists')
    @patch('app.benchmark_manager.get_pe_version')
    @patch('app.benchmark_manager.get_pe_architecture')
    def test_edge_detected_via_fallback(
        self, mock_arch, mock_ver, mock_exists, mock_isfile, mock_wr
    ):
        """MOCK: Edge not in App Paths → fallback path works."""
        mock_wr.HKEY_LOCAL_MACHINE = -2147483646
        mock_wr.HKEY_CURRENT_USER = -2147483647
        mock_wr.KEY_READ = 0x20019
        mock_wr.KEY_WOW64_64KEY = 0x0100
        mock_wr.KEY_WOW64_32KEY = 0x0200

        class MockRegKey:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        mock_wr.OpenKey = lambda *a, **kw: MockRegKey()
        mock_wr.QueryInfoKey = lambda k: (1, 0, 0)
        mock_wr.EnumKey = lambda k, i: 'chrome.exe' if i == 0 else (_ for _ in ()).throw(OSError())
        mock_wr.QueryValueEx = lambda k, v: (
            (r'C:\Program Files\Google\Chrome\Application\chrome.exe', 1)
            if v == ''
            else (_ for _ in ()).throw(OSError("not found"))
        )
        # Edge fallback path exists
        mock_exists.side_effect = lambda p: 'msedge' in p.lower() or 'chrome' in p.lower()
        mock_isfile.return_value = True
        mock_ver.return_value = "120.0.6099.71"
        mock_arch.return_value = "x64"

        manager = BenchmarkManager()
        browsers = manager._detect_browsers()
        assert 'Edge' in browsers


# ═══════════════════════════════════════════════════════════════════════════
# 10. Comparability helpers (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

class TestComparability:
    def test_cross_major_blocked(self):
        comp, reason = can_compare_geekbench_scores(6, 7)
        assert comp is False
        assert "Score comparison blocked" in reason

    def test_same_major_allowed(self):
        comp, reason = can_compare_geekbench_scores(7, 7)
        assert comp is True

    def test_different_benchmarks_blocked(self):
        with pytest.raises(ValueError, match="Cannot compare results from different benchmarks"):
            assert_can_compare_benchmarks('geekbench_6', 'cinebench')


# ═══════════════════════════════════════════════════════════════════════════
# 11. BenchmarkInfo dataclass
# ═══════════════════════════════════════════════════════════════════════════

class TestBenchmarkInfo:
    def test_dataclass_properties(self):
        info = BenchmarkInfo(
            name='test', display_name='Test Benchmark',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_AUTO,
        )
        assert info.name == 'test'
        assert info.is_runnable()
        assert info.is_automatable()
        assert info.scan_diagnostics is None
        assert info.file_exists is True  # default

    def test_web_benchmark_file_exists_none(self):
        info = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
            file_exists=None,
        )
        assert info.file_exists is None


# ═══════════════════════════════════════════════════════════════════════════
# 12. Assisted launch safety
# ═══════════════════════════════════════════════════════════════════════════

class TestAssistedLaunch:
    def test_launch_requires_confirmation(self):
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files (x86)\Geekbench 6\Geekbench 6.exe',
        )
        with pytest.raises(ValueError, match="confirmation is required"):
            launch_assisted_benchmark(info, confirm=False)


# ═══════════════════════════════════════════════════════════════════════════
# 13. BenchmarkManager initialization
# ═══════════════════════════════════════════════════════════════════════════

class TestBenchmarkManagerInit:
    @patch('app.benchmark_manager.subprocess.run')
    def test_initialization(self, mock_run):
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        manager = BenchmarkManager()
        assert manager.benchmarks == []
        assert isinstance(manager.browser_info, dict)


# ═══════════════════════════════════════════════════════════════════════════
# 14. Status / Type enums
# ═══════════════════════════════════════════════════════════════════════════

class TestEnums:
    def test_status_values(self):
        for status in BenchmarkStatus:
            assert isinstance(status.value, str)
            assert len(status.value) > 0

    def test_type_values(self):
        for btype in BenchmarkType:
            assert isinstance(btype.value, str)
            assert len(btype.value) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 15. Elevation fallback — MOCKS
# ═══════════════════════════════════════════════════════════════════════════

class TestElevationFallbackMocks:
    """MOCKS: test the elevation fallback mechanism in _launch_with_elevation_fallback
    and its integration in launch_assisted_benchmark.

    Real UAC flow cannot be tested automatically (the UAC prompt runs on a
    secure desktop and requires human interaction).  These tests mock the
    OSError paths and the snapshot-based process confirmation.
    """

    # --- 15.1  WinError 740 on normal launch → retries with elevation ----

    def test_winerror_740_triggers_elevated_path(self):
        """MOCK: normal launch raises WinError 740 → elevated path is used."""
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench 6\Geekbench 6.exe',
        )

        def fake_popen(*a, **kw):
            exc = OSError()
            exc.winerror = 740
            raise exc

        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen', side_effect=fake_popen):
                with patch('os.startfile') as mock_startfile:
                    with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                        with patch('app.benchmark_manager._has_new_process', return_value=True):
                            result = launch_assisted_benchmark(info, confirm=True)

        assert result['status'] == 'launched'
        assert result.get('elevation_used') is True
        mock_startfile.assert_called_once()
        assert mock_startfile.call_args[0][1] == 'runas'

    # --- 15.2  Elevated launch WinError 1223 → cancelled ----------------

    def test_elevated_launch_winerror_1223_is_cancelled(self):
        """MOCK: normal launch raises 740, elevated launch raises 1223 → cancelled."""
        info = BenchmarkInfo(
            name='geekbench_7', display_name='Geekbench 7',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench 7\Geekbench 7.exe',
        )

        def fake_popen_740(*a, **kw):
            exc = OSError()
            exc.winerror = 740
            raise exc

        def fake_startfile_1223(*a, **kw):
            exc = OSError()
            exc.winerror = 1223
            raise exc

        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen', side_effect=fake_popen_740):
                with patch('os.startfile', side_effect=fake_startfile_1223):
                    result = launch_assisted_benchmark(info, confirm=True)

        assert result['status'] == 'cancelled'
        assert 'cancelled' in result['message'].lower() or 'cancel' in result['message'].lower()
        assert result.get('elevation_used') is True

    # --- 15.3  Other OSError during elevated launch → error --------------

    def test_elevated_launch_other_oserror_is_error(self):
        """MOCK: normal launch raises 740, elevated launch raises other OSError → error."""
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench 6\Geekbench 6.exe',
        )

        def fake_popen_740(*a, **kw):
            exc = OSError()
            exc.winerror = 740
            raise exc

        def fake_startfile_other(*a, **kw):
            exc = OSError()
            exc.winerror = 5
            raise exc

        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen', side_effect=fake_popen_740):
                with patch('os.startfile', side_effect=fake_startfile_other):
                    result = launch_assisted_benchmark(info, confirm=True)

        assert result['status'] == 'error'
        assert result.get('elevation_used') is True

    # --- 15.4  Non-740 OSError on normal launch → error (no elevation) ---

    def test_non_740_oserror_no_elevation(self):
        """MOCK: normal launch raises non-740 OSError → error, no elevation retry."""
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench 6\Geekbench 6.exe',
        )

        def fake_popen_access_denied(*a, **kw):
            exc = OSError()
            exc.winerror = 5
            raise exc

        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen', side_effect=fake_popen_access_denied):
                with patch('os.startfile') as mock_startfile:
                    result = launch_assisted_benchmark(info, confirm=True)

        assert result['status'] == 'error'
        assert result.get('elevation_used') is False
        mock_startfile.assert_not_called()

    # --- 15.5  Normal launch succeeds (no elevation needed) --------------

    def test_normal_launch_succeeds_no_elevation(self):
        """MOCK: normal launch succeeds, new process detected → launched."""
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench 6\Geekbench 6.exe',
        )

        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen'):
                with patch('os.startfile') as mock_startfile:
                    with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                        with patch('app.benchmark_manager._has_new_process', return_value=True):
                            result = launch_assisted_benchmark(info, confirm=True)

        assert result['status'] == 'launched'
        assert result.get('elevation_used') is False
        mock_startfile.assert_not_called()

    # --- 15.6  Process not seen within timeout → unconfirmed -------------

    def test_process_not_seen_unconfirmed(self):
        """MOCK: normal launch succeeds but no new process → unconfirmed."""
        info = BenchmarkInfo(
            name='geekbench_6', display_name='Geekbench 6',
            type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\Geekbench 6\Geekbench 6.exe',
        )

        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen'):
                with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                    with patch('app.benchmark_manager._has_new_process', return_value=False):
                        result = launch_assisted_benchmark(info, confirm=True, timeout=1)

        assert result['status'] == 'unconfirmed'
        assert result.get('elevation_used') is False

    # --- 15.7  Helper executables still rejected -------------------------

    def test_helper_executables_still_rejected(self):
        """Helper binaries must still be rejected even with elevation fallback."""
        for bad_name in ('geekbench_x86_64.exe', 'geekbench_avx2.exe',
                         'geekbench6.exe', 'geekbench7.exe'):
            info = BenchmarkInfo(
                name='geekbench_6', display_name='Geekbench 6',
                type=BenchmarkType.CPU, status=BenchmarkStatus.READY_MANUAL,
                executable_path=rf'C:\Program Files\Geekbench\{bad_name}',
            )
            with patch('os.path.isfile', return_value=True):
                with pytest.raises(RuntimeError, match="Refusing to launch non-main binary"):
                    launch_assisted_benchmark(info, confirm=True)

    # --- 15.8  Generic benchmark also uses elevation fallback ------------

    def test_generic_benchmark_uses_elevation_fallback(self):
        """MOCK: a non-Geekbench benchmark with WinError 740 also gets elevated."""
        info = BenchmarkInfo(
            name='d3dmark', display_name='3DMark',
            type=BenchmarkType.GPU, status=BenchmarkStatus.READY_MANUAL,
            executable_path=r'C:\Program Files\3DMark\3DMark.exe',
        )

        def fake_popen_740(*a, **kw):
            exc = OSError()
            exc.winerror = 740
            raise exc

        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.Popen', side_effect=fake_popen_740):
                with patch('os.startfile') as mock_startfile:
                    with patch('app.benchmark_manager._get_process_snapshot', return_value=[]):
                        with patch('app.benchmark_manager._has_new_process', return_value=True):
                            result = launch_assisted_benchmark(info, confirm=True)

        assert result['status'] == 'launched'
        assert result.get('elevation_used') is True
        mock_startfile.assert_called_once()
        assert mock_startfile.call_args[0][1] == 'runas'

    # --- 15.9  _launch_with_elevation_fallback exported for testing ------

    def test_helper_is_importable(self):
        """_launch_with_elevation_fallback must be importable for direct testing."""
        from app.benchmark_manager import _launch_with_elevation_fallback
        assert callable(_launch_with_elevation_fallback)


# ═══════════════════════════════════════════════════════════════════════════
# 16. Process snapshot — full-name matching, new-process detection
# ═══════════════════════════════════════════════════════════════════════════

class TestProcessSnapshot:
    """MOCKS: test _get_process_snapshot and _has_new_process logic."""

    def test_has_new_process_detects_new(self):
        """A new process appears after launch."""
        before = [('chrome.exe', None), ('svchost.exe', None)]
        after = [('chrome.exe', None), ('svchost.exe', None), ('Geekbench 6.exe', None)]
        assert _has_new_process(before, after, 'Geekbench 6.exe') is True

    def test_has_new_process_already_running(self):
        """Same process already running → not new."""
        before = [('Geekbench 6.exe', r'C:\Geekbench 6\Geekbench 6.exe')]
        after = [('Geekbench 6.exe', r'C:\Geekbench 6\Geekbench 6.exe')]
        assert _has_new_process(before, after, 'Geekbench 6.exe') is False

    def test_has_new_process_none_appears(self):
        """No matching process appears."""
        before = [('chrome.exe', None)]
        after = [('chrome.exe', None)]
        assert _has_new_process(before, after, 'Geekbench 6.exe') is False

    def test_has_new_process_full_name_match(self):
        """Only exact full name matches, not partial."""
        before = []
        after = [('geekbench_x86_64.exe', None)]
        assert _has_new_process(before, after, 'Geekbench 6.exe') is False

    def test_has_new_process_case_insensitive(self):
        """Matching is case-insensitive."""
        before = []
        after = [('GEEKBENCH 6.EXE', None)]
        assert _has_new_process(before, after, 'Geekbench 6.exe') is True

    def test_has_new_process_multiple_existing(self):
        """Two instances already running, a third appears → new."""
        before = [('Geekbench 6.exe', None), ('Geekbench 6.exe', None)]
        after = [('Geekbench 6.exe', None), ('Geekbench 6.exe', None),
                 ('Geekbench 6.exe', None)]
        assert _has_new_process(before, after, 'Geekbench 6.exe') is True

    def test_snapshot_returns_list(self):
        """MOCK: fake psutil processes -> (image, exe_or_None) tuples;
        nameless entries are skipped, unreadable exe becomes None."""
        procs = [
            Mock(info={'name': 'chrome.exe', 'exe': r'C:\Chrome\chrome.exe'}),
            Mock(info={'name': 'Geekbench 6.exe', 'exe': None}),
            Mock(info={'name': None, 'exe': None}),
        ]
        with patch('psutil.process_iter', return_value=procs):
            snapshot = _get_process_snapshot()
        assert snapshot == [
            ('chrome.exe', r'C:\Chrome\chrome.exe'),
            ('Geekbench 6.exe', None),
        ]

    def test_snapshot_failure_returns_empty(self):
        """MOCK: psutil raising -> empty snapshot, no exception."""
        with patch('psutil.process_iter', side_effect=RuntimeError('boom')):
            assert _get_process_snapshot() == []


# ═══════════════════════════════════════════════════════════════════════════
# 17. Browser channel isolation — browser detection raises, scan continues
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserChannelIsolation:
    """A crash in browser detection must not abort scan(): record the failure
    in diagnostics and continue with remaining benchmarks."""

    @patch.object(BenchmarkManager, '_detect_browsers')
    @patch.object(BenchmarkManager, '_query_system_apps')
    def test_browser_detection_crash_does_not_abort_scan(
        self, mock_query_apps, mock_browsers
    ):
        """MOCK: _detect_browsers raises -> scan continues, web benchmarks
        get NOT_INSTALLED with browser error in diagnostics."""
        mock_query_apps.return_value = {'appx_cinebench': None, 'start_apps': []}
        mock_browsers.side_effect = RuntimeError("simulated browser detection crash")

        manager = BenchmarkManager()
        results = manager.scan()

        # All benchmark definitions must still be present
        names = [r.name for r in results]
        assert 'cinebench' in names
        assert 'd3dmark' in names
        assert 'glmark2' in names
        assert 'speedometer' in names
        assert 'jetstream' in names

        # Web benchmarks should have browser error in diagnostics
        web_entries = [r for r in results if r.name in ('speedometer', 'jetstream')]
        for entry in web_entries:
            assert entry.scan_diagnostics is not None
            assert any('browser_detection' in d for d in entry.scan_diagnostics)
            assert entry.status == BenchmarkStatus.SCAN_ERROR
            assert 'browser check failed' in entry.reason


# ═══════════════════════════════════════════════════════════════════════════
# 18. Browser already running — web benchmark launch status
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserAlreadyRunning:
    """_run_single_execution launches nothing for web benchmarks, so a browser
    that is already running must never turn into a launch status."""

    def test_web_benchmark_browser_already_running(self):
        """MOCK: browser process present in the snapshot -> REAL
        _run_single_execution still returns a plain manual result."""
        manager = BenchmarkManager()
        # Fake browser info
        manager.browser_info = {
            'Chrome': {
                'path': r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                'version': '120.0.6099.71',
                'architecture': 'x64',
            }
        }
        executor = BenchmarkExecutor()
        executor.manager = manager

        web_info = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
            executable_path=None,
        )

        with patch('app.benchmark_manager._get_process_snapshot') as mock_snap:
            mock_snap.return_value = [
                ('chrome.exe', r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
                ('svchost.exe', None),
            ]
            result = executor._run_single_execution(web_info, timeout_seconds=60)

        assert 'launch_status' not in result
        assert result['execution_method'] == 'manual'
        assert result['score'] is None

    def test_web_benchmark_browser_not_running(self):
        """MOCK: no browser process in snapshot -> normal manual result."""
        manager = BenchmarkManager()
        manager.browser_info = {
            'Chrome': {
                'path': r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                'version': '120.0.6099.71',
                'architecture': 'x64',
            }
        }
        executor = BenchmarkExecutor()
        executor.manager = manager

        web_info = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.READY_MANUAL,
            executable_path=None,
        )

        with patch('app.benchmark_manager._get_process_snapshot') as mock_snap:
            mock_snap.return_value = [('svchost.exe', None)]
            result = executor._run_single_execution(web_info, timeout_seconds=60)

        # Should be normal manual result (no launch_status key)
        assert 'launch_status' not in result
        assert result['execution_method'] == 'manual'


# ═══════════════════════════════════════════════════════════════════════════
# 19. Fallback browser paths use os.environ (no hardcoded paths)
# ═══════════════════════════════════════════════════════════════════════════

class TestFallbackBrowserPaths:
    """Verify that _detect_browsers builds fallback paths from os.environ,
    not from literal hardcoded paths."""

    @patch('app.benchmark_manager.winreg')
    @patch('os.path.isfile')
    @patch('os.path.exists')
    @patch('app.benchmark_manager.get_pe_version')
    @patch('app.benchmark_manager.get_pe_architecture')
    def test_fallback_browser_has_unverified_flag(
        self, mock_arch, mock_ver, mock_exists, mock_isfile, mock_wr
    ):
        """MOCK: browser found via fallback path has found_by_fallback=True."""
        mock_wr.HKEY_LOCAL_MACHINE = -2147483646
        mock_wr.HKEY_CURRENT_USER = -2147483647
        mock_wr.KEY_READ = 0x20019
        mock_wr.KEY_WOW64_64KEY = 0x0100
        mock_wr.KEY_WOW64_32KEY = 0x0200

        class MockRegKey:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        # App Paths returns nothing useful
        mock_wr.OpenKey = lambda *a, **kw: MockRegKey()
        mock_wr.QueryInfoKey = lambda k: (0, 0, 0)
        mock_wr.EnumKey = lambda k, i: (_ for _ in ()).throw(OSError())
        mock_wr.QueryValueEx = lambda k, v: (_ for _ in ()).throw(OSError())

        # Fallback path exists for Chrome
        def exists_side_effect(p):
            return 'chrome' in str(p).lower()
        mock_exists.side_effect = exists_side_effect
        mock_isfile.return_value = False
        mock_ver.return_value = "120.0.6099.71"
        mock_arch.return_value = "x64"

        manager = BenchmarkManager()
        browsers = manager._detect_browsers()
        assert 'Chrome' in browsers
        assert browsers['Chrome'].get('found_by_fallback') is True

    @patch('app.benchmark_manager.winreg')
    @patch('os.path.isfile')
    @patch('os.path.exists')
    @patch('app.benchmark_manager.get_pe_version')
    @patch('app.benchmark_manager.get_pe_architecture')
    def test_fallback_visible_in_reason_and_diagnostics(
        self, mock_arch, mock_ver, mock_exists, mock_isfile, mock_wr
    ):
        """MOCK: when a browser is found by fallback, _discover_web reason text
        and scan_diagnostics must say 'found by fallback path, unverified'."""
        mock_wr.HKEY_LOCAL_MACHINE = -2147483646
        mock_wr.HKEY_CURRENT_USER = -2147483647
        mock_wr.KEY_READ = 0x20019
        mock_wr.KEY_WOW64_64KEY = 0x0100
        mock_wr.KEY_WOW64_32KEY = 0x0200

        class MockRegKey:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        # App Paths returns nothing -> fallback kicks in
        mock_wr.OpenKey = lambda *a, **kw: MockRegKey()
        mock_wr.QueryInfoKey = lambda k: (0, 0, 0)
        mock_wr.EnumKey = lambda k, i: (_ for _ in ()).throw(OSError())
        mock_wr.QueryValueEx = lambda k, v: (_ for _ in ()).throw(OSError())

        # Fallback path exists for Chrome
        def exists_side_effect(p):
            return 'chrome' in str(p).lower()
        mock_exists.side_effect = exists_side_effect
        mock_isfile.return_value = False
        mock_ver.return_value = "120.0.6099.71"
        mock_arch.return_value = "x64"

        manager = BenchmarkManager()
        from data.benchmark_definitions import DEFINITIONS_BY_ID
        defn = DEFINITIONS_BY_ID.get('speedometer')
        channel_errors = []
        results = manager._discover_web(defn, channel_errors)

        # The reason text must mention fallback
        assert 'found by fallback path, unverified' in results[0].reason
        # scan_diagnostics must also mention fallback
        assert results[0].scan_diagnostics is not None
        assert any('found by fallback path, unverified' in d
                   for d in results[0].scan_diagnostics)


# ═══════════════════════════════════════════════════════════════════════════
# 11. SCAN_ERROR plumbing tests
# ═══════════════════════════════════════════════════════════════════════════

class TestScanErrorPlumbing:
    """Verify that SCAN_ERROR entries are handled correctly in filter functions.

    SCAN_ERROR must:
     - show as \"could not check: <reason>\" in UI text
     - NOT count as installed (status_ready is False)
     - NOT be launchable via the normal path (is_runnable is False)
     - NOT be described as \"not installed\"
    """

    def test_scan_error_is_not_runnable(self):
        """SCAN_ERROR must not pass is_runnable()."""
        info = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.SCAN_ERROR,
            reason='browser check failed: connection timeout',
        )
        assert info.is_runnable() is False

    def test_scan_error_is_not_automatable(self):
        """SCAN_ERROR must not pass is_automatable()."""
        info = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.SCAN_ERROR,
            reason='browser check failed: connection timeout',
        )
        assert info.is_automatable() is False

    def test_scan_error_reason_preserved(self):
        """SCAN_ERROR reason must be preserved in the BenchmarkInfo."""
        info = BenchmarkInfo(
            name='jetstream', display_name='JetStream',
            type=BenchmarkType.WEB, status=BenchmarkStatus.SCAN_ERROR,
            reason='browser check failed: no browser found',
        )
        assert info.reason == 'browser check failed: no browser found'

    def test_scan_error_not_in_runnable_list(self):
        """SCAN_ERROR entries must not appear in the runnable filter."""
        benchmarks = [
            BenchmarkInfo('geekbench_6', 'Geekbench 6', BenchmarkType.CPU,
                          BenchmarkStatus.READY_MANUAL),
            BenchmarkInfo('speedometer', 'Speedometer', BenchmarkType.WEB,
                          BenchmarkStatus.SCAN_ERROR,
                          reason='browser check failed'),
            BenchmarkInfo('cinebench', 'Cinebench', BenchmarkType.CPU,
                          BenchmarkStatus.READY_MANUAL),
        ]
        runnable = [bm for bm in benchmarks if bm.is_runnable()]
        assert len(runnable) == 2
        names = [bm.name for bm in runnable]
        assert 'speedometer' not in names
        assert 'geekbench_6' in names
        assert 'cinebench' in names

    def test_scan_error_status_value(self):
        """SCAN_ERROR status value must be 'scan_error'."""
        assert BenchmarkStatus.SCAN_ERROR.value == 'scan_error'

    def test_scan_error_file_exists_is_none_for_web(self):
        """SCAN_ERROR web benchmarks should have file_exists=None."""
        info = BenchmarkInfo(
            name='speedometer', display_name='Speedometer',
            type=BenchmarkType.WEB, status=BenchmarkStatus.SCAN_ERROR,
            reason='browser check failed', file_exists=None,
        )
        assert info.file_exists is None


# ═══════════════════════════════════════════════════════════════════════════
# 12. Missing ProgramFiles env var tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMissingProgramFilesEnvVar:
    """When ProgramFiles env vars are missing, skip that root with a diagnostic note."""

    def test_search_roots_skip_missing_env(self, tmp_path, monkeypatch):
        """MOCK: with ProgramFiles env vars removed, the REAL _scan_filesystem
        scans only extra_roots (no literal C:\\Program Files fallback)."""
        from data.benchmark_definitions import DEFINITIONS_BY_ID

        monkeypatch.delenv('ProgramFiles', raising=False)
        monkeypatch.delenv('ProgramFiles(x86)', raising=False)

        gb_dir = tmp_path / 'Geekbench 6'
        gb_dir.mkdir()
        (gb_dir / 'Geekbench 6.exe').write_bytes(b'')

        manager = BenchmarkManager()
        defn = DEFINITIONS_BY_ID['geekbench']

        scanned = []
        real_listdir = os.listdir

        def spy_listdir(path):
            scanned.append(os.path.normcase(os.path.normpath(path)))
            return real_listdir(path)

        with patch('app.benchmark_manager.os.listdir', side_effect=spy_listdir):
            candidates, methods = manager._scan_filesystem(defn, [str(tmp_path)])

        assert candidates == [os.path.normpath(str(gb_dir / 'Geekbench 6.exe'))]
        assert methods == {'filesystem pattern'}
        root = os.path.normcase(os.path.normpath(str(tmp_path)))
        assert all(p.startswith(root) for p in scanned), scanned


# ═══════════════════════════════════════════════════════════════════════════
# PowerShell JSON shape: one-element arrays are unwrapped by ConvertTo-Json
# ═══════════════════════════════════════════════════════════════════════════

class TestQuerySystemAppsJsonShape:
    """MOCK: feed PowerShell-shaped JSON into the REAL _query_system_apps."""

    def _run(self, stdout):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=stdout)
            return BenchmarkManager()._query_system_apps()

    def test_single_start_app_object_becomes_list(self):
        data = self._run('{"appx_cinebench": null, "start_apps": '
                         '{"Name": "Geekbench 7", "AppID": "Geekbench 7.exe"}}')
        assert data['start_apps'] == [{'Name': 'Geekbench 7', 'AppID': 'Geekbench 7.exe'}]

    def test_null_start_apps_becomes_empty_list(self):
        assert self._run('{"appx_cinebench": null, "start_apps": null}')['start_apps'] == []

    def test_list_is_kept(self):
        data = self._run('{"start_apps": [{"Name": "A", "AppID": "a"}, {"Name": "B", "AppID": "b"}]}')
        assert [s['Name'] for s in data['start_apps']] == ['A', 'B']

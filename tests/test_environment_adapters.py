"""MOCKED tests for app/environment/adapters.py (no host dependence).

Fake inputs go into the REAL helper functions and the REAL build_profile runner.
"""

import os
from unittest.mock import patch

import pytest

from app.environment import adapters
from app.environment.adapters import (
    PlatformAdapter, WindowsAdapter, emulation_field, process_arch_from_platform_tag,
    select_adapter, windows_name_from_build,
)
from app.environment.profile import DETECTED, UNAVAILABLE, UNVERIFIED, detected


@pytest.mark.parametrize('build, edition, expected', [
    (26300, 'Professional', 'Windows 11 Professional'),
    (22000, None, 'Windows 11'),
    (19045, 'Core', 'Windows 10 Core'),
    (9600, None, 'Windows'),
])
def test_windows_name_from_build(build, edition, expected):
    assert windows_name_from_build(build, edition) == expected


@pytest.mark.parametrize('tag, expected', [
    ('win-amd64', 'x64'),
    ('win32', 'x86'),
    ('win-arm64', 'arm64'),
    ('linux-x86_64', 'x64'),
    ('linux-aarch64', 'arm64'),
    ('linux-i686', 'x86'),
    ('macosx-11.0-universal2', 'unknown'),
])
def test_process_arch_from_platform_tag(tag, expected):
    assert process_arch_from_platform_tag(tag) == expected


class TestEmulation:
    def test_windows_x86_64_native(self):
        f = emulation_field('x64', 'IsWow64Process2')
        assert (f.value, f.status) == (False, DETECTED)

    def test_windows_arm64_native_runs_x64_emulated(self):
        f = emulation_field('arm64', 'IsWow64Process2')
        assert (f.value, f.status) == (True, DETECTED)

    def test_unknown_native_is_unavailable(self):
        assert emulation_field('unknown', 'env').status == UNAVAILABLE


class TestBuildProfileIsolation:
    def test_failing_step_is_recorded_and_others_still_run(self):
        class Fake(PlatformAdapter):
            os_family = 'windows'

            def steps(self):
                def boom():
                    raise RuntimeError('registry unreadable')
                return [('os', boom),
                        ('architecture', lambda: {'native_arch': detected('arm64', 'fake')})]

        profile = Fake().build_profile()
        assert profile.diagnostics == ['step os failed: registry unreadable']
        assert profile.native_arch.value == 'arm64'
        assert profile.os_name.status == UNAVAILABLE
        assert set(profile.step_seconds) == {'os', 'architecture'}
        assert profile.os_family.value == 'windows'


class TestWindowsGraphics:
    def _system_root(self, tmp_path, dlls):
        sys32 = tmp_path / 'System32'
        sys32.mkdir()
        for dll in dlls:
            (sys32 / dll).write_bytes(b'')
        return str(tmp_path)

    def test_all_runtimes_present(self, tmp_path):
        out = WindowsAdapter(self._system_root(tmp_path, ['d3d12.dll', 'vulkan-1.dll', 'opengl32.dll']))._graphics()
        assert out['directx'].status == DETECTED
        assert out['vulkan'].status == UNVERIFIED   # loader present != driver support
        assert out['opengl'].status == UNVERIFIED

    def test_missing_runtime_is_unavailable(self, tmp_path):
        out = WindowsAdapter(self._system_root(tmp_path, ['d3d12.dll']))._graphics()
        assert out['vulkan'].status == UNAVAILABLE and out['vulkan'].value is None

    def test_missing_system_root_raises_for_isolation(self):
        adapter = WindowsAdapter()
        adapter.system_root = None
        with pytest.raises(RuntimeError):
            adapter._graphics()


@pytest.mark.skipif(os.name != 'nt', reason='ctypes.windll exists only on Windows')
class TestWindowsElevation:
    @pytest.mark.parametrize('flag, expected', [(1, True), (0, False)])
    def test_elevated_and_not_elevated(self, flag, expected):
        with patch('ctypes.windll.shell32.IsUserAnAdmin', return_value=flag):
            out = WindowsAdapter()._elevation()
        assert out['app_is_elevated'].value is expected
        assert 'UIPI' in out['app_is_elevated'].source


def test_select_adapter():
    assert isinstance(select_adapter('Windows'), WindowsAdapter)
    assert type(select_adapter('Darwin')) is PlatformAdapter

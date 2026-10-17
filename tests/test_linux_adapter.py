"""MOCKED tests for LinuxAdapter: fake files/env/uname into the REAL adapter code.

Linux detection is unverified on real hardware; these only prove the logic.
"""

import pytest

from app.environment.linux_adapter import LinuxAdapter
from app.environment.profile import UNAVAILABLE, UNVERIFIED

UBUNTU = 'NAME="Ubuntu"\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'


def adapter(files=None, env=None, machine='x86_64', euid=1000):
    files = files or {}
    return LinuxAdapter(read_text=files.get, env=env or {},
                        machine=lambda: machine, euid=lambda: euid)


def run_os_arch(a):
    out = {}
    out.update(a._os())
    out.update(a._architecture())
    out.update(a._elevation())
    return out


def test_linux_x86_64_desktop():
    out = run_os_arch(adapter({'/etc/os-release': UBUNTU, '/proc/version': 'Linux version 6.8.0-45-generic'},
                              env={'XDG_SESSION_TYPE': 'wayland'}))
    assert out['os_name'].value == 'Ubuntu 24.04 LTS'
    assert out['os_version'].value == '24.04'
    assert out['native_arch'].value == 'x64'
    assert out['x64_emulated'].value is False
    assert out['is_wsl'].value is False
    assert out['display_server'].value == 'wayland'
    assert out['app_is_elevated'].value is False
    # Nothing on Linux claims "detected": all unverified or unavailable
    assert {f.status for f in out.values()} <= {UNVERIFIED, UNAVAILABLE}


def test_linux_aarch64():
    out = run_os_arch(adapter({'/etc/os-release': UBUNTU}, machine='aarch64'))
    assert out['native_arch'].value == 'arm64'
    assert out['x64_emulated'].value is True


def test_wsl_detected_from_proc_version():
    out = run_os_arch(adapter({'/etc/os-release': UBUNTU,
                               '/proc/version': 'Linux version 5.15.153.1-microsoft-standard-WSL2'}))
    assert out['is_wsl'].value is True


def test_root_user_is_elevated():
    assert run_os_arch(adapter(euid=0))['app_is_elevated'].value is True


def test_missing_os_release_falls_back_then_unavailable():
    out = run_os_arch(adapter({'/usr/lib/os-release': 'NAME=Fedora\n'}))
    assert out['os_name'].value == 'Fedora'
    out = run_os_arch(adapter({}))
    assert out['os_name'].status == UNAVAILABLE
    assert out['display_server'].status == UNAVAILABLE


def test_unknown_machine_is_unavailable():
    out = run_os_arch(adapter(machine='riscv64'))
    assert out['native_arch'].status == UNAVAILABLE
    assert out['x64_emulated'].status == UNAVAILABLE


def test_build_profile_runs_all_steps_isolated():
    profile = adapter({'/etc/os-release': UBUNTU}).build_profile()
    assert profile.os_family.value == 'linux'
    assert profile.os_name.value == 'Ubuntu 24.04 LTS'
    assert set(profile.step_seconds) == {'os', 'architecture', 'elevation', 'hardware', 'graphics'}


def test_constructible_on_any_os():
    """select_adapter('Linux') must not touch POSIX-only APIs at construction."""
    from app.environment.adapters import select_adapter
    assert isinstance(select_adapter('Linux'), LinuxAdapter)

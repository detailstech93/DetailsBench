"""MOCKED tests for check_compatibility and its integration in BenchmarkManager.

Profiles are built by hand; definitions are the REAL records (replaced
fields only where a case needs a different support value).
"""

import dataclasses

from app.benchmark_manager import BenchmarkInfo, BenchmarkManager, BenchmarkStatus, BenchmarkType
from app.environment.compatibility import (
    COMPATIBLE, INCOMPATIBLE, UNVERIFIED, check_compatibility,
)
from app.environment.profile import EnvironmentProfile, detected, unavailable, unverified
from data.benchmark_definitions import DEFINITIONS_BY_ID

GEEKBENCH = DEFINITIONS_BY_ID['geekbench']


def make_profile(os_family='windows', native='x64', is_wsl=False, **graphics):
    p = EnvironmentProfile()
    p.os_family = detected(os_family, 'test')
    p.is_wsl = detected(is_wsl, 'test')
    p.native_arch = detected(native, 'test') if native else unavailable('test')
    for api, f in graphics.items():
        setattr(p, api, f)
    return p


def with_arch(definition, **support):
    return dataclasses.replace(definition, supported_architectures=support)


def test_native_support_is_compatible():
    r = check_compatibility(with_arch(GEEKBENCH, x64='native'), make_profile())
    assert (r.status, r.reasons, r.comparable_to_native) == (COMPATIBLE, [], True)


def test_default_unverified_arch():
    r = check_compatibility(GEEKBENCH, make_profile())
    assert r.status == UNVERIFIED
    assert r.reasons == ['support on x64 not verified']


def test_wrong_os_is_incompatible():
    r = check_compatibility(GEEKBENCH, make_profile(os_family='linux'))
    assert r.status == INCOMPATIBLE
    assert 'supported OS: windows; this system: linux' in r.reasons


def test_wsl_suite_matches_inside_wsl_only():
    glmark2 = with_arch(DEFINITIONS_BY_ID['glmark2'], x64='native')
    gl = unverified('loader present', 'test')
    assert check_compatibility(glmark2, make_profile('linux', is_wsl=True, opengl=gl)).status != INCOMPATIBLE
    assert check_compatibility(glmark2, make_profile('windows', opengl=gl)).status == INCOMPATIBLE


def test_arm64_emulated_flags_not_comparable():
    r = check_compatibility(with_arch(GEEKBENCH, arm64='emulated'), make_profile(native='arm64'))
    assert r.status == COMPATIBLE
    assert r.comparable_to_native is False
    assert 'not directly comparable' in r.reasons[0]


def test_arch_explicitly_unsupported():
    r = check_compatibility(with_arch(GEEKBENCH, arm64='unsupported'), make_profile(native='arm64'))
    assert r.status == INCOMPATIBLE


def test_unknown_information_never_rejects():
    p = EnvironmentProfile()   # everything unavailable
    r = check_compatibility(DEFINITIONS_BY_ID['d3dmark'], p)
    assert r.status == UNVERIFIED
    assert len(r.reasons) == 3   # os, arch, graphics


def test_graphics_requirement():
    d3d = with_arch(DEFINITIONS_BY_ID['d3dmark'], x64='native')
    assert check_compatibility(d3d, make_profile(directx=detected('d3d12', 't'))).status == COMPATIBLE
    r = check_compatibility(d3d, make_profile(directx=unavailable('t')))
    assert r.status == UNVERIFIED and 'requires directx' in r.reasons[0]


class TestManagerIntegration:
    def _manager(self, *infos):
        m = BenchmarkManager()
        m.benchmarks = list(infos)
        return m

    def test_incompatible_ready_benchmark_becomes_unsupported(self):
        bm = BenchmarkInfo(name='geekbench_7', display_name='Geekbench 7', type=BenchmarkType.CPU,
                           status=BenchmarkStatus.READY_MANUAL, executable_path='x.exe')
        self._manager(bm).apply_compatibility(make_profile(os_family='linux'))
        assert bm.status == BenchmarkStatus.UNSUPPORTED
        assert bm.reason.startswith('Unsupported on this system: supported OS: windows')
        assert bm.compatibility['status'] == INCOMPATIBLE

    def test_unverified_keeps_status(self):
        bm = BenchmarkInfo(name='speedometer', display_name='Speedometer', type=BenchmarkType.WEB,
                           status=BenchmarkStatus.READY_MANUAL)
        self._manager(bm).apply_compatibility(make_profile())
        assert bm.status == BenchmarkStatus.READY_MANUAL
        assert bm.compatibility['status'] == UNVERIFIED

    def test_not_installed_stays_not_installed(self):
        bm = BenchmarkInfo(name='d3dmark', display_name='3DMark', type=BenchmarkType.GPU,
                           status=BenchmarkStatus.NOT_INSTALLED)
        self._manager(bm).apply_compatibility(make_profile(os_family='linux'))
        assert bm.status == BenchmarkStatus.NOT_INSTALLED
        assert bm.compatibility['status'] == INCOMPATIBLE

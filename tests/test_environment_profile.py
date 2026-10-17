"""Tests for the EnvironmentProfile data model (app/environment/profile.py)."""

import json

import pytest

from app.environment.profile import (
    DETECTED, UNAVAILABLE, UNVERIFIED, EnvironmentProfile, ProfileField,
    detected, unavailable, unverified,
)


def test_new_profile_is_all_unavailable():
    profile = EnvironmentProfile()
    for name in profile.field_names():
        f = getattr(profile, name)
        assert f.status == UNAVAILABLE and f.value is None, name


def test_helpers_set_status():
    assert detected('x64', 'IsWow64Process2') == ProfileField('x64', 'IsWow64Process2', DETECTED)
    assert unavailable('dxdiag') == ProfileField(None, 'dxdiag', UNAVAILABLE)
    assert unverified('wayland', 'XDG_SESSION_TYPE').status == UNVERIFIED


def test_invalid_status_rejected():
    with pytest.raises(ValueError):
        ProfileField(1, 'x', 'maybe')


def test_json_round_trip():
    profile = EnvironmentProfile()
    profile.os_family = detected('windows', 'platform.system')
    profile.native_arch = detected('arm64', 'IsWow64Process2')
    profile.x64_emulated = detected(True, 'IsWow64Process2')
    profile.display_server = unavailable('not linux')
    profile.diagnostics.append('graphics: dxdiag timed out')
    profile.step_seconds['os'] = 0.01

    text = json.dumps(profile.to_dict())
    restored = EnvironmentProfile.from_dict(json.loads(text))

    assert restored == profile


def test_from_dict_tolerates_missing_fields():
    restored = EnvironmentProfile.from_dict({'os_family': {'value': 'linux', 'source': 's',
                                                          'status': DETECTED}})
    assert restored.os_family.value == 'linux'
    assert restored.gpu.status == UNAVAILABLE
    assert restored.diagnostics == []

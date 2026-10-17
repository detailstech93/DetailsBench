import json

import pytest

from app.games import rise_tomb_raider as rt
from app.games.settings_file import BACKUP_NAME, ForcedSettings, read_for_dialog

REAL = {"Fullscreen": 1, "ExclusiveFullscreen": 1, "FullscreenWidth": 2560, "FullscreenHeight": 1440, "VSync": 1,
        "TextureQuality": 3, "ShadowQuality": 3, "FXAA": 0, "SSAAQuality": 0, "MotionBlur": 1,
        "AmbientOcclusionQuality": 3, "LevelOfDetail": 3, "SMAA": 0, "EnableDX12": 0, "DLSS": 3, "RefreshRate": 60}


class FakeReg:
    def __init__(self, values=None, missing=False):
        self.values, self.missing, self.writes = dict(values or REAL), missing, []

    def read(self, key):
        assert key == rt.REGISTRY_KEY
        if self.missing:
            raise FileNotFoundError("no such key")
        return dict(self.values)

    def set_dword(self, key, name, value):
        self.writes.append((name, value))
        self.values[name] = value


def test_read_settings_on_the_real_values():
    s = rt.read_settings(REAL)
    assert s["resolution"] == "2560x1440" and s["fullscreen"] is True and s["fxaa"] is False
    assert s["graphics_api"] == "DX11" and s["vsync"] == 1 and s["dlss"] == 3
    assert "fullscreen" not in rt.read_settings({"Fullscreen": 2})          # not 0/1: absent, never guessed


def test_plan_rules():
    known = rt.merge_indices({}, rt.observed_indices(REAL))
    changes, refused = rt.plan(REAL, {"vsync": 0, "graphics_api": "DX12", "fxaa": True, "dlss": 3,
                                      "texture_quality": "high", "nope": 1, "motion_blur": 1}, known)
    assert [c.key for c in changes] == ["graphics_api", "fxaa"]
    assert refused["vsync"] == rt.NOT_SEEN and refused["texture_quality"] == "expects a whole number"
    assert refused["nope"] == "unknown setting" and refused["motion_blur"] == "expects true or false"
    known["vsync"]["0"] = 0
    assert [c.key for c in rt.plan(REAL, {"vsync": 0}, known)[0]] == ["vsync"]


def test_apply_and_verify():
    known = {"resolution": {"1920x1080": 0}}
    changes, refused = rt.plan(REAL, {"resolution": "1920x1080", "graphics_api": "DX12", "motion_blur": False}, known)
    out = rt.apply(REAL, changes)
    assert (out["FullscreenWidth"], out["FullscreenHeight"], out["EnableDX12"], out["MotionBlur"]) == (1920, 1080, 1, 0)
    assert REAL["FullscreenWidth"] == 2560 and rt.verify(out, {"resolution": "1920x1080", "motion_blur": False}) == {}
    assert rt.verify(REAL, {"motion_blur": False}) == {"motion_blur": (False, True)}


def test_choices():
    c = rt.choices(REAL, {"vsync": {"0": 0}, "resolution": {"1920x1080": 0}})
    assert c["vsync"] == [0, 1] and c["graphics_api"] == ["DX11", "DX12"] and c["fxaa"] == [True, False]
    assert c["resolution"] == ["1920x1080", "2560x1440"]


def test_registry_flow_writes_only_changes_and_puts_values_back(tmp_path):
    reg = FakeReg()
    fs = ForcedSettings("391220", {"graphics_api": "DX12", "motion_blur": False, "fxaa": False},
                        tmp_path / "run", tmp_path / "data", reg=reg)
    rec = fs.apply()
    assert rec.refused == {} and rec.mismatch_before_launch == {} and rec.file.startswith("HKEY_CURRENT_USER\\")
    assert sorted(reg.writes) == [("EnableDX12", 1), ("MotionBlur", 0)]                 # FXAA was already off
    assert json.loads((tmp_path / "run" / (BACKUP_NAME + ".json")).read_text(encoding="utf-8")) == REAL
    rec = fs.finish()
    assert rec.verified and rec.restored is True and reg.values == REAL
    assert sorted(reg.writes[2:]) == [("EnableDX12", 0), ("MotionBlur", 1)]       # only what differs, back


def test_missing_key_changes_nothing(tmp_path):
    reg = FakeReg(missing=True)
    with pytest.raises(OSError, match="start the game once"):
        ForcedSettings("391220", {"fxaa": True}, tmp_path / "run", tmp_path / "data", reg=reg).apply()
    with pytest.raises(OSError):
        read_for_dialog("391220", tmp_path / "data", reg=reg)
    assert reg.writes == []


def test_dialog_values_come_from_the_registry(tmp_path):
    choices, current = read_for_dialog("391220", tmp_path / "data", reg=FakeReg())
    assert current["resolution"] == "2560x1440" and choices["dlss"] == [3]

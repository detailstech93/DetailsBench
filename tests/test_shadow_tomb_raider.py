"""Shadow of the Tomb Raider settings in the registry (owner, 2026-10-23: it was missing from the game settings)."""

from app.games import shadow_tomb_raider as sotr
from app.games.presets import GamePreset, game_values, plan_preset
from app.games.settings_file import adapter_for

# values read on this PC on 2026-10-23 (a few of them)
REG = {"FullscreenWidth": 1920, "FullscreenHeight": 1080, "Fullscreen": 1, "ExclusiveFullscreen": 0,
       "EnableDX12": 1, "RayTracing": 0, "DLSS": 3, "XESS": 0, "CAS": 0, "TextureQuality": 2, "MotionBlur": 1}


def test_shadow_of_the_tomb_raider_is_read_and_changed_in_its_registry_key():
    adapter = adapter_for(750920)
    assert adapter.base == "registry" and adapter.relative_path.endswith(r"Shadow of the Tomb Raider\Graphics")
    now = sotr.read_settings(REG)
    assert now["resolution"] == "1920x1080" and now["dlss"] == "Quality" and now["xess"] == 0
    assert now["fullscreen"] is True and now["ray_tracing"] == "Off" and "preset" not in now
    known = sotr.merge_indices({}, {"resolution": {"2560x1440": 0}})
    changes, refused = sotr.plan(REG, {"resolution": "2560x1440", "dlss": "Balanced", "xess": 1}, known)
    assert refused == {"xess": sotr._rt.NOT_SEEN}
    new = sotr.apply(REG, changes)
    assert (new["FullscreenWidth"], new["FullscreenHeight"], new["DLSS"]) == (2560, 1440, 2) and REG["DLSS"] == 3
    assert sotr.verify(new, {"resolution": "2560x1440", "dlss": "Balanced"}) == {}
    assert sotr.plan(REG, {"dlss": "DLAA"}, known)[1] == {"dlss": "not one of: " + ", ".join(sotr.DLSS)}


def test_a_preset_writes_every_one_of_its_settings_and_is_read_back():
    high = dict(zip(sotr.PRESET_VALUES, sotr.PRESETS["High"]), **REG)
    assert sotr.read_settings(high)["preset"] == "High"
    assert sotr.read_settings(dict(high, Tessellation=0))["preset"] == "Custom"
    changes, refused = sotr.plan(high, {"preset": "Highest", "ray_tracing": "Ultra"}, {})
    new = sotr.apply(high, changes)
    assert not refused and new["TextureQuality"] == 3 and new["ScreenSpaceContactShadows"] == 1 and new["RayTracing"] == 3
    assert sotr.read_settings(new)["preset"] == "Highest" and sotr.verify(new, {"preset": "Highest"}) == {}


def test_the_dialog_offers_what_was_matched_with_the_owner():
    high = dict(zip(sotr.PRESET_VALUES, sotr.PRESETS["High"]), **REG)
    offered = sotr.choices(high, {})
    preset = GamePreset(resolution="1920x1080", upscaler="dlss", ray_tracing="on", quality="ultra")
    assert plan_preset(750920, preset, offered) == (
        {"resolution": "1920x1080", "dlss": "Quality", "ray_tracing": "Ultra", "preset": "Highest"}, {})
    assert plan_preset(750920, GamePreset(upscaler="off", ray_tracing="off", quality="low"), offered)[0] == {
        "dlss": "Off", "ray_tracing": "Off", "preset": "Low"}
    assert game_values(750920, offered, "upscaler", ["off", "dlss", "fsr", "xess"]) == ["off", "dlss"]
    assert game_values(750920, offered, "quality", ["low", "medium", "high", "ultra"]) == ["low", "medium", "high", "ultra"]
    assert game_values(750920, offered, "frame_gen", ["x2"]) is None

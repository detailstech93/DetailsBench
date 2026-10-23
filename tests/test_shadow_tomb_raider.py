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
    assert now["resolution"] == "1920x1080" and now["dlss"] == 3 and now["xess"] == 0 and now["fullscreen"] is True
    known = sotr.merge_indices({}, {"resolution": {"2560x1440": 0}})
    changes, refused = sotr.plan(REG, {"resolution": "2560x1440", "dlss": 1}, known)
    assert refused == {"dlss": sotr._rt.NOT_SEEN}
    new = sotr.apply(REG, changes)
    assert new["FullscreenWidth"] == 2560 and new["FullscreenHeight"] == 1440 and REG["FullscreenWidth"] == 1920
    assert sotr.verify(new, {"resolution": "2560x1440"}) == {}


def test_the_dialog_offers_its_resolution_and_nothing_it_cannot_name():
    offered = sotr.choices(REG, {})
    assert plan_preset(750920, GamePreset(resolution="1920x1080"), offered) == ({"resolution": "1920x1080"}, {})
    assert game_values(750920, offered, "resolution", ["1920x1080"]) == ["1920x1080"]
    assert game_values(750920, offered, "upscaler", ["dlss"]) is None

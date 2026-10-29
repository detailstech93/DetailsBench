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
    from app.games import presets
    assert game_values(750920, offered, "quality", list(presets.QUALITIES[1:])) == [
        "lowest", "low", "medium", "high", "highest"]
    assert game_values(750920, offered, "ray_tracing", list(presets.RAY_TRACING[1:])) == ["off", "medium", "high", "ultra"]
    assert game_values(1091500, None, "ray_tracing", list(presets.RAY_TRACING[1:])) == ["off", "on"]
    assert "lowest" not in game_values(1091500, None, "quality", list(presets.QUALITIES[1:]))


def test_a_resolution_the_screen_offers_is_written_in_exclusive_full_screen(monkeypatch):
    from app.games import screen_modes
    monkeypatch.setattr(screen_modes, "resolutions", lambda: {(1920, 1080), (2560, 1440)})
    assert "2560x1440" in sotr.choices(REG, {})["resolution"]
    changes, refused = sotr.plan(REG, {"resolution": "2560x1440"}, {})
    new = sotr.apply(REG, changes)
    assert not refused and (new["FullscreenWidth"], new["ExclusiveFullscreen"]) == (2560, 1)
    assert sotr.plan(REG, {"resolution": "3840x2160"}, {})[1] == {"resolution": sotr._rt.NOT_SEEN}
    assert [c.key for c in sotr.plan(REG, {"dlss": "Off"}, {})[0]] == ["dlss"]


def test_what_was_written_is_kept_in_the_run_folder(tmp_path):
    import json
    from app.games.settings_file import RECORD_NAME, ForcedSettings

    class Store:
        where, backup_suffix, writable = "registry", ".json", True
        data = dict(REG)
        def exists(self): return True
        def read(self): return dict(self.data)
        def write(self, d): self.data = dict(d)
        def save_backup(self, path): path.write_text(json.dumps(self.data))
        def restore(self, path): self.data = json.loads(path.read_text())

    forced = ForcedSettings(750920, {"dlss": "Off"}, tmp_path / "run", tmp_path / "data")
    forced.store = Store()
    forced.apply()
    kept = json.loads((tmp_path / "run" / RECORD_NAME).read_text(encoding="utf-8"))
    assert kept["changed"] == ["dlss"] and kept["requested"] == {"dlss": "Off"}


def test_the_dlss_mode_is_chosen_only_with_dlss_on():
    from app.games import presets
    offered = sotr.choices(REG, {})
    assert game_values(750920, offered, "upscaler_mode", list(presets.UPSCALER_MODES[1:])) == [
        "quality", "balanced", "performance", "ultra_performance"]
    assert game_values(1091500, None, "upscaler_mode", list(presets.UPSCALER_MODES[1:])) is None
    on = presets.game_preset("upscaling", "dlss", "off", presets.KEEP, presets.KEEP, presets.KEEP, "performance")
    assert plan_preset(750920, on, offered)[0] == {"dlss": "Performance"}
    off = presets.game_preset("raw", "dlss", "off", presets.KEEP, presets.KEEP, presets.KEEP, "performance")
    assert off.upscaler_mode == presets.KEEP and plan_preset(750920, off, offered)[0] == {"dlss": "Off"}
    assert presets.GamePreset.from_dict(on.to_dict()) == on


def test_the_dialog_offers_only_resolutions_the_screen_has(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from app.games import presets, screen_modes
    from app.ui.run_tests import PresetBox
    monkeypatch.setattr(screen_modes, "resolutions", lambda: {(1280, 720), (1920, 1080)})
    box = PresetBox([("750920", "SOTTR")], {"750920": (sotr.choices(REG, {}), None)}, presets.GamePreset())
    combo = box.combos["resolution"]
    assert [combo.itemData(i) for i in range(combo.count())] == ["1280x720", "1920x1080", presets.KEEP]


def test_the_remote_page_gets_each_games_own_lists():
    from app.games import presets
    lists = presets.page_game_lists(750920, sotr.choices(REG, {}), {(1280, 720), (1920, 1080)})
    assert [v for v, _ in lists["qualities"]] == ["lowest", "low", "medium", "high", "highest", presets.KEEP]
    assert [v for v, _ in lists["ray_tracing"]] == ["off", "medium", "high", "ultra", presets.KEEP]
    assert [v for v, _ in lists["upscaler_modes"]][:2] == ["quality", "balanced"]
    assert [v for v, _ in lists["resolutions"]] == ["1280x720", "1920x1080", presets.KEEP]
    assert lists["upscalers"] == ["off", "dlss"] and lists["frame_gens"] is None
    rdr2 = presets.page_game_lists(1174180, None, None)
    assert rdr2["ray_tracing"] == [] and rdr2["upscalers"] == ["off", "dlss", "fsr"]
    page = {"mode": "upscaling", "upscaler": "dlss", "upscaler_mode": "balanced", "quality": "highest",
            "ray_tracing": "medium", "resolution": "1920x1080"}
    assert presets.from_page(page).upscaler_mode == "balanced"
    assert presets.from_page(dict(page, mode="raw")).upscaler_mode == presets.KEEP

import json

import pytest

from app.games.settings_file import (BACKUP_NAME, INDICES_FILENAME, ForcedSettings, adapter_for, learn_indices,
                                     load_indices)

APP = "1091500"


def sample():
    return {"version": 140, "data": [
        {"group_name": "/graphics/presets", "options": [
            {"name": "DLSS", "type": "string_list", "is_dynamic": True, "value": "Balanced", "index": 3},
            {"name": "DLSSFrameGen", "type": "bool", "value": True, "default_value": False}]},
        {"group_name": "/graphics/raytracing", "options": [
            {"name": "RayTracing", "type": "bool", "value": True, "default_value": False},
            {"name": "RayTracedLighting", "type": "string_list", "is_dynamic": False, "value": "Psycho", "index": 3,
             "values": ["Off", "Medium", "Ultra", "Psycho"]}]},
        {"group_name": "/video/display", "options": [
            {"name": "Resolution", "type": "string_list", "is_dynamic": True, "value": "2560x1440", "index": 0}]}]}


@pytest.fixture
def env(tmp_path):
    local = tmp_path / "local"
    path = local / "CD Projekt Red" / "Cyberpunk 2077" / "UserSettings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(sample(), indent=4), encoding="utf-8")
    return {"LOCALAPPDATA": str(local)}, path, tmp_path / "data", tmp_path / "run"


def option(path, name):
    for g in json.loads(path.read_text(encoding="utf-8"))["data"]:
        for o in g["options"]:
            if o["name"] == name:
                return o


def test_adapter_only_for_known_games():
    assert adapter_for(1091500) is not None and adapter_for("1091500") is not None
    assert adapter_for("570") is None
    with pytest.raises(ValueError):
        ForcedSettings("570", {}, ".", ".")


def test_apply_writes_backs_up_and_verifies(env):
    e, path, data_dir, run_dir = env
    original = path.read_bytes()
    fs = ForcedSettings(APP, {"ray_tracing": False, "rt_lighting": "Ultra", "dlss_frame_gen": True}, run_dir,
                        data_dir, env=e)
    rec = fs.apply()
    assert rec.changed == ["ray_tracing", "rt_lighting"] and rec.refused == {}
    assert rec.mismatch_before_launch == {} and rec.before["rt_lighting"] == "Psycho"
    assert (run_dir / BACKUP_NAME).read_bytes() == original
    assert option(path, "RayTracing")["value"] is False
    assert option(path, "RayTracedLighting")["value"] == "Ultra" and option(path, "RayTracedLighting")["index"] == 2
    assert not path.with_name(path.name + ".pcbm-tmp").exists()


def test_finish_verifies_after_run_and_restores(env):
    e, path, data_dir, run_dir = env
    original = path.read_bytes()
    fs = ForcedSettings(APP, {"ray_tracing": False}, run_dir, data_dir, env=e)
    fs.apply()
    rec = fs.finish()
    assert rec.mismatch_after_run == {} and rec.restored is True and rec.verified
    assert path.read_bytes() == original
    assert rec.to_dict()["verified"] is True


def test_game_rewrote_a_setting_is_a_mismatch(env):
    e, path, data_dir, run_dir = env
    fs = ForcedSettings(APP, {"ray_tracing": False}, run_dir, data_dir, env=e)
    fs.apply()
    path.write_text(json.dumps(sample()), encoding="utf-8")            # the game put RT back on
    rec = fs.finish()
    assert rec.mismatch_after_run == {"ray_tracing": [False, True]} and not rec.verified


def test_unknown_dynamic_value_is_refused_and_not_written(env):
    e, path, data_dir, run_dir = env
    fs = ForcedSettings(APP, {"dlss_mode": "Quality"}, run_dir, data_dir, env=e)
    rec = fs.apply()
    assert "dlss_mode" in rec.refused and rec.changed == []
    assert option(path, "DLSS")["value"] == "Balanced"
    rec = fs.finish()
    assert rec.restored is None and not rec.verified                  # nothing written, nothing to put back


def test_positions_are_learned_from_the_file(env):
    e, path, data_dir, run_dir = env
    ForcedSettings(APP, {}, run_dir, data_dir, env=e).apply()
    assert load_indices(data_dir)[APP]["dlss_mode"] == {"Balanced": 3}
    # the user picked Quality once in the game: the next run may force either value
    data = sample()
    data["data"][0]["options"][0].update(value="Quality", index=2)
    path.write_text(json.dumps(data), encoding="utf-8")
    fs = ForcedSettings(APP, {"dlss_mode": "Balanced"}, run_dir, data_dir, env=e)
    rec = fs.apply()
    assert rec.changed == ["dlss_mode"] and option(path, "DLSS")["index"] == 3
    assert load_indices(data_dir)[APP]["dlss_mode"] == {"Balanced": 3, "Quality": 2}


def test_missing_settings_file_changes_nothing(tmp_path):
    fs = ForcedSettings(APP, {"ray_tracing": False}, tmp_path / "run", tmp_path / "data",
                        env={"LOCALAPPDATA": str(tmp_path / "none")})
    with pytest.raises(OSError, match="start the game once"):
        fs.apply()
    assert not (tmp_path / "run" / BACKUP_NAME).exists()


def test_malformed_indices_file_is_ignored(tmp_path):
    (tmp_path / INDICES_FILENAME).write_text("not json", encoding="utf-8")
    assert load_indices(tmp_path) == {}
    adapter = adapter_for(APP)
    assert learn_indices(tmp_path, APP, adapter, sample())["resolution"] == {"2560x1440": 0}

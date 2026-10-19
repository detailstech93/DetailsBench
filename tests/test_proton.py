"""Linux: a Proton game's settings inside its Wine prefix (app/games/proton.py), on a fake home folder."""

import json
import os

from app.games import proton
from app.games.settings_file import ForcedSettings, adapter_for, read_for_dialog
from app.games.rise_tomb_raider import APP_ID as ROTTR
from tests.test_game_settings_file import option, sample

APP = "1091500"


def fake_home(tmp_path, old_names=False):
    """~/.local/share/Steam (no prefix for the game) and a second library on another drive that has it."""
    home = tmp_path / "home"
    steam = home / ".local" / "share" / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    other = tmp_path / "games"
    (other / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0"\n {\n  "path" "%s"\n }\n "1"\n {\n  "path" "%s"\n }\n}\n'
        % (str(steam).replace("\\", "\\\\"), str(other).replace("\\", "\\\\")), encoding="utf-8")
    user = other / "steamapps" / "compatdata" / APP / "pfx" / "drive_c" / "users" / "steamuser"
    local = user / ("Local Settings/Application Data" if old_names else "AppData/Local")
    settings = local / "CD Projekt Red" / "Cyberpunk 2077" / "UserSettings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(sample(), indent=4), encoding="utf-8")
    (user / ("My Documents" if old_names else "Documents")).mkdir()
    return home, steam, user, settings


def test_game_env_points_into_the_prefix_of_any_library(tmp_path):
    home, steam, user, settings = fake_home(tmp_path)
    assert proton.prefix_for(APP, home) == user.parents[2]
    assert proton.prefix_for("570", home) is None                       # never started: no prefix
    env = proton.game_env(APP, platform="linux", home=home)
    assert env["PCBM_STEAM_ROOT"] == str(steam)
    assert env["LOCALAPPDATA"] == str(user / "AppData" / "Local") and env["PCBM_DOCUMENTS"] == str(user / "Documents")
    assert adapter_for(APP).settings_path(env) == settings
    assert proton.game_env("570", platform="linux", home=home) == {"PCBM_STEAM_ROOT": str(steam)}
    windows = {"LOCALAPPDATA": "C:\\x"}
    assert proton.game_env(APP, platform="win32", base=windows) is windows   # Windows: unchanged
    old = proton.prefix_env(fake_home(tmp_path / "old", old_names=True)[2].parents[2])
    assert old["LOCALAPPDATA"].endswith(os.path.join("Local Settings", "Application Data"))
    assert old["PCBM_DOCUMENTS"].endswith("My Documents")


def test_registry_games_are_not_forced_on_linux():
    assert proton.supported(adapter_for(APP), "linux") and proton.supported(adapter_for(APP), "win32")
    assert not proton.supported(adapter_for(ROTTR), "linux") and proton.supported(adapter_for(ROTTR), "win32")
    assert not proton.supported(None, "win32")


def test_forced_settings_in_the_prefix_are_written_and_put_back(tmp_path):
    home, _, _, settings = fake_home(tmp_path)
    env = proton.game_env(APP, platform="linux", home=home)
    original = settings.read_bytes()
    choices, current = read_for_dialog(APP, tmp_path / "data", env=env)
    assert current["ray_tracing"] is True and "ray_tracing" in choices
    fs = ForcedSettings(APP, {"ray_tracing": False}, tmp_path / "run", tmp_path / "data", env=env)
    assert fs.apply().changed == ["ray_tracing"] and option(settings, "RayTracing")["value"] is False
    assert fs.finish().restored and settings.read_bytes() == original


def test_linux_panel_forces_settings_except_registry_games(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    import sys
    QApplication.instance() or QApplication(sys.argv)
    from app.games.settings_file import DECLARED_KEY, SAVED_KEY
    from app.paths import load_settings, save_setting
    from tests.test_frame_capture_ui import make_panel
    p = make_panel(tmp_path, platform_name="linux")
    save_setting(p.data_dir, SAVED_KEY, {APP: {"ray_tracing": False}, str(ROTTR): {"ray_tracing": False}})
    save_setting(p.data_dir, DECLARED_KEY, {str(ROTTR): {"ray_tracing": True}})
    assert p.can_force(APP) and not p.can_force(ROTTR)
    assert p.forced_settings_for(APP) == {"ray_tracing": False} and p.forced_settings_for(ROTTR) == {}
    assert p.declared_settings_for(ROTTR) == {"ray_tracing": True} and p.declared_settings_for(APP) == {}
    from types import SimpleNamespace
    spec = SimpleNamespace(game_name="Cyberpunk 2077", app_id=APP, launch_args=[], settle_seconds=5, seconds=60,
                           warmup_runs=0, repetitions=3, wait_seconds=lambda: 200.0)
    text = p.linux_text(spec, SimpleNamespace(name="p"), 180, {}, {"ray_tracing": False}, False)
    assert "Forced game settings: " in text and "compatdata/1091500/pfx" in text
    assert "not forced on Linux" not in text
    assert "declared by you" in p.linux_text(spec, SimpleNamespace(name="p"), 180, {"ray_tracing": True})
    assert load_settings(p.data_dir)[0][SAVED_KEY][APP] == {"ray_tracing": False}

"""Linux: a Proton game's settings inside its Wine prefix (app/games/proton.py), on a fake home folder."""

import json
import os

import pytest

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


def test_registry_games_are_forced_on_linux_too():
    assert proton.supported(adapter_for(APP), "linux") and proton.supported(adapter_for(APP), "win32")
    assert proton.supported(adapter_for(ROTTR), "linux") and proton.supported(adapter_for(ROTTR), "win32")
    assert not proton.supported(None, "win32")


USER_REG = ('WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n'
            '[Software\\\\Eidos Montreal\\\\Shadow of the Tomb Raider\\\\Graphics] 1795117706\n'
            '#time=1dc5a0b8f1e2c3a\n"Fullscreen"=dword:00000001\n"ImageWidth"=dword:00000780\n'
            '"ImageHeight"=dword:00000438\n"Name"="text"\n\n'
            '[Software\\\\Other] 1\n"ImageWidth"=dword:00000001\n')


def test_wine_registry_is_read_and_written_like_windows(tmp_path):
    from app.games.settings_file import RegistryStore, WineReg
    from app.games.shadow_tomb_raider import REGISTRY_KEY
    path = tmp_path / "user.reg"
    path.write_text(USER_REG, encoding="utf-8")
    reg = WineReg(path, busy=lambda: False)
    assert reg.read(REGISTRY_KEY.upper()) == {"Fullscreen": 1, "ImageWidth": 1920, "ImageHeight": 1080}
    reg.set_dword(REGISTRY_KEY, "imagewidth", 1280)                  # names without case, as Windows
    reg.set_dword(REGISTRY_KEY, "Vsync", 0)                          # a new one goes at the end of its key
    text = path.read_text(encoding="utf-8")
    assert '"imagewidth"=dword:00000500' in text and '"ImageWidth"=dword:00000001' in text   # other key untouched
    assert reg.read(REGISTRY_KEY)["Vsync"] == 0 and text.count("Vsync") == 1
    assert text.split("\n")[:4] == USER_REG.split("\n")[:4] and not list(tmp_path.glob("*.tmp"))
    with pytest.raises(OSError):
        reg.read("Software\\Nobody")                                # never started: as on Windows
    assert RegistryStore(REGISTRY_KEY, reg).exists()


def test_wine_registry_is_not_written_while_the_game_wine_runs(tmp_path):
    from app.games.settings_file import WineReg
    from app.games.shadow_tomb_raider import REGISTRY_KEY
    path = tmp_path / "user.reg"
    path.write_text(USER_REG, encoding="utf-8")
    slept = []
    WineReg(path, busy=lambda: len(slept) < 3, sleep=slept.append).set_dword(REGISTRY_KEY, "Fullscreen", 0)
    assert len(slept) == 3 and WineReg(path, busy=lambda: False).read(REGISTRY_KEY)["Fullscreen"] == 0
    with pytest.raises(OSError):
        WineReg(path, busy=lambda: True, sleep=lambda s: None).set_dword(REGISTRY_KEY, "Fullscreen", 1)
    assert WineReg(path, busy=lambda: False).read(REGISTRY_KEY)["Fullscreen"] == 0


def test_the_prefix_registry_is_the_store_on_linux(tmp_path):
    from app.games.settings_file import WineReg
    from app.games.shadow_tomb_raider import APP_ID as SOTTR
    pfx = tmp_path / "pfx"
    store = adapter_for(SOTTR).store(proton.prefix_env(pfx))
    assert isinstance(store.reg, WineReg) and store.reg.path == pfx / "user.reg"


def test_forced_settings_in_the_prefix_are_written_and_put_back(tmp_path):
    home, _, _, settings = fake_home(tmp_path)
    env = proton.game_env(APP, platform="linux", home=home)
    original = settings.read_bytes()
    choices, current = read_for_dialog(APP, tmp_path / "data", env=env)
    assert current["dlss_frame_gen"] is True and "dlss_frame_gen" in choices
    fs = ForcedSettings(APP, {"dlss_frame_gen": False}, tmp_path / "run", tmp_path / "data", env=env)
    assert fs.apply().changed == ["dlss_frame_gen"] and option(settings, "DLSSFrameGen")["value"] is False
    assert fs.finish().restored and settings.read_bytes() == original


def test_linux_panel_forces_settings_registry_games_too(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    import sys
    QApplication.instance() or QApplication(sys.argv)
    from app.games.settings_file import DECLARED_KEY, SAVED_KEY
    from app.paths import load_settings, save_setting
    from tests.test_frame_capture_ui import make_panel
    p = make_panel(tmp_path, platform_name="linux")
    save_setting(p.data_dir, SAVED_KEY, {APP: {"ray_tracing": False}, str(ROTTR): {"ray_tracing": False}})
    save_setting(p.data_dir, DECLARED_KEY, {str(ROTTR): {"ray_tracing": True}})
    assert p.can_force(APP) and p.can_force(ROTTR)                    # its registry is in the prefix
    assert p.forced_settings_for(ROTTR) == {"ray_tracing": False} and p.declared_settings_for(ROTTR) == {}
    from types import SimpleNamespace
    spec = SimpleNamespace(game_name="Cyberpunk 2077", app_id=APP, launch_args=[], settle_seconds=5, seconds=60,
                           warmup_runs=0, repetitions=3, wait_seconds=lambda: 200.0, steps=[], start_keys=[])
    text = p.linux_text(spec, SimpleNamespace(name="p"), 180, {}, {"ray_tracing": False}, False)
    assert "Forced game settings: " in text and "compatdata/1091500/pfx" in text
    assert "not forced on Linux" not in text
    assert "declared by you" in p.linux_text(spec, SimpleNamespace(name="p"), 180, {"ray_tracing": True})
    assert load_settings(p.data_dir)[0][SAVED_KEY][APP] == {"ray_tracing": False}


def test_the_proton_that_ran_the_game_is_read_from_its_compatdata(tmp_path):
    # owner, 2026-11-19: Linux game results keep their Proton version
    home = fake_home(tmp_path)[0]
    other = tmp_path / "games"
    tool = other / "steamapps" / "common" / "Proton 11.0"
    tool.mkdir(parents=True)
    (tool / "version").write_text("1755700000 proton-11.0-2\n", encoding="utf-8")
    (other / "steamapps" / "compatdata" / APP / "config_info").write_text(
        f"11.1000-200\n{tool}/files/share/fonts/\n{tool}/files/lib/\n", encoding="utf-8")
    assert proton.proton_used(APP, home) == "Proton 11.0 (proton-11.0-2)"
    assert proton.proton_note(APP, "linux", home) == "Proton: Proton 11.0 (proton-11.0-2)"
    assert proton.proton_note(APP, "win32", home) is None
    assert proton.proton_used("730", home) is None                  # native, or never run: nothing said


def test_a_linux_game_result_shows_its_proton_in_the_library():
    from app.library import attribution as at
    from tests.test_library_attribution import FEDORA
    a = at.attribute("steam_1091500", "game", FEDORA, note="Average FPS; Proton: Proton 11.0 (proton-11.0-2)")
    assert a["context"]["proton"] == "Proton 11.0 (proton-11.0-2)"

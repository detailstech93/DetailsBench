import pytest

from app.games import counter_strike2 as cs2
from app.games import doom2016 as doom
from app.games.launch_settings import NOT_SEEN
from app.games.settings_file import ForcedSettings, read_for_dialog
from app.steam.launch import build_launch_url
from app.ui.frame_capture_panel import game_launch_args

LOCAL = '// local\nconfigVersion 7\nr_renderAPI "0"\nr_mode "17"\n'
CFG = 'unbindall\nbind "w" "_forward"\nr_swapInterval "0"\nm_smooth "1"\n'
VIDEO = '"video.cfg"\n{\n\t"setting.defaultres"\t\t"2560"\n\t"setting.defaultresheight"\t\t"1440"\n' \
        '\t"setting.fullscreen"\t\t"1"\n\t"setting.nowindowborder"\t\t"0"\n}\n'


def doom_files(tmp_path):
    base = tmp_path / "saved" / "id Software" / "DOOM" / "base"
    base.mkdir(parents=True)
    (base / "DOOMConfig.local").write_bytes(LOCAL.encode())
    (base / "DOOMConfig.cfg").write_bytes(CFG.encode())
    return base, {"PCBM_SAVED_GAMES": str(tmp_path / "saved")}


def test_doom_read_plan_and_args():
    data = doom.parse({"DOOMConfig.local": LOCAL, "DOOMConfig.cfg": CFG})
    assert "bind" not in data and doom.read_settings(data) == {"resolution_mode": "17", "graphics_api": "OpenGL",
                                                                "vsync": False}
    known = doom.merge_indices({}, doom.observed_indices(data))
    assert doom.choices(data, known) == {"resolution_mode": ["17"], "graphics_api": ["OpenGL", "Vulkan"],
                                         "vsync": [False, True]}
    changes, refused = doom.plan(data, {"resolution_mode": "3", "graphics_api": "Vulkan", "vsync": "on"}, known)
    assert [c.key for c in changes] == ["graphics_api"]
    assert refused == {"resolution_mode": NOT_SEEN, "vsync": "not one of: False, True"}
    assert doom.launch_args({"vsync": True, "graphics_api": "Vulkan", "resolution_mode": "17"}) == \
        ["+r_mode", "17", "+r_renderAPI", "1", "+r_swapInterval", "1"]


def test_doom_flow_checks_the_saved_values_and_puts_the_files_back(tmp_path):
    base, env = doom_files(tmp_path)
    fs = ForcedSettings("379720", {"graphics_api": "Vulkan", "vsync": True}, tmp_path / "run", tmp_path / "data",
                        env=env)
    rec = fs.apply()
    assert rec.refused == {} and rec.launch_args == ["+r_renderAPI", "1", "+r_swapInterval", "1"]
    assert (base / "DOOMConfig.local").read_text() == LOCAL                  # nothing written before the launch
    (base / "DOOMConfig.local").write_text(LOCAL.replace('r_renderAPI "0"', 'r_renderAPI "1"'))   # the game saves
    (base / "DOOMConfig.cfg").write_text(CFG.replace('r_swapInterval "0"', 'r_swapInterval "1"'))
    rec = fs.finish()
    assert rec.verified and rec.restored is True and rec.unchecked == []
    assert (base / "DOOMConfig.local").read_text() == LOCAL and (base / "DOOMConfig.cfg").read_text() == CFG


def test_doom_values_the_files_do_not_hold_are_not_checked(tmp_path):
    base, env = doom_files(tmp_path)
    fs = ForcedSettings("379720", {"vsync": True, "graphics_api": "Vulkan"}, tmp_path / "run", tmp_path / "data",
                        env=env)
    fs.apply()
    (base / "DOOMConfig.cfg").write_text("unbindall\n")                      # the game dropped r_swapInterval
    rec = fs.finish()
    assert rec.unchecked == ["vsync"] and rec.mismatch_after_run == {"graphics_api": ["Vulkan", "OpenGL"]}
    assert not rec.verified and rec.to_dict()["unchecked"] == ["vsync"]


def cs2_root(tmp_path, *accounts):
    for a in accounts:
        cfg = tmp_path / "steam" / "userdata" / a / "730" / "local" / "cfg"
        cfg.mkdir(parents=True)
        (cfg / "cs2_video.txt").write_text(VIDEO)
    return {"PCBM_STEAM_ROOT": str(tmp_path / "steam")}


def test_cs2_reads_one_account_and_never_names_it(tmp_path):
    env = cs2_root(tmp_path, "123456789")
    files, where = cs2.settings_files(env)
    assert "123456789" not in where and list(files) == ["cs2_video.txt", "cs2_machine_convars.vcfg"]
    choices, current = read_for_dialog("730", tmp_path / "data", env=env)
    assert current == {"resolution": "2560x1440", "window_mode": "Fullscreen"}           # no convars file
    assert choices["resolution"] == ["1280x720", "1920x1080", "2560x1440", "3840x2160"]
    assert choices["window_mode"] == ["Fullscreen", "Borderless", "Windowed"]
    files, where = cs2.settings_files(cs2_root(tmp_path / "two", "1", "2"))
    assert files == {} and "more than one" in where


def test_cs2_fps_max_from_the_machine_convars_only(tmp_path):
    cfg = tmp_path / "steam" / "userdata" / "7" / "730" / "local" / "cfg"
    cfg.mkdir(parents=True)
    (cfg / "cs2_machine_convars.vcfg").write_text('"config"\n{\n\t"convars"\n\t{\n\t\t"fps_max"\t\t"400"\n\t}\n}\n')
    env = {"PCBM_STEAM_ROOT": str(tmp_path / "steam")}
    choices, current = read_for_dialog("730", tmp_path / "data", env=env)
    assert current == {"fps_limit": "400"} and choices["fps_limit"] == ["0", "60", "144", "240", "400"]
    fs = ForcedSettings("730", {"fps_limit": "144", "resolution": "1920x1080"}, tmp_path / "run",
                        tmp_path / "data", env=env)
    assert fs.apply().launch_args == ["-w", "1920", "-h", "1080", "+fps_max", "144"]
    rec = fs.finish()                                   # the game did not save fps_max 144
    assert rec.unchecked == ["resolution"] and rec.mismatch_after_run == {"fps_limit": ["144", "400"]}


def test_cs2_without_a_file_launches_and_records_unchecked(tmp_path):
    env = {"PCBM_STEAM_ROOT": str(tmp_path / "steam")}
    choices, current = read_for_dialog("730", tmp_path / "data", env=env)
    assert current == {} and "Borderless" in choices["window_mode"]
    fs = ForcedSettings("730", {"resolution": "1920x1080", "window_mode": "Borderless"}, tmp_path / "run",
                        tmp_path / "data", env=env)
    rec = fs.apply()
    assert rec.launch_args == ["-w", "1920", "-h", "1080", "-windowed", "-noborder"] and rec.file == cs2.WHERE
    rec = fs.finish()
    assert rec.unchecked == ["resolution", "window_mode"] and rec.restored is None and not rec.verified


def test_cs2_file_is_only_read(tmp_path):
    env = cs2_root(tmp_path, "42")
    video = tmp_path / "steam" / "userdata" / "42" / "730" / "local" / "cfg" / "cs2_video.txt"
    fs = ForcedSettings("730", {"resolution": "1920x1080"}, tmp_path / "run", tmp_path / "data", env=env)
    fs.apply()
    video.write_text(VIDEO.replace('"2560"', '"1920"').replace('"1440"', '"1080"'))        # the game saves
    rec = fs.finish()
    assert rec.verified and rec.restored is None and '"1920"' in video.read_text()
    assert any("set them back in the game's menu" in n for n in rec.notes)


def test_launch_url_and_panel_args():
    assert build_launch_url(379720, ["379720"], ["+r_mode", "17", "-w", "-1"]) == \
        "steam://run/379720//+r_mode 17 -w -1/"
    for bad in ("+r_mode 1", "a/b", "17\n", "1.5"):
        with pytest.raises(ValueError):
            build_launch_url(379720, ["379720"], [bad])
    assert game_launch_args("379720", {"graphics_api": "Vulkan"}) == ["+r_renderAPI", "1"]
    assert game_launch_args("1091500", {"preset": "Low"}) == ["--launcher-skip", "-skipStartScreen"]

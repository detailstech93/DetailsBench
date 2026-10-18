"""Force Counter-Strike 2 settings with launch arguments: -w/-h, -fullscreen/-windowed/-noborder, +fps_max.

Steam/userdata/<account>/730/local/cfg holds cs2_video.txt (resolution, window mode) and
cs2_machine_convars.vcfg (fps_max), both KeyValues text. That folder is named after the Steam account, so
the app only reads these two files (allowed by the user on 2026-10-18), never writes them, and never stores
or shows its real path (where says <account>). On this computer CS2 wrote no cs2_video.txt on 2026-10-18,
even after a video setting was applied: resolution and window mode are then recorded as not checked; the
window still offers the common resolutions and every window mode. More than one account folder with these
files: none is read (which one is in use is not known).
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from app.games import launch_settings as ls
from app.games.launch_settings import Change, Option  # noqa: F401  (Change is used by callers)

APP_ID = 730
LAUNCH_ARGS: List[str] = []
RESTORE = False                                   # read only: the files are in the account's folder
FILES = ("cs2_video.txt", "cs2_machine_convars.vcfg")
WHERE = "Steam/userdata/<account>/730/local/cfg (" + ", ".join(FILES) + ")"
COMMON = tuple((r, "") for r in ("1280x720", "1920x1080", "2560x1440", "3840x2160"))
FPS = tuple((f, "") for f in ("0", "60", "144", "240"))         # fps_max 0: no limit
WINDOW_ARGS = {"fullscreen": ["-fullscreen"], "borderless": ["-windowed", "-noborder"], "windowed": ["-windowed"]}

SETTINGS = {
    "resolution": Option("resolution", ("setting.defaultres", "setting.defaultresheight"), COMMON,
                         lambda v: ["-w", v.split("x")[0], "-h", v.split("x")[1]]),
    "window_mode": Option("map", ("pcbm.window",),
                          (("Fullscreen", "fullscreen"), ("Borderless", "borderless"), ("Windowed", "windowed")),
                          lambda t: list(WINDOW_ARGS[t])),
    "fps_limit": Option("learned", ("fps_max",), FPS, lambda v: ["+fps_max", v]),
}


def _steam_root(env: Mapping[str, str]) -> Optional[Path]:
    if env.get("PCBM_STEAM_ROOT"):
        return Path(env["PCBM_STEAM_ROOT"])
    if env is not os.environ or os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None


def settings_files(env: Mapping[str, str]) -> Tuple[Dict[str, Path], str]:
    root = _steam_root(env)
    found = [p.parent for name in FILES for p in root.glob("userdata/*/730/local/cfg/" + name)] if root else []
    folders = sorted(set(found))
    if len(folders) > 1:
        return {}, WHERE + " (more than one Steam account has them; none is read)"
    return ({name: folders[0] / name for name in FILES} if folders else {}), WHERE


def parse(files: Dict[str, str]) -> Dict[str, str]:
    data = {}
    for name in FILES:
        data.update(re.findall(r'"([^"]+)"\s+"([^"]*)"', (files or {}).get(name, "")))
    if data.get("setting.fullscreen") == "1":
        data["pcbm.window"] = "fullscreen"
    elif data.get("setting.nowindowborder") == "1":
        data["pcbm.window"] = "borderless"
    elif data.get("setting.fullscreen") == "0":
        data["pcbm.window"] = "windowed"
    return data


def read_settings(data):
    return ls.read_settings(SETTINGS, data)


def observed_indices(data):
    return ls.observed_indices(SETTINGS, data)


merge_indices = ls.merge_indices


def choices(data, known):
    return ls.choices(SETTINGS, data, known)


def plan(data, desired, known):
    return ls.plan(SETTINGS, data, desired, known)


def launch_args(values):
    return ls.launch_args(SETTINGS, values)


def verify(data, desired):
    return ls.verify(SETTINGS, data, desired)

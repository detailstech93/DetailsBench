"""Force Counter-Strike 2 resolution and window mode with launch arguments (-w/-h, -fullscreen/-windowed/-noborder).

The game's video settings are in Steam/userdata/<account>/730/local/cfg/cs2_video.txt (KeyValues text).
That folder is named after the Steam account, so the app only reads the file (allowed by the user on
2026-10-18), never writes it, and never stores or shows its real path (where says <account>). On this
computer the file did not exist yet on 2026-10-18 (CS2 not started since then): the window still offers the
common resolutions and every window mode, and the values are checked after the run once the game wrote it.
More than one account with a cs2_video.txt: none is read (which one is in use is not known).
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from app.games import launch_settings as ls
from app.games.launch_settings import Change, Option  # noqa: F401  (Change is used by callers)

APP_ID = 730
LAUNCH_ARGS: List[str] = []
RESTORE = False                                   # read only: the file is in the account's folder
WHERE = "Steam/userdata/<account>/730/local/cfg/cs2_video.txt"
COMMON = tuple((r, "") for r in ("1280x720", "1920x1080", "2560x1440", "3840x2160"))
WINDOW_ARGS = {"fullscreen": ["-fullscreen"], "borderless": ["-windowed", "-noborder"], "windowed": ["-windowed"]}

SETTINGS = {
    "resolution": Option("resolution", ("setting.defaultres", "setting.defaultresheight"), COMMON,
                         lambda v: ["-w", v.split("x")[0], "-h", v.split("x")[1]]),
    "window_mode": Option("map", ("pcbm.window",),
                          (("Fullscreen", "fullscreen"), ("Borderless", "borderless"), ("Windowed", "windowed")),
                          lambda t: list(WINDOW_ARGS[t])),
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
    found = sorted(root.glob("userdata/*/730/local/cfg/cs2_video.txt")) if root else []
    if len(found) > 1:
        return {}, WHERE + " (more than one Steam account has one; none is read)"
    return {"cs2_video.txt": found[0]} if found else {}, WHERE


def parse(files: Dict[str, str]) -> Dict[str, str]:
    data = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', (files or {}).get("cs2_video.txt", "")))
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

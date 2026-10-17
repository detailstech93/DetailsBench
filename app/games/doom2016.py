"""Force DOOM (2016) settings with launch arguments: +r_mode, +r_renderAPI, +r_swapInterval (console variables).

Saved Games/id Software/DOOM/base/DOOMConfig.cfg and DOOMConfig.local hold `name "value"` lines (read on this
computer on 2026-10-18: r_mode "17" and r_renderAPI "0" in .local, r_swapInterval "0" in .cfg; most values are
not written while they are at the game's default). The game may save the forced values into these files
when it closes, so both files are copied before the launch and put back afterwards if their bytes changed.
r_mode is a position in the game's resolution list, which the files do not name: only positions this game
has written before are offered. r_renderAPI: 0 OpenGL, 1 Vulkan. r_swapInterval: 0 VSync off, 1 on.
"""

import re
from pathlib import Path
from typing import Dict, List, Mapping, Tuple

from app.games import launch_settings as ls
from app.games.launch_settings import Change, Option  # noqa: F401  (Change is used by callers)

APP_ID = 379720
LAUNCH_ARGS: List[str] = []
RESTORE = True
FOLDER = ("id Software", "DOOM", "base")
FILES = ("DOOMConfig.cfg", "DOOMConfig.local")      # .local is read last and wins

SETTINGS = {
    "resolution_mode": Option("learned", ("r_mode",), (), lambda v: ["+r_mode", v]),
    "graphics_api": Option("map", ("r_renderAPI",), (("OpenGL", "0"), ("Vulkan", "1")),
                           lambda t: ["+r_renderAPI", t]),
    "vsync": Option("map", ("r_swapInterval",), ((False, "0"), (True, "1")), lambda t: ["+r_swapInterval", t]),
}


def saved_games_dir(env: Mapping[str, str]):
    if env.get("PCBM_SAVED_GAMES"):
        return Path(env["PCBM_SAVED_GAMES"])
    home = env.get("USERPROFILE") or env.get("HOME")
    return Path(home) / "Saved Games" if home else None


def settings_files(env: Mapping[str, str]) -> Tuple[Dict[str, Path], str]:
    root = saved_games_dir(env)
    folder = root.joinpath(*FOLDER) if root else None
    where = str(folder) if folder else "Saved Games/" + "/".join(FOLDER)
    return ({name: folder / name for name in FILES} if folder else {}), where


def parse(files: Dict[str, str]) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for name in FILES:
        for key, value in re.findall(r'^\s*(\w+)\s+"([^"]*)"', (files or {}).get(name, ""), re.M):
            if key not in ("bind", "unbind"):
                data[key] = value
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

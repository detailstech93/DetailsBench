"""Where a Proton game keeps its Windows settings on Linux (owner, 2026-10-19: Linux games).

Steam runs each Proton game in its own Wine prefix, <library>/steamapps/compatdata/<appid>/pfx; the game sees
drive_c/users/steamuser as its Windows user folder (Proton's user is always "steamuser"). Its AppData\\Local,
Documents and Saved Games are folders there, so the settings adapters (app/games/settings_file.py) work on them
unchanged through an environment that points into the prefix. Older prefixes use Wine's older names
("Local Settings/Application Data", "My Documents"); the existing one is used.

Read only: the prefix is looked up in every Steam library (~/.steam/steam, ~/.local/share/Steam and the
libraries libraryfolders.vdf lists). A game that has never run has no prefix: its settings are then "not found;
start the game once first", as on Windows. Settings kept in the registry (Rise of the Tomb Raider) live in the
prefix's user.reg, which the app does not edit: not forced on Linux.
"""

import os
import sys
from pathlib import Path
from typing import List, Mapping, Optional

STEAM_ROOTS = (".steam/steam", ".local/share/Steam")
USER = ("drive_c", "users", "steamuser")


def steam_roots(home: Path) -> List[Path]:
    out = []
    for rel in STEAM_ROOTS:
        root = home / rel
        if root.is_dir() and all(root.resolve() != r.resolve() for r in out):
            out.append(root)
    return out


def libraries(home: Path) -> List[Path]:
    from app.steam.library import library_paths
    found: List[Path] = []
    for root in steam_roots(home):
        try:
            paths, _ = library_paths(str(root))
        except Exception:
            paths = [str(root)]
        for p in paths:
            if all(Path(p).resolve() != f.resolve() for f in found):
                found.append(Path(p))
    return found


def prefix_for(app_id, home: Optional[Path] = None) -> Optional[Path]:
    """The game's Wine prefix (…/compatdata/<appid>/pfx) in any Steam library, or None."""
    home = Path(home) if home is not None else Path.home()
    for library in libraries(home):
        pfx = library / "steamapps" / "compatdata" / str(app_id) / "pfx"
        if (pfx.joinpath(*USER)).is_dir():
            return pfx
    return None


def _first(user: Path, *names: str) -> Path:
    for name in names:
        if (user / name).is_dir():
            return user / name
    return user / names[0]


def prefix_env(pfx: Path) -> dict:
    """The environment the settings adapters read: the Windows folders inside the prefix."""
    user = pfx.joinpath(*USER)
    return {"USERPROFILE": str(user),
            "LOCALAPPDATA": str(_first(user, os.path.join("AppData", "Local"),
                                       os.path.join("Local Settings", "Application Data"))),
            "PCBM_DOCUMENTS": str(_first(user, "Documents", "My Documents")),
            "PCBM_SAVED_GAMES": str(user / "Saved Games")}


def game_env(app_id, platform: str = sys.platform, base: Optional[Mapping[str, str]] = None,
             home: Optional[Path] = None) -> Mapping[str, str]:
    """Windows: the process environment, unchanged. Linux: the game's prefix folders (when it has one) and the
    Steam folder (Counter-Strike 2 keeps its settings under Steam's userdata, also natively)."""
    base = os.environ if base is None else base
    if not platform.startswith("linux"):
        return base
    home = Path(home) if home is not None else Path.home()
    env = {}
    roots = steam_roots(home)
    if roots:
        env["PCBM_STEAM_ROOT"] = str(roots[0])
    pfx = prefix_for(app_id, home)
    if pfx is not None:
        env.update(prefix_env(pfx))
    return env


def supported(adapter, platform: str = sys.platform) -> bool:
    """Can this adapter's settings be forced on this system? (Linux: not the registry ones.)"""
    return adapter is not None and not (platform.startswith("linux") and adapter.base == "registry")

"""Windows installed-software discovery (registry reads only, nothing changed).

The registry module is injectable so tests feed a fake registry into the
REAL reading code.
"""

import os
from typing import Iterator, List, Optional, Tuple

from app.discovery.matcher import match_software
from app.discovery.result import DiscoveryResult, store_status
from app.discovery.store_parsers import parse_epic_manifest, parse_gog_entry

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
# Store locations come from community documentation; not verified on this machine
EPIC_MANIFESTS = os.path.join("Epic", "EpicGamesLauncher", "Data", "Manifests")
GOG_GAMES = r"SOFTWARE\WOW6432Node\GOG.com\Games"


def _uninstall_roots(reg) -> List[Tuple[int, int, str]]:
    return [
        (reg.HKEY_LOCAL_MACHINE, reg.KEY_WOW64_64KEY, "registry HKLM (64-bit)"),
        (reg.HKEY_LOCAL_MACHINE, reg.KEY_WOW64_32KEY, "registry HKLM (32-bit)"),
        (reg.HKEY_CURRENT_USER, 0, "registry HKCU"),
    ]


def _value(reg, key, name: str) -> Optional[str]:
    try:
        value = reg.QueryValueEx(key, name)[0]
    except OSError:
        return None
    return value if isinstance(value, str) else None


def read_uninstall_entries(reg) -> Iterator[dict]:
    """Yield raw installed-program records. Callers must pass them through
    match_software() at once; raw records are never stored."""
    for hive, view, source in _uninstall_roots(reg):
        try:
            root = reg.OpenKey(hive, UNINSTALL, 0, reg.KEY_READ | view)
        except OSError:
            continue
        with root:
            count = reg.QueryInfoKey(root)[0]
            for i in range(count):
                try:
                    with reg.OpenKey(root, reg.EnumKey(root, i)) as sub:
                        name = _value(reg, sub, "DisplayName")
                        if name:
                            yield {"name": name,
                                   "version": _value(reg, sub, "DisplayVersion"),
                                   "location": _value(reg, sub, "InstallLocation"),
                                   "source": source}
                except OSError:
                    continue


def find_steam_client(reg, isfile=os.path.isfile) -> dict:
    """Steam client location from the registry; libraries are Phase 3C."""
    candidates = [
        (reg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (reg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (reg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam", "SteamPath"),
    ]
    for hive, subkey, value_name in candidates:
        try:
            with reg.OpenKey(hive, subkey) as key:
                path = _value(reg, key, value_name)
        except OSError:
            continue
        if path:
            path = os.path.normpath(path)
            source = f"registry {subkey}\\{value_name}"
            if isfile(os.path.join(path, "steam.exe")):
                return store_status("detected", path, source)
            return store_status("not detected", path, source + " (steam.exe missing)")
    return store_status("not detected", None, "registry Valve\\Steam keys absent")


def read_epic_games(program_data: Optional[str]) -> Tuple[dict, List[dict]]:
    """Epic games from launcher manifests (*.item). Returns (store status, games)."""
    if not program_data:
        return store_status("not detected", None, "ProgramData env var missing"), []
    folder = os.path.join(program_data, EPIC_MANIFESTS)
    if not os.path.isdir(folder):
        return store_status("not detected", folder, "manifest folder absent"), []
    games = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".item"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                game = parse_epic_manifest(f.read())
        except (OSError, UnicodeDecodeError):
            game = None
        if game:
            games.append(game)
    return store_status("detected", folder, "Epic manifests (field names unverified)"), games


def read_gog_games(reg) -> Tuple[dict, List[dict]]:
    """GOG games from the GOG.com registry key. Returns (store status, games)."""
    try:
        root = reg.OpenKey(reg.HKEY_LOCAL_MACHINE, GOG_GAMES)
    except OSError:
        return store_status("not detected", None, f"registry {GOG_GAMES} absent"), []
    games = []
    with root:
        for i in range(reg.QueryInfoKey(root)[0]):
            try:
                key_name = reg.EnumKey(root, i)
                with reg.OpenKey(root, key_name) as sub:
                    values = {n: _value(reg, sub, n) for n in ("gameName", "path", "gameID", "ver")}
            except OSError:
                continue
            game = parse_gog_entry(key_name, values)
            if game:
                games.append(game)
    return store_status("detected", None, f"registry {GOG_GAMES} (field names unverified)"), games


def discover_windows(reg=None) -> DiscoveryResult:
    if reg is None:
        import winreg as reg
    result = DiscoveryResult()

    def installed():
        result.software = match_software(read_uninstall_entries(reg))

    def steam():
        result.stores["steam"] = find_steam_client(reg)

    def epic():
        result.stores["epic"], games = read_epic_games(os.environ.get("ProgramData"))
        result.games.extend(games)

    def gog():
        result.stores["gog"], games = read_gog_games(reg)
        result.games.extend(games)

    result.run_step("installed_software", installed)
    result.run_step("steam_client", steam)
    result.run_step("epic_games", epic)
    result.run_step("gog_games", gog)
    return result

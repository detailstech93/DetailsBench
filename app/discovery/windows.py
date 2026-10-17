"""Windows installed-software discovery (registry reads only, nothing changed).

The registry module is injectable so tests feed a fake registry into the
REAL reading code.
"""

import os
from typing import Iterator, List, Optional, Tuple

from app.discovery.matcher import match_software
from app.discovery.result import DiscoveryResult, store_status

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


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


def discover_windows(reg=None) -> DiscoveryResult:
    if reg is None:
        import winreg as reg
    result = DiscoveryResult()

    def installed():
        result.software = match_software(read_uninstall_entries(reg))

    def steam():
        result.stores["steam"] = find_steam_client(reg)

    result.run_step("installed_software", installed)
    result.run_step("steam_client", steam)
    return result

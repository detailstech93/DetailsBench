"""The resolutions this PC's screens offer now (Windows), to check a forced game resolution before the launch.

A game asked for a resolution its screen does not offer falls back to its own (2026-10-23: Cyberpunk 2077 asked
2560x1440 on a 1920x1080 screen ran at 1024x768; the whole benchmark ran, then was stored as invalid). Now that
value is refused before the game starts (app/games/settings_file.py), with the screen's own largest mode.
Read only (EnumDisplayDevices / EnumDisplaySettings); None when unknown (not Windows, or the read failed): then
nothing is refused and the check after the run still catches a fallback.
"""

import re
import sys
from typing import Dict, Optional, Set, Tuple

SIZE = re.compile(r"^\s*(\d{3,5})\s*[x×]\s*(\d{3,5})\s*$", re.IGNORECASE)


def parse(value) -> Optional[Tuple[int, int]]:
    m = SIZE.match(str(value or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _win32():
    """(user32, DEVMODEW, DISPLAY_DEVICEW) for reading display modes."""
    import ctypes
    from ctypes import wintypes

    class DEVMODEW(ctypes.Structure):
        _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                    ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
                    ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                    ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                    ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                    ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                    ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                    ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                    ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                    ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                    ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD)]

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]

    return ctypes.windll.user32, DEVMODEW, DISPLAY_DEVICEW


def resolutions() -> Optional[Set[Tuple[int, int]]]:
    """Every width x height an attached screen offers, or None when it cannot be read."""
    modes = _modes()
    return {(w, h) for w, h, _ in modes} if modes else None


def refresh_rates() -> Optional[Dict[Tuple[int, int], int]]:
    """The highest refresh rate (Hz) the screens offer at each width x height, or None when unknown."""
    modes = _modes()
    if not modes:
        return None
    out: Dict[Tuple[int, int], int] = {}
    for w, h, hz in modes:
        out[(w, h)] = max(out.get((w, h), 0), hz)
    return out


# Proton's own sizes (ValveSoftware/wine proton_11.0, dlls/win32u/sysparams.c default_sizes): with its emulated
# mode setting (on by default) a game is shown these that fit the screen, the screen's own modes from 800x600 up,
# the screen's size, and 640x480 + 960x540. Checked on the GEEKOM (Fedora 45, XWayland, 2026-11-19): this list puts
# 1920x1080 at 15 in Cyberpunk's list, the position the game itself saved (the X server's modes alone gave 13).
PROTON_SIZES = [(800, 600), (1024, 768), (1600, 1200), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440),
                (2880, 1620), (3200, 1800), (1440, 900), (1680, 1050), (1920, 1200), (2560, 1600), (1440, 960),
                (1920, 1280), (2560, 1080), (1920, 800), (3840, 1600), (1280, 1024)]
XRANDR_MODE = re.compile(r"^\s+(\d+)x(\d+)i?\s+(.*)$")


def proton_modes(xrandr_text: str) -> Optional[Set[Tuple[int, int, int]]]:
    """The (width, height, Hz) a Proton game is shown on this X screen (XWayland too), from `xrandr` output."""
    sizes, current = set(), []
    for line in xrandr_text.splitlines():
        m = XRANDR_MODE.match(line)
        if not m:
            continue
        w, h = int(m.group(1)), int(m.group(2))
        sizes.add((w, h))
        if "*" in m.group(3):
            rate = re.search(r"([\d.]+)\*", m.group(3))
            current.append((w, h, round(float(rate.group(1))) if rate else 60))
    if not current:
        return None
    mw, mh, hz = max(current, key=lambda c: c[0] * c[1])
    fits = lambda w, h: w <= mw and h <= mh
    shown = {(w, h) for w, h in sizes if fits(w, h) and w >= 800 and h >= 600}
    shown |= {s for s in PROTON_SIZES if fits(*s)} | {(640, 480), (960, 540)}
    return {(w, h, 60) for w, h in shown} | {(mw, mh, max(60, hz))}


def _modes() -> Optional[Set[Tuple[int, int, int]]]:
    """Every (width, height, Hz) an attached screen offers, or None when it cannot be read."""
    if sys.platform.startswith("linux"):
        import shutil
        import subprocess
        xrandr = shutil.which("xrandr")
        if not xrandr:
            return None
        try:
            done = subprocess.run([xrandr, "--query"], capture_output=True, timeout=10, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proton_modes(done.stdout.decode("utf-8", "replace")) if done.returncode == 0 else None
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32, DEVMODEW, DISPLAY_DEVICEW = _win32()
        modes: Set[Tuple[int, int, int]] = set()
        for i in range(32):
            device = DISPLAY_DEVICEW()
            device.cb = ctypes.sizeof(device)
            if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(device), 0):
                break
            if not device.StateFlags & 1:           # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
                continue
            for k in range(2000):
                mode = DEVMODEW()
                mode.dmSize = ctypes.sizeof(mode)
                if not user32.EnumDisplaySettingsW(device.DeviceName, k, ctypes.byref(mode)):
                    break
                modes.add((int(mode.dmPelsWidth), int(mode.dmPelsHeight), int(mode.dmDisplayFrequency)))
        return modes or None
    except Exception:
        return None


def current_mode() -> Optional[str]:
    """The main screen's mode now, "1920x1080 at 60 Hz" (a game in exclusive full screen sets it to its own
    resolution while it runs: shown in the game report as a test condition), or None when unknown."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32, DEVMODEW, _ = _win32()
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(mode)
        if not user32.EnumDisplaySettingsW(None, -1, ctypes.byref(mode)):     # ENUM_CURRENT_SETTINGS
            return None
        hz = int(mode.dmDisplayFrequency)
        return f"{int(mode.dmPelsWidth)}x{int(mode.dmPelsHeight)}" + (f" at {hz} Hz" if hz > 1 else "")
    except Exception:
        return None


def desktop_resolution() -> Optional[str]:
    """The main screen's resolution now, "1920x1080", or None when unknown."""
    return (current_mode() or "").split(" ")[0] or None


def refusal(value, modes: Optional[Set[Tuple[int, int]]]) -> Optional[str]:
    """Why a forced resolution cannot be used on these screens, or None (offered, unknown, or not a size)."""
    size = parse(value)
    if size is None or not modes or size in modes:
        return None
    w, h = max(modes, key=lambda m: m[0] * m[1])
    return (f"the screen does not offer {size[0]}x{size[1]} now (its largest mode is {w}x{h}); the game would "
            "fall back to another resolution (seen 2026-10-23: 1024x768). Pick a resolution the screen offers in "
            "the game preset, or run it on the screen that has this one")

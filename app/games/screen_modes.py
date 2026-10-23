"""The resolutions this PC's screens offer now (Windows), to check a forced game resolution before the launch.

A game asked for a resolution its screen does not offer falls back to its own (2026-10-23: Cyberpunk 2077 asked
2560x1440 on a 1920x1080 screen ran at 1024x768; the whole benchmark ran, then was stored as invalid). Now that
value is refused before the game starts (app/games/settings_file.py), with the screen's own largest mode.
Read only (EnumDisplayDevices / EnumDisplaySettings); None when unknown (not Windows, or the read failed): then
nothing is refused and the check after the run still catches a fallback.
"""

import re
import sys
from typing import Optional, Set, Tuple

SIZE = re.compile(r"^\s*(\d{3,5})\s*[x×]\s*(\d{3,5})\s*$", re.IGNORECASE)


def parse(value) -> Optional[Tuple[int, int]]:
    m = SIZE.match(str(value or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def resolutions() -> Optional[Set[Tuple[int, int]]]:
    """Every width x height an attached screen offers, or None when it cannot be read."""
    if sys.platform != "win32":
        return None
    try:
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

        user32 = ctypes.windll.user32
        modes: Set[Tuple[int, int]] = set()
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
                modes.add((int(mode.dmPelsWidth), int(mode.dmPelsHeight)))
        return modes or None
    except Exception:
        return None


def refusal(value, modes: Optional[Set[Tuple[int, int]]]) -> Optional[str]:
    """Why a forced resolution cannot be used on these screens, or None (offered, unknown, or not a size)."""
    size = parse(value)
    if size is None or not modes or size in modes:
        return None
    w, h = max(modes, key=lambda m: m[0] * m[1])
    return (f"the screen does not offer {size[0]}x{size[1]} now (its largest mode is {w}x{h}); the game would "
            "fall back to another resolution (seen 2026-10-23: 1024x768). Pick a resolution the screen offers in "
            "the game preset, or run it on the screen that has this one")

"""Read and change Red Dead Redemption 2 graphics settings in its system.xml text.

Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml (shape read on this computer on
2026-10-18, version 37). A ref "section/name" is the first <name value="..." /> or <name>...</name> inside
<section>; only that value's characters are changed, every other byte is kept. Quality levels the game
names in the file (kSettingLevel_Low ... _Ultra) and the graphics API offer every value; numbers such as
VSync, window mode or the DLSS index carry no names here, so only values this game has written before
are offered or written (app/games/text_settings.py).
"""

import re
from typing import List, Optional

from app.games import text_settings as ts
from app.games.text_settings import Change, Setting  # noqa: F401  (Change is used by callers)

APP_ID = 1174180
LAUNCH_ARGS: List[str] = []
LEVELS = tuple((name, "kSettingLevel_" + name) for name in ("Low", "Medium", "High", "Ultra"))
ON_OFF = ((True, "true"), (False, "false"))
# DLSS and FSR as the game writes them (dlssIndex, dlssQuality, fsr2Index), read with the owner one menu change at a
# time on 2026-10-29. FSR Balanced (2) sits between the Quality (1) and Performance (3) read; FSR Ultra Performance
# and dlssIndex 1 were not read, so they are not offered
UPSCALERS = (("Off", ("0", "5", "0")),
             ("DLSS Quality", ("2", "2", "0")), ("DLSS Balanced", ("3", "1", "0")),
             ("DLSS Performance", ("4", "0", "0")), ("DLSS Ultra Performance", ("5", "3", "0")),
             ("FSR Quality", ("0", "5", "1")), ("FSR Balanced", ("0", "5", "2")), ("FSR Performance", ("0", "5", "3")))

SETTINGS = {
    "resolution": Setting("resolution", ("video/screenWidth", "video/screenHeight")),
    "window_mode": Setting("learned", ("video/windowed",)),
    "refresh_rate": Setting("learned", ("video/refreshRateNumerator",)),
    "vsync": Setting("learned", ("video/vSync",)),
    "graphics_api": Setting("map", ("advancedGraphics/API",),
                            (("Vulkan", "kSettingAPI_Vulkan"), ("DX12", "kSettingAPI_DX12"))),
    "texture_quality": Setting("map", ("graphics/textureQuality",), LEVELS),
    "shadow_quality": Setting("map", ("graphics/shadowQuality",), LEVELS),
    "lighting_quality": Setting("map", ("graphics/lightingQuality",), LEVELS),
    "volumetrics_quality": Setting("map", ("graphics/volumetricsQuality",), LEVELS),
    "reflection_quality": Setting("map", ("graphics/reflectionQuality",), LEVELS),
    "ambient_occlusion": Setting("learned", ("graphics/ssao",)),
    "anti_aliasing": Setting("learned", ("graphics/taa",)),
    "msaa": Setting("learned", ("graphics/msaa",)),
    "fxaa": Setting("map", ("graphics/fxaaEnabled",), ON_OFF),
    "upscaler": Setting("map", ("graphics/dlssIndex", "graphics/dlssQuality", "graphics/fsr2Index"), UPSCALERS),
    "motion_blur": Setting("map", ("advancedGraphics/motionBlur",), ON_OFF),
}


def parse(text: str) -> str:
    return text


def dump(data: str) -> str:
    return data


def _span(text: str, ref: str) -> Optional[tuple]:
    section, name = ref.split("/")
    block = re.search(r"<%s>(.*?)</%s>" % (section, section), text, re.S)
    if not block:
        return None
    n = re.escape(name)
    m = re.search(r'<%s value="([^"]*)" />|<%s>([^<]*)</%s>' % (n, n, n), block.group(1))
    if not m:
        return None
    g = 1 if m.group(1) is not None else 2
    return block.start(1) + m.start(g), block.start(1) + m.end(g)


def get(text, ref: str) -> Optional[str]:
    span = _span(text, ref) if isinstance(text, str) else None
    return text[span[0]:span[1]] if span else None


def put(text: str, ref: str, value: str) -> str:
    start, end = _span(text, ref)
    return text[:start] + value + text[end:]


def read_settings(data):
    return ts.read_settings(SETTINGS, get, data)


def observed_indices(data):
    return ts.observed_indices(SETTINGS, get, data)


merge_indices = ts.merge_indices


def _screens():
    """{"WxH": highest Hz} the screens offer now ({} when unknown): the game takes any of them in exclusive full
    screen (owner, 2026-10-29, as in Shadow of the Tomb Raider)."""
    from app.games import screen_modes
    return {f"{w}x{h}": hz for (w, h), hz in (screen_modes.refresh_rates() or {}).items()}


def choices(data, known):
    out = ts.choices(SETTINGS, get, data, known)
    if "resolution" in out:
        out["resolution"] = sorted(set(out["resolution"]) | set(_screens()))
    return out


FULL_SCREEN = "0"           # video/windowed: 0 exclusive full screen, 1 windowed, 2 borderless
SHOWN = {"window_mode": {"0": "Full screen", "1": "Windowed", "2": "Borderless"}}     # named in the game report
NOT_FULL_SCREEN = ("windowed or borderless mode (video/windowed {mode}) draws at the screen's own resolution and "
                   "resets this value (seen 2026-10-19: 2560x1440 asked, 1024x768 after the run); leave the window "
                   "mode out, or force 0 (exclusive full screen)")


def plan(data, desired, known):
    desired = desired or {}
    screens = _screens()
    want = desired.get("resolution")
    if want in screens:     # a resolution the screen offers is written even if the game never used it
        known = ts.merge_indices(known, {"resolution": {want: 0}})
    changes, refused = ts.plan(SETTINGS, get, data, desired, known)
    if "resolution" in desired and "resolution" not in refused:
        mode = desired.get("window_mode", get(data, "video/windowed"))
        if "window_mode" in desired and str(mode) != FULL_SCREEN:
            refused["resolution"] = NOT_FULL_SCREEN.format(mode=mode)
            changes = [c for c in changes if c.key != "resolution"]
        else:
            # exclusive full screen, so the game surely runs at it (borderless ran at the desktop's resolution);
            # the game's own window mode is put back after the run like the rest
            if mode is not None and str(mode) != FULL_SCREEN:
                changes.append(Change("window_mode", mode, FULL_SCREEN))
            # and at the screen's own refresh rate: a Remote Desktop session left 32 Hz in the file (2026-10-18)
            hz, now = screens.get(want), get(data, "video/refreshRateNumerator")
            if hz and now is not None and "refresh_rate" not in desired and now != str(hz):
                changes.append(Change("refresh_rate", now, str(hz)))
    # V-Sync off for the run (owner, 2026-10-29: on, it caps the benchmark at the refresh rate); put back after
    vsync = get(data, "video/vSync")
    if "vsync" not in desired and vsync not in (None, "0"):
        changes.append(Change("vsync", vsync, "0"))
    return changes, refused


def apply(data, changes):
    return ts.apply(SETTINGS, put, data, changes)


def verify(data, desired):
    return ts.verify(SETTINGS, get, data, desired)

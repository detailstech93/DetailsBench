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

SETTINGS = {
    "resolution": Setting("resolution", ("video/screenWidth", "video/screenHeight")),
    "window_mode": Setting("learned", ("video/windowed",)),
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
    "dlss": Setting("learned", ("graphics/dlssIndex",)),
    "dlss_mode": Setting("learned", ("graphics/dlssQuality",)),
    "fsr": Setting("learned", ("graphics/fsr2Index",)),
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


def choices(data, known):
    return ts.choices(SETTINGS, get, data, known)


def plan(data, desired, known):
    return ts.plan(SETTINGS, get, data, desired, known)


def apply(data, changes):
    return ts.apply(SETTINGS, put, data, changes)


def verify(data, desired):
    return ts.verify(SETTINGS, get, data, desired)

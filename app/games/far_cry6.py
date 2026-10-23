"""Read and change Far Cry 6 graphics settings in its gamerprofile.xml.

Documents/My Games/Far Cry 6/gamerprofile.xml (shape read on this computer on 2026-10-23, game 1.7.0). A ref
"Element@attribute" is that attribute of the first <Element ...> tag; only its value's characters are changed,
every other byte is kept. Seen then: RenderProfile Quality="high" (the preset), VideoModeProfile="1" with the
game's menu showing Fullscreen, VideoModeProfileFullscreen width/height 1920x1080, DxrReflections and DxrShadows
"0" (ray tracing off), ResolutionConfig FidelityFxSuperResolutionEnabled "0" (FSR 1.0 off). The game has no DLSS
and no frame generation. Only "high" was seen for the preset; low/medium/ultra are the game's own preset names
written the same way, and the check after the run shows whether the game kept them.
"""

import re
from typing import List, Optional

from app.games import text_settings as ts
from app.games.text_settings import Change, Setting  # noqa: F401  (Change is used by callers)

APP_ID = 2369390
LAUNCH_ARGS: List[str] = []
ON_OFF = ((True, "1"), (False, "0"))
NOT_IN_GAME = {"frame_gen": "the game has none", "dlss": "the game has none"}     # for the game report
SHOWN = {"window_mode": {"1": "Full screen"}}       # file values named in the game report (seen in its menu)

SETTINGS = {
    "resolution": Setting("resolution", ("VideoModeProfileFullscreen@width", "VideoModeProfileFullscreen@height")),
    "window_mode": Setting("learned", ("RenderProfile@VideoModeProfile",)),
    "preset": Setting("map", ("RenderProfile@Quality",),
                      (("Low", "low"), ("Medium", "medium"), ("High", "high"), ("Ultra", "ultra"))),
    "dxr_reflections": Setting("map", ("RenderProfile@DxrReflections",), ON_OFF),
    "dxr_shadows": Setting("map", ("RenderProfile@DxrShadows",), ON_OFF),
    "upscaler": Setting("map", ("ResolutionConfig@FidelityFxSuperResolutionEnabled",),
                        (("Off", "0"), ("FSR 1.0", "1"))),
    "resolution_scale": Setting("learned", ("RenderProfile@ResolutionScale",)),
    "vsync": Setting("learned", ("RenderProfile@VSyncPreset",)),
}


def parse(text: str) -> str:
    return text


def dump(data: str) -> str:
    return data


def _span(text: str, ref: str) -> Optional[tuple]:
    element, attribute = ref.split("@")
    tag = re.search(r"<%s\b[^>]*>" % re.escape(element), text)
    if not tag:
        return None
    m = re.search(r'\s%s="([^"]*)"' % re.escape(attribute), tag.group(0))
    if not m:
        return None
    return tag.start() + m.start(1), tag.start() + m.end(1)


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


FULL_SCREEN = "1"           # RenderProfile VideoModeProfile 1: the menu showed Fullscreen (2026-10-23)
NOT_FULL_SCREEN = ("the game is not in full screen (VideoModeProfile {mode}); the full-screen resolution is not "
                   "the one it draws at then. Choose Fullscreen once in the game's Monitor settings")


def plan(data, desired, known):
    changes, refused = ts.plan(SETTINGS, get, data, desired, known)
    if "resolution" in (desired or {}) and "resolution" not in refused:
        mode = (desired or {}).get("window_mode", get(data, "RenderProfile@VideoModeProfile"))
        if mode is not None and str(mode) != FULL_SCREEN:
            refused["resolution"] = NOT_FULL_SCREEN.format(mode=mode)
            changes = [c for c in changes if c.key != "resolution"]
    return changes, refused


def apply(data, changes):
    return ts.apply(SETTINGS, put, data, changes)


def verify(data, desired):
    return ts.verify(SETTINGS, get, data, desired)

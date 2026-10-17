"""Read and change Rise of the Tomb Raider graphics settings: DWORD values in the registry.

HKEY_CURRENT_USER\\Software\\Crystal Dynamics\\Rise of the Tomb Raider\\Graphics (54 DWORDs, read on this
computer on 2026-10-18). data is {value name: int}; app/games/settings_file.py reads and writes the key.
On/off values are 0/1. The graphics API is EnableDX12. Quality levels, VSync and DLSS are numbers whose
meaning the game does not store, so only numbers this game has used before (learned like the other
adapters) are offered or written; pick another one once in the game's menu to make it available.
"""

import copy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

APP_ID = 391220
LAUNCH_ARGS: List[str] = []
REGISTRY_KEY = "Software\\Crystal Dynamics\\Rise of the Tomb Raider\\Graphics"

# key -> (kind, registry value names); kind: "bool", "api", "learned" (a number), "resolution"
SETTINGS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "resolution": ("resolution", ("FullscreenWidth", "FullscreenHeight")),
    "fullscreen": ("bool", ("Fullscreen",)),
    "exclusive_fullscreen": ("bool", ("ExclusiveFullscreen",)),
    "vsync": ("learned", ("VSync",)),
    "graphics_api": ("api", ("EnableDX12",)),
    "dlss": ("learned", ("DLSS",)),
    "texture_quality": ("learned", ("TextureQuality",)),
    "shadow_quality": ("learned", ("ShadowQuality",)),
    "ambient_occlusion": ("learned", ("AmbientOcclusionQuality",)),
    "level_of_detail": ("learned", ("LevelOfDetail",)),
    "ssaa": ("learned", ("SSAAQuality",)),
    "fxaa": ("bool", ("FXAA",)),
    "smaa": ("bool", ("SMAA",)),
    "motion_blur": ("bool", ("MotionBlur",)),
}
API = {"DX11": 0, "DX12": 1}
NOT_SEEN = "not seen in this game yet; choose it once in the game's menu so the app can learn it"


@dataclass(frozen=True)
class Change:
    key: str
    old: object
    new: object
    index: Optional[int] = None


def _int(data, name) -> Optional[int]:
    v = data.get(name) if isinstance(data, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def read_settings(data) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for key, (kind, names) in SETTINGS.items():
        values = [_int(data, n) for n in names]
        if None in values:
            continue
        if kind == "bool" and values[0] in (0, 1):
            out[key] = values[0] == 1
        elif kind == "api":
            out.update({key: name for name, v in API.items() if v == values[0]})
        elif kind == "learned":
            out[key] = values[0]
        elif kind == "resolution":
            out[key] = f"{values[0]}x{values[1]}"
    return out


def observed_indices(data) -> Dict[str, Dict[str, int]]:
    """Learned numbers and resolutions, as {key: {text: 0}} (JSON keys are text)."""
    now = read_settings(data)
    return {k: {str(now[k]): 0} for k, (kind, _) in SETTINGS.items()
            if kind in ("learned", "resolution") and k in now}


def merge_indices(known, seen) -> Dict[str, Dict[str, int]]:
    known = known if isinstance(known, dict) else {}
    seen = seen if isinstance(seen, dict) else {}
    out = {}
    for k in list(known) + [k for k in seen if k not in known]:
        out[k] = {**(known.get(k) if isinstance(known.get(k), dict) else {}),
                  **(seen.get(k) if isinstance(seen.get(k), dict) else {})}
    return out


def _seen(known, key) -> List[str]:
    inner = known.get(key) if isinstance(known, dict) else None
    return list(inner) if isinstance(inner, dict) else []


def choices(data, known) -> Dict[str, list]:
    now = read_settings(data)
    out: Dict[str, list] = {}
    for key, (kind, _) in SETTINGS.items():
        if key not in now:
            continue
        if kind == "bool":
            out[key] = [True, False]
        elif kind == "api":
            out[key] = list(API)
        elif kind == "learned":
            out[key] = sorted({now[key]} | {int(t) for t in _seen(known, key) if t.isdigit()})
        else:
            out[key] = sorted({now[key]} | set(_seen(known, key)))
    return out


def plan(data, desired: Dict[str, object], known) -> Tuple[List[Change], Dict[str, str]]:
    now = read_settings(data)
    changes: List[Change] = []
    refused: Dict[str, str] = {}
    for key, want in (desired or {}).items():
        if key not in SETTINGS:
            refused[key] = "unknown setting"
            continue
        kind = SETTINGS[key][0]
        if key not in now:
            refused[key] = "not in the settings"
            continue
        if now[key] == want and type(now[key]) is type(want):
            continue
        if kind == "bool" and not isinstance(want, bool):
            refused[key] = "expects true or false"
        elif kind == "api" and want not in API:
            refused[key] = "not one of: " + ", ".join(API)
        elif kind == "learned" and (not isinstance(want, int) or isinstance(want, bool)):
            refused[key] = "expects a whole number"
        elif kind in ("learned", "resolution") and str(want) not in _seen(known, key):
            refused[key] = NOT_SEEN
        else:
            changes.append(Change(key, now[key], want))
    return changes, refused


def apply(data, changes: List[Change]) -> Dict[str, int]:
    out = copy.deepcopy(data)
    for c in changes:
        kind, names = SETTINGS[c.key]
        if kind == "bool":
            out[names[0]] = 1 if c.new else 0
        elif kind == "api":
            out[names[0]] = API[c.new]
        elif kind == "learned":
            out[names[0]] = c.new
        else:
            w, h = str(c.new).split("x")
            out[names[0]], out[names[1]] = int(w), int(h)
    return out


def verify(data, desired: Dict[str, object]) -> Dict[str, Tuple[object, object]]:
    now = read_settings(data)
    return {k: (v, now.get(k)) for k, v in (desired or {}).items()
            if now.get(k) != v or type(now.get(k)) is not type(v)}

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

# key -> (kind, registry value names[, names]); kind: "bool", "api", "learned" (a number), "resolution",
# "named" ({menu name: number}, matched with the owner) or "preset" ({preset name: one number per value name})
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


def read_settings(data, spec=None) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for key, (kind, names, *named) in (spec or SETTINGS).items():
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
        elif kind == "named":
            out[key] = next((name for name, v in named[0].items() if v == values[0]), values[0])
        elif kind == "preset":
            out[key] = next((name for name, v in named[0].items() if list(v) == values), "Custom")
    return out


def observed_indices(data, spec=None) -> Dict[str, Dict[str, int]]:
    """Learned numbers and resolutions, as {key: {text: 0}} (JSON keys are text)."""
    now = read_settings(data, spec)
    return {k: {str(now[k]): 0} for k, (kind, *_) in (spec or SETTINGS).items()
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


def choices(data, known, spec=None) -> Dict[str, list]:
    now = read_settings(data, spec)
    out: Dict[str, list] = {}
    for key, (kind, _, *named) in (spec or SETTINGS).items():
        if key not in now:
            continue
        if kind in ("named", "preset"):
            out[key] = list(named[0])
        elif kind == "bool":
            out[key] = [True, False]
        elif kind == "api":
            out[key] = list(API)
        elif kind == "learned":
            out[key] = sorted({now[key]} | {int(t) for t in _seen(known, key) if t.isdigit()})
        else:
            out[key] = sorted({now[key]} | set(_seen(known, key)) | _screen())
    return out


def _screen() -> set:
    """Every resolution this PC's screens offer ("WxH"): the game takes any of them (owner, 2026-10-29: the chosen
    2560x1440 was not written because the game had never been seen using it)."""
    from app.games import screen_modes
    return {f"{w}x{h}" for w, h in screen_modes.resolutions() or ()}


def plan(data, desired: Dict[str, object], known, spec=None) -> Tuple[List[Change], Dict[str, str]]:
    spec = spec or SETTINGS
    now = read_settings(data, spec)
    changes: List[Change] = []
    refused: Dict[str, str] = {}
    for key, want in (desired or {}).items():
        if key not in spec:
            refused[key] = "unknown setting"
            continue
        kind = spec[key][0]
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
        elif kind in ("named", "preset") and want not in spec[key][2]:
            refused[key] = "not one of: " + ", ".join(spec[key][2])
        elif kind == "learned" and str(want) not in _seen(known, key):
            refused[key] = NOT_SEEN
        elif kind == "resolution" and str(want) not in set(_seen(known, key)) | _screen():
            refused[key] = NOT_SEEN
        else:
            changes.append(Change(key, now[key], want))
    # a chosen resolution in exclusive full screen, so the game surely runs at it (owner, 2026-10-29: it ran at
    # the desktop's 1080p); the game's own window mode is put back after the run like the rest
    desired = desired or {}
    if ("resolution" in desired and "resolution" not in refused and "exclusive_fullscreen" in spec
            and now.get("fullscreen") is True and now.get("exclusive_fullscreen") is False
            and "exclusive_fullscreen" not in desired):
        changes.append(Change("exclusive_fullscreen", False, True))
    return changes, refused


def apply(data, changes: List[Change], spec=None) -> Dict[str, int]:
    out = copy.deepcopy(data)
    for c in changes:
        kind, names, *named = (spec or SETTINGS)[c.key]
        if kind == "named":
            out[names[0]] = named[0][c.new]
        elif kind == "preset":
            out.update(zip(names, named[0][c.new]))
        elif kind == "bool":
            out[names[0]] = 1 if c.new else 0
        elif kind == "api":
            out[names[0]] = API[c.new]
        elif kind == "learned":
            out[names[0]] = c.new
        else:
            w, h = str(c.new).split("x")
            out[names[0]], out[names[1]] = int(w), int(h)
    return out


def verify(data, desired: Dict[str, object], spec=None) -> Dict[str, Tuple[object, object]]:
    now = read_settings(data, spec)
    return {k: (v, now.get(k)) for k, v in (desired or {}).items()
            if now.get(k) != v or type(now.get(k)) is not type(v)}

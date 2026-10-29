"""Read and change Cyberpunk 2077 graphics settings in its parsed UserSettings.json.

%LOCALAPPDATA%/CD Projekt Red/Cyberpunk 2077/UserSettings.json holds groups of options. A list option
stores its value and its position ("index"). Fixed lists carry their "values", so any listed value can be
written. Lists the game builds at runtime (resolution, DLSS mode, upscaler, preset, ...) carry no values;
a value is written only when its position was seen before (plan refuses it otherwise). verify compares
what the file says with what was asked. No file access here (app/games/settings_file.py does that; parse and dump only convert text).
Shape checked on a real file (version 140) on 2026-10-18. Drafted by the local agent, reviewed.
"""

import copy
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

APP_ID = 1091500
# Passed to this launch only (steam://run): skip REDlauncher and the "press any key" start screen.
LAUNCH_ARGS = ["--launcher-skip", "-skipStartScreen"]

SETTINGS: Dict[str, Tuple[str, str]] = {
    "resolution": ("/video/display", "Resolution"),
    "window_mode": ("/video/display", "WindowMode"),
    "vsync": ("/video/display", "VSync"),
    "fps_limit": ("/video/display", "MaximumFPS_OnOff"),
    "preset": ("/graphics/presets", "QuickPresets"),
    "dlss_mode": ("/graphics/presets", "DLSS"),
    "texture_quality": ("/graphics/presets", "TextureQuality"),
    "dynamic_resolution": ("/graphics/presets", "DynamicResolutionScaling"),
    "frame_gen": ("/graphics/presets", "FrameGeneration"),
    "dlss_frame_gen": ("/graphics/presets", "DLSSFrameGen"),
    "multi_frame_gen": ("/graphics/presets", "DLSS_MultiFrameGeneration"),
    "rt_lighting": ("/graphics/raytracing", "RayTracedLighting"),
    "path_tracing": ("/graphics/raytracing", "RayTracedPathTracing"),
}

# The game's Quick Presets Low..Ultra, read with the owner from its menu on 2026-10-29 (upscaler off): every setting
# a preset changes. Written as a whole, since the game shows "Custom" once the upscaler is changed. Ray tracing,
# the upscaler and frame generation stay separate choices.
PRESET_OPTIONS = (
    ("/gameplay/performance", "CrowdDensity"), ("/graphics/performance", "CrowdDensity"),
    ("/graphics/presets", "TextureQuality"), ("/graphics/advanced", "Anisotropy"),
    ("/graphics/advanced", "ShadowMeshQuality"), ("/graphics/advanced", "LocalShadowsQuality"),
    ("/graphics/advanced", "CascadedShadowsRange"), ("/graphics/advanced", "CascadedShadowsResolution"),
    ("/graphics/advanced", "DistantShadowsResolution"), ("/graphics/advanced", "VolumetricFogResolution"),
    ("/graphics/advanced", "VolumetricCloudsQuality"), ("/graphics/advanced", "MaxDynamicDecals"),
    ("/graphics/advanced", "ScreenSpaceReflectionsQuality"), ("/graphics/advanced", "SubsurfaceScatteringQuality"),
    ("/graphics/advanced", "AmbientOcclusion"), ("/graphics/advanced", "ColorPrecision"),
    ("/graphics/advanced", "MirrorQuality"), ("/graphics/advanced", "LODPreset"),
    ("/graphics/basic", "FilmGrain"), ("/graphics/basic", "ChromaticAberration"),
    ("/graphics/basic", "DepthOfField"), ("/graphics/basic", "MotionBlur"),
)
PRESETS: Dict[str, tuple] = {
    "Low": ("Low", "Low", "Low", 4, "Low", "Low", "Medium", "Medium", "Low", "Low", "Medium", "Low", "Low", "Low",
            "Low", "Medium", "Low", "Low", False, False, False, "Off"),
    "Medium": ("Low", "Low", "Medium", 8, "Medium", "Medium", "Medium", "Medium", "High", "High", "Medium", "Medium",
               "Medium", "Medium", "High", "Medium", "Medium", "Medium", False, False, False, "Low"),
    "High": ("Medium", "Medium", "High", 16, "High", "High", "High", "High", "High", "High", "High", "High", "High",
             "High", "High", "High", "High", "High", True, False, True, "High"),
    "Ultra": ("High", "High", "High", 16, "High", "High", "High", "High", "High", "Ultra", "Ultra", "Ultra", "Ultra",
              "High", "High", "High", "High", "High", True, True, True, "High"),
}
CUSTOM = ("Custom", 0)      # QuickPresets as the game wrote it with each of the four (2026-10-29)

# The upscaler with its mode, and ray tracing, read with the owner from the menu the same day. None leaves that
# setting as it is. The game's own ray tracing presets also turn DLSS on: here ray tracing is written alone (owner:
# ray tracing without an upscaler must stay possible). FSR 2.1 has no Ultra Performance.
UPSCALER_OPTIONS = (("/graphics/presets", "ResolutionScaling"), ("/graphics/presets", "DLSS"),
                    ("/graphics/presets", "FSR2"), ("/graphics/presets", "XESS"))
_MODES = ("Quality", "Balanced", "Performance", "Ultra Performance")
UPSCALERS: Dict[str, tuple] = {"Off": ("Off", None, None, None)}
UPSCALERS.update({f"DLSS {m}": ("DLSS", m, None, None) for m in _MODES})
UPSCALERS.update({f"FSR {m}": ("FSR2", None, m, None) for m in _MODES[:3]})
UPSCALERS.update({f"XeSS {m}": ("XeSS", None, None, m) for m in _MODES})
RT_OPTIONS = tuple(("/graphics/raytracing", n) for n in ("RayTracing", "RayTracedReflections", "RayTracedSunShadows",
                                                          "RayTracedLocalShadows", "RayTracedLighting",
                                                          "RayTracedPathTracing"))
RT_LEVELS: Dict[str, tuple] = {"Off": (False, False, False, False, "Off", False),
                               "Low": (True, False, False, True, "Off", False),
                               "Medium": (True, False, True, True, "Medium", False),
                               "Ultra": (True, True, True, True, "Ultra", False)}
# positions in the lists the game builds at runtime (no "values" in the file)
INDEX = {"ResolutionScaling": {"Off": 0, "DLSS": 1, "FSR2": 2, "XeSS": 3},
         "DLSS": {"Quality": 2, "Balanced": 3, "Performance": 4, "Ultra Performance": 5},
         "FSR2": {"Quality": 1, "Balanced": 2, "Performance": 3},
         "XESS": {"Quality": 2, "Balanced": 3, "Performance": 4, "Ultra Performance": 5}}
WHOLE = {"preset": (PRESET_OPTIONS, PRESETS), "upscaler": (UPSCALER_OPTIONS, UPSCALERS),
         "ray_tracing": (RT_OPTIONS, RT_LEVELS)}


@dataclass(frozen=True)
class Change:
    key: str
    old: object
    new: object
    index: Optional[int]


def find_option(data, group: str, name: str) -> Optional[dict]:
    if not isinstance(data, dict):
        return None

    groups = data.get("data")
    if not isinstance(groups, list):
        return None

    for group_entry in groups:
        if not isinstance(group_entry, dict):
            continue

        if group_entry.get("group_name") != group:
            continue

        options = group_entry.get("options")
        if not isinstance(options, list):
            continue

        for option in options:
            if isinstance(option, dict) and option.get("name") == name:
                return option

    return None


def read_settings(data) -> Dict[str, object]:
    result: Dict[str, object] = {}

    for key, (group, name) in SETTINGS.items():
        option = find_option(data, group, name)
        if option is not None and "value" in option:
            result[key] = option["value"]

    for key in WHOLE:
        name = _whole_in(data, key)
        if name is not None:
            result[key] = name
        elif key != "preset" and find_option(data, *WHOLE[key][0][0]) is not None:
            result[key] = "Custom"          # a mix none of the values have
    return result


def _whole_in(data, key: str) -> Optional[str]:
    """Which of a setting's values (preset Low..Ultra, upscaler, ray tracing) the file holds, option by option."""
    options, table = WHOLE[key]
    now = [(find_option(data, g, n) or {}).get("value", NOT_THERE) for g, n in options]
    return next((name for name, values in table.items()
                 if all(b is None or (a == b and type(a) is type(b)) for a, b in zip(now, values))), None)


def _allowed(option: dict, value) -> bool:
    if isinstance(value, bool):
        return option.get("type") == "bool"
    return value in (option.get("values") or INDEX.get(option.get("name"), {}))


NOT_THERE = object()


def observed_indices(data) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {}

    for key, (group, name) in SETTINGS.items():
        option = find_option(data, group, name)
        if option is None:
            continue

        if option.get("type") != "string_list":
            continue

        if isinstance(option.get("values"), list):
            continue

        value = option.get("value")
        index = option.get("index")

        if isinstance(value, str) and isinstance(index, int) and not isinstance(index, bool):
            result[key] = {value: index}

    return result


def merge_indices(
    known: Dict[str, Dict[str, int]],
    seen: Dict[str, Dict[str, int]],
) -> Dict[str, Dict[str, int]]:
    if not isinstance(known, dict):
        known = {}
    if not isinstance(seen, dict):
        seen = {}

    keys = list(known.keys())
    for key in seen.keys():
        if key not in keys:
            keys.append(key)

    result: Dict[str, Dict[str, int]] = {}

    for key in keys:
        known_inner = known.get(key)
        seen_inner = seen.get(key)

        if not isinstance(known_inner, dict):
            known_inner = {}
        if not isinstance(seen_inner, dict):
            seen_inner = {}

        merged: Dict[str, int] = {}
        merged.update(known_inner)
        merged.update(seen_inner)
        result[key] = merged

    return result


def plan(
    data,
    desired: Dict[str, object],
    known: Dict[str, Dict[str, int]],
) -> Tuple[List[Change], Dict[str, str]]:
    changes: List[Change] = []
    refused: Dict[str, str] = {}

    if not isinstance(desired, dict):
        return changes, refused

    if not isinstance(known, dict):
        known = {}

    for key, desired_value in desired.items():
        if key in WHOLE and (desired_value in WHOLE[key][1] or key != "preset"):
            options, table = WHOLE[key]
            if desired_value not in table:
                if desired_value != read_settings(data).get(key):      # "Custom" as it is: nothing to write
                    refused[key] = "not one of: " + ", ".join(table)
                continue
            missing = [n for (g, n), v in zip(options, table[desired_value])
                       if v is not None and not _allowed(find_option(data, g, n) or {}, v)]
            if missing:
                refused[key] = "not in the settings file: " + ", ".join(missing)
            elif _whole_in(data, key) != desired_value:
                changes.append(Change(key, read_settings(data).get(key), desired_value, None))
            continue

        if key not in SETTINGS:
            refused[key] = "unknown setting"
            continue

        group, name = SETTINGS[key]
        option = find_option(data, group, name)

        if option is None:
            refused[key] = "not in the settings file"
            continue

        if (
            "value" in option
            and option["value"] == desired_value
            and type(option["value"]) is type(desired_value)
        ):
            continue

        option_type = option.get("type")

        if option_type == "bool":
            if not isinstance(desired_value, bool):
                refused[key] = "expects true or false"
                continue

            changes.append(Change(key, option.get("value"), desired_value, None))

        elif option_type == "int":
            if not isinstance(desired_value, int) or isinstance(desired_value, bool):
                refused[key] = "expects a whole number"
                continue

            min_value = option.get("min_value")
            max_value = option.get("max_value")

            min_is_int = isinstance(min_value, int) and not isinstance(min_value, bool)
            max_is_int = isinstance(max_value, int) and not isinstance(max_value, bool)

            if (min_is_int and desired_value < min_value) or (max_is_int and desired_value > max_value):
                refused[key] = f"must be between {min_value} and {max_value}"
                continue

            changes.append(Change(key, option.get("value"), desired_value, None))

        elif option_type == "string_list":
            values = option.get("values")

            if isinstance(values, list):
                if not isinstance(desired_value, str) or desired_value not in values:
                    refused[key] = "not one of: " + ", ".join(str(item) for item in values)
                    continue

                index = values.index(desired_value)
                changes.append(Change(key, option.get("value"), desired_value, index))

            else:
                if not isinstance(desired_value, str):
                    refused[key] = "expects text"
                    continue

                inner = known.get(key)
                if not isinstance(inner, dict):
                    inner = {}

                index = inner.get(desired_value)

                if not isinstance(index, int) or isinstance(index, bool):
                    refused[key] = (
                        "the game's position for this value is unknown; "
                        "choose it once in the game's menu so the app can learn it"
                    )
                    continue

                changes.append(Change(key, option.get("value"), desired_value, index))

        else:
            refused[key] = "unsupported type"

    return changes, refused


def apply(data, changes: List[Change]) -> dict:
    out = copy.deepcopy(data)

    if not isinstance(changes, list):
        return out

    for change in changes:
        if not isinstance(change, Change):
            continue

        key = change.key
        if key not in SETTINGS and key not in WHOLE:
            continue

        if key in WHOLE and change.new in WHOLE[key][1]:
            options, table = WHOLE[key]
            for (group, name), value in zip(options, table[change.new]):
                option = find_option(out, group, name)
                if option is None or value is None:
                    continue
                option["value"] = value
                if isinstance(option.get("values"), list):
                    option["index"] = option["values"].index(value)
                elif name in INDEX:
                    option["index"] = INDEX[name][value]
            # the game shows "Custom" once one of these is changed by hand; a named preset would be put back on load
            option = find_option(out, *SETTINGS["preset"])
            if option is not None:
                option["value"], option["index"] = CUSTOM
            continue

        group, name = SETTINGS[key]
        option = find_option(out, group, name)

        if option is None:
            continue

        option["value"] = change.new

        if change.index is not None:
            option["index"] = change.index

    return out


def verify(data, desired: Dict[str, object]) -> Dict[str, Tuple[object, object]]:
    result: Dict[str, Tuple[object, object]] = {}

    if not isinstance(desired, dict):
        return result

    actual_settings = read_settings(data)

    for key, expected in desired.items():
        actual = actual_settings.get(key)

        if actual != expected or type(actual) is not type(expected):
            result[key] = (expected, actual)

    return result


def parse(text: str):
    return json.loads(text)


def dump(data) -> str:
    return json.dumps(data, indent=4)


def choices(data, known: Dict[str, Dict[str, int]]) -> Dict[str, list]:
    """Values offered per setting: on/off, the file's own list, or the runtime-list values seen so far."""
    out = {}
    for key, (group, name) in SETTINGS.items():
        opt = find_option(data, group, name)
        if opt is None:
            continue
        kind = opt.get("type")
        if kind == "bool":
            out[key] = [True, False]
        elif kind == "string_list" and isinstance(opt.get("values"), list):
            out[key] = [v for v in opt["values"] if isinstance(v, str)]
        elif kind == "string_list":
            seen = dict(known.get(key) or {})
            if isinstance(opt.get("value"), str) and isinstance(opt.get("index"), int):
                seen[opt["value"]] = opt["index"]
            out[key] = [v for v, _ in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))]
    for key, (options, table) in WHOLE.items():
        if all(find_option(data, g, n) for g, n in options):
            out[key] = list(table) + [v for v in out.get(key, []) if v not in table]
    return out

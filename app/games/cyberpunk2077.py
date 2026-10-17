"""Read and change Cyberpunk 2077 graphics settings in its parsed UserSettings.json.

%LOCALAPPDATA%/CD Projekt Red/Cyberpunk 2077/UserSettings.json holds groups of options. A list option
stores its value and its position ("index"). Fixed lists carry their "values", so any listed value can be
written. Lists the game builds at runtime (resolution, DLSS mode, upscaler, preset, ...) carry no values;
a value is written only when its position was seen before (plan refuses it otherwise). verify compares
what the file says with what was asked. No file access here (app/games/settings_file.py does that).
Shape checked on a real file (version 140) on 2026-10-18. Drafted by the local agent, reviewed.
"""

import copy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

APP_ID = 1091500

SETTINGS: Dict[str, Tuple[str, str]] = {
    "resolution": ("/video/display", "Resolution"),
    "window_mode": ("/video/display", "WindowMode"),
    "vsync": ("/video/display", "VSync"),
    "fps_limit": ("/video/display", "MaximumFPS_OnOff"),
    "preset": ("/graphics/presets", "QuickPresets"),
    "upscaler": ("/graphics/presets", "ResolutionScaling"),
    "dlss_mode": ("/graphics/presets", "DLSS"),
    "texture_quality": ("/graphics/presets", "TextureQuality"),
    "dynamic_resolution": ("/graphics/presets", "DynamicResolutionScaling"),
    "frame_gen": ("/graphics/presets", "FrameGeneration"),
    "dlss_frame_gen": ("/graphics/presets", "DLSSFrameGen"),
    "multi_frame_gen": ("/graphics/presets", "DLSS_MultiFrameGeneration"),
    "ray_tracing": ("/graphics/raytracing", "RayTracing"),
    "rt_lighting": ("/graphics/raytracing", "RayTracedLighting"),
    "path_tracing": ("/graphics/raytracing", "RayTracedPathTracing"),
}


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

    return result


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
        if key not in SETTINGS:
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

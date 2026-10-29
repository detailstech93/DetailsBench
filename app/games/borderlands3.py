"""Read and change Borderlands 3 graphics settings in its GameUserSettings.ini text.

Documents/My Games/Borderlands 3/Saved/Config/WindowsNoEditor/GameUserSettings.ini (CRLF, duplicate keys).
Only the first matching line of a key in [/Script/OakGame.OakGameUserSettings] is read or changed; every
other byte is kept. On/off settings, window mode (Unreal's 0 fullscreen, 1 borderless, 2 windowed) and the
graphics API offer every value; Overall Quality writes its whole preset (PRESETS). Resolution and the rest
are free text in this file, so only values this game has written before (learned by app/games/settings_file.py) are offered or written.
Shape checked on a real file on 2026-10-18. Drafted by the local agent, reviewed.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class IniData:
    lines: List[str]
    newline: str
    final_newline: bool


@dataclass(frozen=True)
class Setting:
    kind: str
    keys: Tuple[str, ...]
    values: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Change:
    key: str
    old: object
    new: object
    index: Optional[int] = None


SECTION = "/Script/OakGame.OakGameUserSettings"
APP_ID = 397540
LAUNCH_ARGS: List[str] = []

# Each Overall Quality preset as the game writes it, read with the owner from its menu on 2026-10-29.
# A preset is all of these lines; a file whose lines match none of them reads as "Custom".
SG = "ScalabilityGroups"
PRESET_KEYS: Tuple[Tuple[str, str], ...] = (
    (SECTION, "GraphicsQualityMode"), (SG, "sg.ShadowQuality"), (SG, "sg.TextureQuality"), (SG, "sg.FoliageQuality"),
    (SECTION, "bEnableCameraMotionBlur"), (SECTION, "bEnablePerObjectMotionBlur"), (SECTION, "bEnableCAS"),
    (SECTION, "TextureStreamingQuality"), (SECTION, "MaterialQuality"), (SECTION, "AntiAliasingModeIndex"),
    (SECTION, "AnisotropicFilteringMode"), (SECTION, "ShadowQualityMode"), (SECTION, "DrawDistanceMode"),
    (SECTION, "EnvironmentDetailMode"), (SECTION, "TerrainMode"), (SECTION, "FoliageMode"),
    (SECTION, "VolumetricFogMode"), (SECTION, "ScreenSpaceReflectionsMode"), (SECTION, "CharacterDetailMode"),
    (SECTION, "AmbientOcclusionQualityMode"), (SECTION, "TextureStreamingAdaptiveBoostScale"),
    (SECTION, "TextureStreamingPoolSizeMB"),
)
PRESETS: Dict[str, Tuple[str, ...]] = {
    "VeryLow": ("VeryLow", "0", "0", "0", "False", "False", "False", "Low", "Low", "0", "Trilinear", "Low", "Low",
                "Low", "Low", "Low", "Off", "Off", "Low", "Off", "0.000000", "800"),
    "Low": ("Low", "1", "1", "0", "False", "False", "False", "Medium", "Medium", "1", "TwoX", "Medium", "Low",
            "Low", "Medium", "Low", "Off", "Off", "Low", "Low", "0.250000", "1200"),
    "Medium": ("Medium", "1", "2", "1", "False", "False", "False", "High", "High", "1", "FourX", "Medium", "Medium",
               "Medium", "Medium", "Medium", "Medium", "Medium", "Medium", "Medium", "0.500000", "2000"),
    "High": ("High", "2", "2", "2", "False", "False", "True", "High", "High", "2", "EightX", "High", "High",
             "High", "High", "High", "High", "High", "High", "High", "0.500000", "2000"),
    "Ultra": ("Ultra", "3", "3", "3", "True", "True", "True", "Ultra", "Ultra", "2", "SixteenX", "Ultra", "Ultra",
              "Ultra", "Ultra", "Ultra", "High", "High", "Ultra", "High", "0.750000", "3000"),
    "Badass": ("Badass", "3", "3", "3", "True", "True", "True", "Ultra", "Ultra", "2", "SixteenX", "Ultra", "Ultra",
               "Ultra", "Ultra", "Ultra", "Ultra", "Ultra", "Ultra", "High", "0.750000", "3000"),
}


SETTINGS: Dict[str, Setting] = {
    "resolution": Setting(
        "resolution",
        (
            "ResolutionSizeX",
            "ResolutionSizeY",
            "LastUserConfirmedResolutionSizeX",
            "LastUserConfirmedResolutionSizeY",
        ),
    ),
    "window_mode": Setting(
        "map",
        ("FullscreenMode", "LastConfirmedFullscreenMode", "PreferredFullscreenMode"),
        (("Fullscreen", "0"), ("Borderless", "1"), ("Windowed", "2")),
    ),
    "vsync": Setting("bool", ("bUseVSync",)),
    "fps_limit": Setting("learned", ("FrameRateLimitMode",)),
    "preset": Setting("whole", ("GraphicsQualityMode",), tuple((name, name) for name in PRESETS)),
    "resolution_scale": Setting("learned", ("ResolutionScale",)),
    "dynamic_resolution": Setting("bool", ("bUseDynamicResolution",)),
    "anti_aliasing": Setting("learned", ("AntiAliasingModeIndex",)),
    "texture_quality": Setting("learned", ("TextureStreamingQuality",)),
    "graphics_api": Setting(
        "map",
        ("PreferredGraphicsAPI",),
        (("DX11", "DX11"), ("DX12", "DX12")),
    ),
}

def _preset_name(data: "IniData") -> Optional[str]:
    """The preset whose lines the file has (a line the file lacks is not compared); "Custom" for none."""
    if get_value(data, SECTION, "GraphicsQualityMode") is None:
        return None
    found = [(section, key, get_value(data, section, key)) for section, key in PRESET_KEYS]
    for name, values in PRESETS.items():
        if all(v is None or v == want for (_, _, v), want in zip(found, values)):
            return name
    return "Custom"


def parse(text: str) -> IniData:
    if not isinstance(text, str):
        return IniData([], "\n", False)

    newline = "\r\n" if "\r\n" in text else "\n"
    final_newline = text.endswith(newline)
    lines = text.split(newline)
    if final_newline:
        lines = lines[:-1]

    return IniData(lines, newline, final_newline)


def dump(data: IniData) -> str:
    if not isinstance(data, IniData):
        return ""
    if not isinstance(data.lines, list):
        lines: List[str] = []
    else:
        lines = [line for line in data.lines if isinstance(line, str)]
    newline = data.newline if isinstance(data.newline, str) else "\n"
    final_newline = bool(data.final_newline)

    return newline.join(lines) + (newline if final_newline else "")


def _is_ascii_digits(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return all(char in "0123456789" for char in value)


def _is_resolution_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split("x")
    if len(parts) != 2:
        return False
    return _is_ascii_digits(parts[0]) and _is_ascii_digits(parts[1])


def _known_values(known: object, key: str) -> Dict[str, int]:
    if not isinstance(known, dict):
        return {}
    value = known.get(key, {})
    if not isinstance(value, dict):
        return {}
    return value


def get_value(data: IniData, section: str, key: str) -> Optional[str]:
    if not isinstance(data, IniData):
        return None
    if not isinstance(data.lines, list):
        return None
    if not isinstance(section, str) or not isinstance(key, str):
        return None

    section_marker = "[" + section + "]"
    in_section = False

    for line in data.lines:
        if not isinstance(line, str):
            continue

        stripped = line.strip()

        if not in_section:
            if stripped == section_marker:
                in_section = True
            continue

        if stripped.startswith("["):
            break

        if stripped.startswith(";"):
            continue

        if line.lstrip().startswith(key + "="):
            eq_index = line.find("=")
            if eq_index == -1:
                return None
            return line[eq_index + 1:].rstrip()

    return None


def set_value(data: IniData, section: str, key: str, value: str) -> Optional[IniData]:
    if not isinstance(data, IniData):
        return None
    if not isinstance(data.lines, list):
        return None
    if not isinstance(section, str) or not isinstance(key, str):
        return None
    if not isinstance(value, str):
        return None

    new_lines = [line for line in data.lines if isinstance(line, str)]
    section_marker = "[" + section + "]"
    in_section = False

    for index, line in enumerate(new_lines):
        stripped = line.strip()

        if not in_section:
            if stripped == section_marker:
                in_section = True
            continue

        if stripped.startswith("["):
            break

        if stripped.startswith(";"):
            continue

        if line.lstrip().startswith(key + "="):
            leading = line[: len(line) - len(line.lstrip())]
            new_lines[index] = leading + key + "=" + value
            return IniData(new_lines, data.newline, data.final_newline)

    return None


def read_settings(data: IniData) -> Dict[str, object]:
    result: Dict[str, object] = {}
    if not isinstance(data, IniData):
        return result

    for name, setting in SETTINGS.items():
        if not setting.keys:
            continue

        if setting.kind == "bool":
            value = get_value(data, SECTION, setting.keys[0])
            if value == "True":
                result[name] = True
            elif value == "False":
                result[name] = False

        elif setting.kind == "map":
            value = get_value(data, SECTION, setting.keys[0])
            if value is not None:
                for shown, ini_text in setting.values:
                    if ini_text == value:
                        result[name] = shown
                        break

        elif setting.kind == "learned":
            value = get_value(data, SECTION, setting.keys[0])
            if value is not None:
                result[name] = value

        elif setting.kind == "whole":
            value = _preset_name(data)
            if value is not None:
                result[name] = value

        elif setting.kind == "resolution":
            if len(setting.keys) >= 2:
                width = get_value(data, SECTION, setting.keys[0])
                height = get_value(data, SECTION, setting.keys[1])
                if (
                    width is not None
                    and height is not None
                    and _is_ascii_digits(width)
                    and _is_ascii_digits(height)
                ):
                    result[name] = width + "x" + height

    return result


def observed_indices(data: IniData) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {}
    settings = read_settings(data)

    for name, setting in SETTINGS.items():
        if setting.kind not in ("learned", "resolution"):
            continue
        if name not in settings:
            continue
        value = settings[name]
        if isinstance(value, str):
            result[name] = {value: 0}

    return result


def merge_indices(known: object, seen: object) -> Dict[str, Dict[str, int]]:
    known_dict = known if isinstance(known, dict) else {}
    seen_dict = seen if isinstance(seen, dict) else {}

    result: Dict[str, Dict[str, int]] = {}
    keys = list(known_dict.keys())
    for key in seen_dict.keys():
        if key not in known_dict:
            keys.append(key)

    for key in keys:
        merged: Dict[str, int] = {}
        known_inner = known_dict.get(key)
        seen_inner = seen_dict.get(key)

        if isinstance(known_inner, dict):
            merged.update(known_inner)
        if isinstance(seen_inner, dict):
            merged.update(seen_inner)

        result[key] = merged

    return result


def choices(data: IniData, known: object) -> Dict[str, list]:
    result: Dict[str, list] = {}
    settings = read_settings(data)
    known_dict = known if isinstance(known, dict) else {}

    for name, setting in SETTINGS.items():
        if name not in settings:
            continue

        if setting.kind == "bool":
            result[name] = [True, False]

        elif setting.kind == "map":
            result[name] = [shown for shown, _ in setting.values]

        elif setting.kind == "whole":
            result[name] = [shown for shown, _ in setting.values]

        elif setting.kind in ("learned", "resolution"):
            current = settings[name]
            known_values = _known_values(known_dict, name)
            candidates = set()

            if isinstance(current, str):
                candidates.add(current)
            for key in known_values.keys():
                if isinstance(key, str):
                    candidates.add(key)

            result[name] = sorted(candidates)

    return result


def plan(
    data: IniData,
    desired: Dict[str, object],
    known: object,
) -> Tuple[List[Change], Dict[str, str]]:
    changes: List[Change] = []
    refusals: Dict[str, str] = {}

    if not isinstance(desired, dict):
        return changes, refusals

    current_settings = read_settings(data)

    for key, desired_value in desired.items():
        setting = SETTINGS.get(key)
        if setting is None:
            refusals[key] = "unknown setting"
            continue

        if key not in current_settings:
            refusals[key] = "not in the settings file"
            continue

        current = current_settings[key]
        if current == desired_value and type(current) is type(desired_value):
            continue

        kind = setting.kind

        if kind == "bool":
            if not isinstance(desired_value, bool):
                refusals[key] = "expects true or false"
                continue

        elif kind == "map":
            shown_values = [shown for shown, _ in setting.values]
            if desired_value not in shown_values:
                refusals[key] = "not one of: " + ", ".join(shown_values)
                continue

        elif kind == "whole":
            if desired_value not in PRESETS:
                refusals[key] = "not one of: " + ", ".join(PRESETS)
                continue
            missing = [k for s, k in PRESET_KEYS if get_value(data, s, k) is None]
            if missing:     # half a preset is not the preset
                refusals[key] = "not in the settings file: " + ", ".join(missing)
            else:
                changes.append(Change(key, current, desired_value))
            continue

        elif kind == "learned":
            if not isinstance(desired_value, str):
                refusals[key] = "expects text"
                continue
            if desired_value not in _known_values(known, key):
                refusals[key] = (
                    "not seen in this game yet; choose it once in the game's menu "
                    "so the app can learn it"
                )
                continue

        elif kind == "resolution":
            if not _is_resolution_text(desired_value):
                refusals[key] = "expects WIDTHxHEIGHT"
                continue
            if desired_value not in _known_values(known, key):
                refusals[key] = (
                    "not seen in this game yet; choose it once in the game's menu "
                    "so the app can learn it"
                )
                continue

        else:
            refusals[key] = "unknown setting"
            continue

        for ini_key in setting.keys:
            if get_value(data, SECTION, ini_key) is None:
                refusals[key] = "not in the settings file"
                break
        else:
            changes.append(Change(key, current, desired_value))
            continue

    return changes, refusals


def apply(data: IniData, changes: List[Change]) -> IniData:
    if not isinstance(data, IniData):
        data = IniData([], "\n", False)
    if not isinstance(changes, list):
        return data

    current = data

    for change in changes:
        if not isinstance(change, Change):
            continue

        setting = SETTINGS.get(change.key)
        if setting is None:
            continue

        value = change.new

        if setting.kind == "bool":
            if not isinstance(value, bool):
                continue
            text = "True" if value else "False"
            for key in setting.keys:
                updated = set_value(current, SECTION, key, text)
                if updated is not None:
                    current = updated

        elif setting.kind == "map":
            if not isinstance(value, str):
                continue
            text = None
            for shown, ini_text in setting.values:
                if shown == value:
                    text = ini_text
                    break
            if text is None:
                continue
            for key in setting.keys:
                updated = set_value(current, SECTION, key, text)
                if updated is not None:
                    current = updated

        elif setting.kind == "learned":
            if not isinstance(value, str):
                continue
            for key in setting.keys:
                updated = set_value(current, SECTION, key, value)
                if updated is not None:
                    current = updated

        elif setting.kind == "whole":
            for (section, key), text in zip(PRESET_KEYS, PRESETS.get(value, ())):
                updated = set_value(current, section, key, text)
                if updated is not None:
                    current = updated

        elif setting.kind == "resolution":
            if not _is_resolution_text(value):
                continue
            parts = value.split("x")
            width = parts[0]
            height = parts[1]

            for key in setting.keys:
                if key.endswith("SizeX"):
                    text = width
                elif key.endswith("SizeY"):
                    text = height
                else:
                    continue

                updated = set_value(current, SECTION, key, text)
                if updated is not None:
                    current = updated

    return current


def verify(data: IniData, desired: Dict[str, object]) -> Dict[str, Tuple[object, object]]:
    mismatches: Dict[str, Tuple[object, object]] = {}
    if not isinstance(desired, dict):
        return mismatches

    actual_settings = read_settings(data)

    for key, expected in desired.items():
        actual = actual_settings.get(key)
        if actual != expected or type(actual) is not type(expected):
            mismatches[key] = (expected, actual)

    return mismatches

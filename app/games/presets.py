"""Game presets: five simple choices (resolution, upscaler, ray tracing, frame generation, quality) turned into each game's settings.

The run dialog asks these once for one game or for every selected game. Per game, a choice becomes a value the
game's adapter can write and check (app/games/settings_file.py ForcedSettings), using only values the adapter
offers (its choices: on/off, the file's own lists, or values the game was seen using). A choice the game has no
setting for, or whose value was never seen, is not written: it is listed with the reason, never guessed.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

KEEP = "keep"
RESOLUTIONS = (KEEP, "1280x720", "1920x1080", "2560x1440", "3840x2160")
UPSCALERS = (KEEP, "off", "dlss", "fsr", "xess")
RAY_TRACING = (KEEP, "on", "off")
FRAME_GEN = (KEEP, "off", "x2", "x3", "x4")      # off, or on with that many frames shown per rendered one
QUALITIES = (KEEP, "low", "medium", "high", "ultra")

LABELS = {KEEP: "Keep the game's own", "off": "Off", "on": "On", "dlss": "DLSS", "fsr": "FSR", "xess": "XeSS",
          "low": "Low", "medium": "Medium", "high": "High", "ultra": "Ultra",
          "x2": "On, 2x", "x3": "On, 3x (multi frame)", "x4": "On, 4x (multi frame)"}
CHOICE_NAMES = {"resolution": "Resolution", "upscaler": "Upscaler", "ray_tracing": "Ray tracing",
                "frame_gen": "Frame generation", "quality": "Quality"}


@dataclass(frozen=True)
class GamePreset:
    resolution: str = KEEP
    upscaler: str = KEEP
    ray_tracing: str = KEEP
    quality: str = KEEP
    frame_gen: str = KEEP

    def chosen(self) -> Dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v != KEEP}

    def to_dict(self) -> Dict[str, str]:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(data) -> "GamePreset":
        data = data if isinstance(data, dict) else {}
        allowed = {"resolution": RESOLUTIONS, "upscaler": UPSCALERS, "ray_tracing": RAY_TRACING,
                   "frame_gen": FRAME_GEN, "quality": QUALITIES}
        out = {}
        for key, values in allowed.items():
            value = data.get(key, KEEP)
            if key == "frame_gen" and value == "on":
                value = "x2"                    # saved before x2 / x3 / x4 were offered
            if key == "resolution" and isinstance(value, str) and _is_resolution(value):
                out[key] = value
            elif value in values:
                out[key] = value
        return GamePreset(**out)


def _is_resolution(text: str) -> bool:
    w, _, h = text.partition("x")
    return w.isdigit() and h.isdigit()


def describe(preset: GamePreset) -> str:
    parts = [f"{CHOICE_NAMES[k]} {LABELS.get(v, v)}" for k, v in preset.chosen().items()]
    return ", ".join(parts) or "the game's own settings"


# ---- per game: which setting each choice changes, and how a choice is matched to an offered value --------------

Matcher = Callable[[str, list], Optional[object]]


def _exact_text(choice: str, offered: list) -> Optional[object]:
    """resolution "2560x1440" or quality "ultra": the offered value with that text (any case)."""
    for value in offered:
        if isinstance(value, str) and value.strip().lower() == choice.lower():
            return value
    return None


def _contains(choice: str, offered: list) -> Optional[object]:
    """upscaler: "off" is the value Off; "dlss"/"fsr"/"xess" an offered value that names it (e.g. "FSR 3")."""
    if choice == "off":
        return _exact_text("off", offered)
    found = [v for v in offered if isinstance(v, str) and choice in v.lower().replace(" ", "")]
    return found[0] if len(found) == 1 else (sorted(found)[-1] if found else None)


def _on_off(choice: str, offered: list) -> Optional[object]:
    value = choice == "on"
    return value if value in offered and any(isinstance(v, bool) for v in offered) else None


def _frame_gen(choice: str, offered: list) -> Optional[object]:
    """frame generation off / x2 / x3 / x4: an on/off switch, the Off / DLSS / FSR list (on: the DLSS value, else
    the only other one) or the multi frame list x2 / x3 / x4 (that value; off leaves it as it is)."""
    if any(isinstance(v, bool) for v in offered):
        return _on_off("off" if choice == "off" else "on", offered)
    texts = [v for v in offered if isinstance(v, str)]
    if any(v.lower() == "x2" for v in texts):
        return _exact_text(choice, texts) if choice != "off" else None
    if choice == "off":
        return _exact_text("off", texts)
    others = [v for v in texts if v.lower() != "off"]
    dlss = [v for v in others if "dlss" in v.lower()]
    return dlss[0] if dlss else (others[0] if len(others) == 1 else None)


@dataclass(frozen=True)
class Target:
    keys: Tuple[str, ...]          # the adapter's setting keys this choice writes (all of them get the same value)
    match: Matcher
    when_off: Tuple[str, ...] = ()  # keys left as they are when the choice is "off" (the rest must match)


# The settings each game's adapter knows (app/games/<game>.py SETTINGS). A choice missing here has no setting in
# the app for that game.
GAME_TARGETS: Dict[str, Dict[str, Target]] = {
    "1091500": {   # Cyberpunk 2077
        "resolution": Target(("resolution",), _exact_text),
        "upscaler": Target(("upscaler",), _contains),
        "ray_tracing": Target(("ray_tracing",), _on_off),
        "frame_gen": Target(("frame_gen", "dlss_frame_gen", "multi_frame_gen"), _frame_gen,
                            when_off=("multi_frame_gen",)),
        "quality": Target(("preset",), _exact_text),
    },
    "397540": {    # Borderlands 3
        "resolution": Target(("resolution",), _exact_text),
        "quality": Target(("preset",), _exact_text),
    },
    "1174180": {   # Red Dead Redemption 2: the four quality settings with Low..Ultra
        "resolution": Target(("resolution",), _exact_text),
        "quality": Target(("texture_quality", "shadow_quality", "lighting_quality", "reflection_quality"),
                          _exact_text),
    },
    "391220": {    # Rise of the Tomb Raider
        "resolution": Target(("resolution",), _exact_text),
    },
    "292030": {    # The Witcher 3
        "resolution": Target(("resolution",), _exact_text),
        "ray_tracing": Target(("ray_tracing",), _on_off),
    },
    "730": {       # Counter-Strike 2
        "resolution": Target(("resolution",), _exact_text),
    },
}

NO_SETTING = "this game has no such setting in the app"
NOT_SEEN = ("the game was never seen using this value: choose it once in the game's own menu (then quit the "
            "game), and the app learns it")


def plan_preset(app_id, preset: GamePreset, offered: Dict[str, list]) -> Tuple[Dict[str, object], Dict[str, str]]:
    """(settings to force {adapter key: value}, choices not applied {choice: reason}) for one game.
    offered: the adapter's choices for the game (settings_file.read_for_dialog)."""
    targets = GAME_TARGETS.get(str(app_id), {})
    requested: Dict[str, object] = {}
    skipped: Dict[str, str] = {}
    for choice, value in preset.chosen().items():
        target = targets.get(choice)
        if target is None or not any(k in offered for k in target.keys):
            skipped[choice] = NO_SETTING
            continue
        found = {}
        for key in target.keys:
            if key not in offered:
                continue
            match = target.match(value, list(offered[key]))
            if match is None and value == "off" and key in target.when_off:
                continue
            if match is None:
                found = None
                break
            found[key] = match
        if not found:
            skipped[choice] = NOT_SEEN
            continue
        requested.update(found)
    return requested, skipped


def summary_lines(app_id, game_name: str, preset: GamePreset, offered: Optional[Dict[str, list]],
                  problem: Optional[str] = None) -> List[str]:
    """Plain lines for the dialog: what will be set and what will not, for one game."""
    if not preset.chosen():
        return [f"{game_name}: the game's own settings."]
    if offered is None:
        return [f"{game_name}: settings cannot be set ({problem or 'not supported by the app'}); "
                f"the game's own are used."]
    requested, skipped = plan_preset(app_id, preset, offered)
    done = [f"{CHOICE_NAMES[c]} {LABELS.get(v, v)}" for c, v in preset.chosen().items() if c not in skipped]
    lines = [f"{game_name}: " + (", ".join(done) if done else "nothing from this preset can be set") + "."]
    for choice, why in skipped.items():
        lines.append(f"    not set: {CHOICE_NAMES[choice]} {LABELS.get(preset.chosen()[choice], preset.chosen()[choice])}"
                     f" ({why})")
    return lines


def resolutions_offered(all_offered: Sequence[Dict[str, list]]) -> List[str]:
    """The fixed list plus any resolution a game offers (sorted by pixels), for the dialog's list."""
    seen = set(RESOLUTIONS[1:])
    for offered in all_offered:
        for value in offered.get("resolution", []) or []:
            if isinstance(value, str) and _is_resolution(value):
                seen.add(value)

    def pixels(text):
        w, _, h = text.partition("x")
        return int(w) * int(h), int(w)
    return [KEEP] + sorted(seen, key=pixels)

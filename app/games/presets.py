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
RAY_TRACING = (KEEP, "off", "medium", "high", "ultra", "on")      # the levels only where the game has them
FRAME_GEN = (KEEP, "off", "x2", "x3", "x4")      # off, or on with that many frames shown per rendered one
# how strong the upscaler is (owner, 2026-10-29: DLSS strength); offered only where the game has the mode
UPSCALER_MODES = (KEEP, "quality", "balanced", "performance", "ultra_performance")
QUALITIES = (KEEP, "lowest", "low", "medium", "high", "highest", "ultra")

LABELS = {KEEP: "Keep the game's own", "off": "Off", "on": "On", "dlss": "DLSS", "fsr": "FSR", "xess": "XeSS",
          "lowest": "Lowest", "low": "Low", "medium": "Medium", "high": "High", "highest": "Highest", "ultra": "Ultra",
          "quality": "Quality", "balanced": "Balanced", "performance": "Performance",
          "ultra_performance": "Ultra Performance",
          "x2": "On, 2x", "x3": "On, 3x (multi frame)", "x4": "On, 4x (multi frame)"}
# offered only where the game's own list has them (owner, 2026-10-29: Shadow of the Tomb Raider's own presets and
# ray tracing levels), never as a value "not seen yet"
GAME_ONLY = {"quality": ("lowest", "highest"), "ray_tracing": ("medium", "high", "ultra"),
             "upscaler_mode": UPSCALER_MODES[1:]}
CHOICE_NAMES = {"resolution": "Resolution", "upscaler": "Upscaler", "upscaler_mode": "Upscaler mode", "ray_tracing": "Ray tracing",
                "frame_gen": "Frame generation", "quality": "Quality"}


@dataclass(frozen=True)
class GamePreset:
    resolution: str = KEEP
    upscaler: str = KEEP
    ray_tracing: str = KEEP
    quality: str = KEEP
    frame_gen: str = KEEP
    upscaler_mode: str = KEEP       # last (positional use stays as it was); after upscaler, so its value wins

    def chosen(self) -> Dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v != KEEP}

    def to_dict(self) -> Dict[str, str]:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(data) -> "GamePreset":
        data = data if isinstance(data, dict) else {}
        allowed = {"resolution": RESOLUTIONS, "upscaler": UPSCALERS, "upscaler_mode": UPSCALER_MODES,
                   "ray_tracing": RAY_TRACING,
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
    if choice not in ("on", "off"):
        return None
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


def _sottr_dlss(choice: str, offered: list) -> Optional[object]:
    """Shadow of the Tomb Raider: DLSS on is its Quality mode; it has no FSR, and its XeSS is not matched yet."""
    return _exact_text({"off": "off", "dlss": "quality"}.get(choice, ""), offered)


def _sottr_dlss_mode(choice: str, offered: list) -> Optional[object]:
    """Its DLSS modes by name: quality -> Quality, ultra_performance -> Ultra Performance."""
    return _exact_text(choice.replace("_", " "), offered)


def _rdr2_upscaler(choice: str, offered: list) -> Optional[object]:
    """Red Dead Redemption 2: off, or DLSS / FSR in its Quality mode (the mode choice changes it)."""
    return _exact_text("off" if choice == "off" else choice + " quality", offered)


def _rdr2_upscaler_mode(choice: str, offered: list) -> Optional[object]:
    """"dlss balanced" -> DLSS Balanced; with the upscaler kept, the first upscaler that has the mode."""
    upscaler, _, mode = choice.partition(" ")
    names = [upscaler] if upscaler != KEEP else [v.split(" ")[0].lower() for v in offered if " " in str(v)]
    for name in names:
        found = _exact_text(f"{name} {mode.replace('_', ' ')}", offered)
        if found is not None:
            return found
    return None


def _upscaler_frame_gen(choice: str, offered: list) -> Optional[object]:
    """"fsr x2" -> FSR x2 (one setting holds the upscaler's frame generation and its count); off is Off."""
    return _exact_text("off", offered) if choice.endswith(" off") else _rdr2_upscaler_mode(choice, offered)


def _sottr_ray_tracing(choice: str, offered: list) -> Optional[object]:
    """Its ray traced shadows Off / Medium / High / Ultra; on is Ultra (the most ray tracing work)."""
    return _exact_text("ultra" if choice == "on" else choice, offered)


def _bl3_quality(choice: str, offered: list) -> Optional[object]:
    """Its presets Very Low..Badass: lowest is Very Low, highest is Badass."""
    return _exact_text({"lowest": "verylow", "highest": "badass"}.get(choice, choice), offered)


def _sottr_quality(choice: str, offered: list) -> Optional[object]:
    """Its presets Lowest..Highest: Ultra is Highest."""
    return _exact_text("highest" if choice == "ultra" else choice, offered)


@dataclass(frozen=True)
class Target:
    keys: Tuple[str, ...]          # the adapter's setting keys this choice writes (all of them get the same value)
    match: Matcher
    when_off: Tuple[str, ...] = ()  # keys left as they are when the choice is "off" (the rest must match)
    with_upscaler: bool = False     # the mode's matcher gets "<upscaler> <mode>": one setting holds both (RDR2)


# The settings each game's adapter knows (app/games/<game>.py SETTINGS). A choice missing here has no setting in
# the app for that game.
GAME_TARGETS: Dict[str, Dict[str, Target]] = {
    "1091500": {   # Cyberpunk 2077: presets, DLSS / FSR / XeSS with their modes and ray tracing read with the owner
        "resolution": Target(("resolution",), _exact_text),
        "upscaler": Target(("upscaler",), _rdr2_upscaler),
        "upscaler_mode": Target(("upscaler",), _rdr2_upscaler_mode, with_upscaler=True),
        "ray_tracing": Target(("ray_tracing",), _sottr_ray_tracing),
        "frame_gen": Target(("frame_gen",), _upscaler_frame_gen, with_upscaler=True),
        "quality": Target(("preset",), _exact_text),
    },
    "397540": {    # Borderlands 3
        "resolution": Target(("resolution",), _exact_text),
        "quality": Target(("preset",), _bl3_quality),
    },
    "1174180": {   # Red Dead Redemption 2: its whole quality preset Low..Ultra and DLSS / FSR, read with the owner
        "resolution": Target(("resolution",), _exact_text),
        "upscaler": Target(("upscaler",), _rdr2_upscaler),
        "upscaler_mode": Target(("upscaler",), _rdr2_upscaler_mode, with_upscaler=True),
        "quality": Target(("preset",), _exact_text),
    },
    "391220": {    # Rise of the Tomb Raider
        "resolution": Target(("resolution",), _exact_text),
    },
    "750920": {    # Shadow of the Tomb Raider: DLSS, ray traced shadows and presets matched with the owner, 2026-10-29
        "resolution": Target(("resolution",), _exact_text),
        "upscaler": Target(("dlss",), _sottr_dlss),
        "upscaler_mode": Target(("dlss",), _sottr_dlss_mode),
        "ray_tracing": Target(("ray_tracing",), _sottr_ray_tracing),
        "quality": Target(("preset",), _sottr_quality),
    },
    "292030": {    # The Witcher 3
        "resolution": Target(("resolution",), _exact_text),
        "ray_tracing": Target(("ray_tracing",), _on_off),
    },
    "2369390": {   # Far Cry 6: FSR 1.0 is its only upscaler; ray traced reflections and shadows; no frame generation
        "resolution": Target(("resolution",), _exact_text),
        "upscaler": Target(("upscaler",), _contains),
        "ray_tracing": Target(("dxr_reflections", "dxr_shadows"), _on_off),
        "quality": Target(("preset",), _exact_text),
    },
    "730": {       # Counter-Strike 2
        "resolution": Target(("resolution",), _exact_text),
    },
}

NO_SETTING = "this game has no such setting in the app"
NOT_SEEN = ("the game was never seen using this value: choose it once in the game's own menu (then quit the "
            "game), and the app learns it")


def _not_in_game(app_id, choice: str) -> Optional[str]:
    """The adapter's own word that the game has no such option at all (far_cry6.NOT_IN_GAME)."""
    try:
        from app.games.settings_file import adapter_for
        return getattr(adapter_for(app_id).module, "NOT_IN_GAME", {}).get(choice)
    except Exception:
        return None


def _not_offered(app_id, keys, offered: Dict[str, list]) -> str:
    """Why a choice is not set: a fixed list the game has (it has no such value, owner 2026-10-23: Far Cry 6
    "DLSS" said "never seen" though the game has only FSR 1.0) or values learned from the game (never seen)."""
    try:
        from app.games.settings_file import adapter_for
        settings = getattr(adapter_for(app_id).module, "SETTINGS", {})
        fixed = [k for k in keys if k in offered and _fixed_list(settings.get(k)) is not None]
    except Exception:
        fixed = []
    if fixed and len(fixed) == len([k for k in keys if k in offered]):
        values = sorted({str(v) for k in fixed for v in offered[k]})
        return "the game has no such value; it offers: " + ", ".join(values)
    return NOT_SEEN


def plan_preset(app_id, preset: GamePreset, offered: Dict[str, list]) -> Tuple[Dict[str, object], Dict[str, str]]:
    """(settings to force {adapter key: value}, choices not applied {choice: reason}) for one game.
    offered: the adapter's choices for the game (settings_file.read_for_dialog)."""
    targets = GAME_TARGETS.get(str(app_id), {})
    requested: Dict[str, object] = {}
    skipped: Dict[str, str] = {}
    for choice, value in preset.chosen().items():
        if choice == "upscaler_mode" and preset.upscaler == "off":
            continue                    # upscaler off: its mode changes nothing
        target = targets.get(choice)
        if target is None or not any(k in offered for k in target.keys):
            skipped[choice] = _not_in_game(app_id, choice) or NO_SETTING
            continue
        found = {}
        for key in target.keys:
            if key not in offered:
                continue
            match = target.match(f"{preset.upscaler} {value}" if target.with_upscaler else value, list(offered[key]))
            if match is None and value == "off" and key in target.when_off:
                continue
            if match is None:
                found = None
                break
            found[key] = match
        if not found:
            skipped[choice] = _not_offered(app_id, target.keys, offered)
            continue
        requested.update(found)
    return requested, skipped


def _fixed_values(app_id) -> Dict[str, list]:
    """{adapter key: its values} for the game's fixed lists ("map" settings): known before its file is read."""
    try:
        from app.games.settings_file import adapter_for
        settings = getattr(adapter_for(app_id).module, "SETTINGS", {})
    except Exception:
        return {}
    return {k: _fixed_list(s) for k, s in settings.items() if _fixed_list(s) is not None}


def _fixed_list(setting) -> Optional[list]:
    """The names of a fixed list: a "map" setting, or a Tomb Raider "named" / "preset" one (kind, names, list)."""
    if getattr(setting, "kind", None) in ("map", "whole"):
        return [shown for shown, _ in setting.values]
    if isinstance(setting, tuple) and len(setting) == 3 and setting[0] in ("named", "preset"):
        return list(setting[2])
    return None


def game_values(app_id, offered: Optional[Dict[str, list]], choice: str, candidates: Sequence[str]
                ) -> Optional[List[str]]:
    """The candidates this game has for a choice (owner, 2026-10-23: only the game's own options are offered, e.g.
    no DLSS for Far Cry 6); None: the game has no such setting in the app. A value missing from the game's fixed
    list is left out; a value the game may have but was never seen, or not read yet, stays (the summary says why)."""
    if choice not in GAME_TARGETS.get(str(app_id), {}):
        return None
    offered = dict(_fixed_values(app_id), **(offered or {}))
    out, written = [], []
    for value in candidates:
        if value == KEEP:
            out.append(value)
            continue
        requested, skipped = plan_preset(app_id, GamePreset(**{choice: value}), offered)
        why = skipped.get(choice)
        if value in GAME_ONLY.get(choice, ()) and why is not None:
            continue
        if why is None and requested in written:        # e.g. Ultra when the game's own Highest is offered
            continue
        if why in (None, NOT_SEEN, NO_SETTING):
            out.append(value)
            written.append(requested)
    return out


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


# ---- what this PC's graphics card can use (owner, 2026-10-20: the owner chooses; the best choice is marked
# "recommended"; what the card cannot run is not offered at all) -----------------------------------------------
MODES = ("raw", "upscaling", "upscaling_fg")
MODE_LABELS = {"raw": "Raw (no upscaling, no frame generation)", "upscaling": "Upscaling, no frame generation",
               "upscaling_fg": "Upscaling + frame generation"}
RECOMMENDED = " (recommended)"


def main_gpu(names: Sequence[str]) -> str:
    """The card games run on: a discrete NVIDIA / AMD / Intel Arc card before a built-in one ("" when unknown)."""
    names = [n for n in names or [] if isinstance(n, str) and n.strip()]
    for test in (lambda n: "nvidia" in n.lower() or "geforce" in n.lower(),
                 lambda n: ("amd" in n.lower() or "radeon" in n.lower()) and not _built_in_amd(n),
                 lambda n: "arc" in n.lower().split() or "intel(r) arc" in n.lower()):
        found = [n for n in names if test(n)]
        if found:
            return found[0]
    return names[0] if names else ""


def _built_in_amd(name: str) -> bool:
    low = name.lower()
    return low.strip() in ("amd radeon(tm) graphics", "amd radeon graphics") or "vega" in low and "rx" not in low


def _vendor(name: str) -> str:
    low = name.lower()
    if "nvidia" in low or "geforce" in low:
        return "nvidia"
    if "amd" in low or "radeon" in low:
        return "amd"
    if "intel" in low or "arc" in low.split():
        return "intel"
    return ""


def _rtx_series(name: str) -> int:
    """GeForce RTX 4070 -> 40, RTX 5090 -> 50, RTX 2060 -> 20; 0: not an RTX card (no DLSS)."""
    import re
    m = re.search(r"rtx\s*(\d{2})\d{2}", name.lower())
    if m:
        return int(m.group(1))
    return 20 if "rtx" in name.lower() else 0      # RTX A4000 / RTX 6000 Ada...: DLSS upscaling at least


def upscalers_for(gpu: str) -> List[str]:
    """The upscalers this card runs: DLSS only on NVIDIA RTX; FSR and XeSS run on every vendor."""
    out = ["dlss"] if _vendor(gpu) == "nvidia" and _rtx_series(gpu) else []
    return out + ["fsr", "xess"]


def recommended_upscaler(gpu: str) -> str:
    return {"nvidia": "dlss" if _rtx_series(gpu) else "fsr", "intel": "xess"}.get(_vendor(gpu), "fsr")


def frame_gens_for(gpu: str, upscaler: str) -> List[str]:
    """Frame generation this card runs with that upscaler: DLSS 2x on RTX 40+, 3x / 4x on RTX 50+; FSR 2x on every
    card; XeSS 2x on Intel Arc only. [] : none."""
    if upscaler == "dlss":
        series = _rtx_series(gpu) if _vendor(gpu) == "nvidia" else 0
        return ["x2", "x3", "x4"] if series >= 50 else ["x2"] if series >= 40 else []
    if upscaler == "fsr":
        return ["x2"]
    if upscaler == "xess":
        return ["x2"] if _vendor(gpu) == "intel" and "arc" in gpu.lower() else []
    return []


def choice_labels(values: Sequence[str], recommended: Optional[str]) -> List[Tuple[str, str]]:
    """[(value, shown text)] with "(recommended)" after the recommended one."""
    return [(v, LABELS.get(v, v) + (RECOMMENDED if v == recommended else "")) for v in values]


def game_preset(mode: str, upscaler: str, frame_gen: str, quality: str, resolution: str, ray_tracing: str,
                upscaler_mode: str = KEEP) -> GamePreset:
    """The page's choices as a preset: raw = upscaler off and no frame generation; upscaling = that upscaler, frame
    generation off; upscaling + frame generation = both."""
    return GamePreset(resolution=resolution, quality=quality, ray_tracing=ray_tracing,
                      upscaler="off" if mode == "raw" else upscaler,
                      upscaler_mode=KEEP if mode == "raw" else upscaler_mode,
                      frame_gen=frame_gen if mode == "upscaling_fg" else "off")


def mode_of(preset: GamePreset) -> str:
    if preset.upscaler in ("off", KEEP):
        return "raw"
    return "upscaling" if preset.frame_gen in ("off", KEEP) else "upscaling_fg"


def page_choices(gpu: str) -> dict:
    """The remote page's game choices for this PC's card (the same rules as the run dialog on the PC):
    [value, text] lists, "(recommended)" after the best ones; frame generation per upscaler."""
    ups = upscalers_for(gpu) if gpu else ["dlss", "fsr", "xess"]
    best = recommended_upscaler(gpu) if gpu else None

    def gens(u):
        return frame_gens_for(gpu, u) if gpu else (["x2", "x3", "x4"] if u == "dlss" else ["x2"])
    modes = [m for m in MODES if m != "upscaling_fg" or any(gens(u) for u in ups)]
    return {"gpu": gpu,
            "modes": [[m, MODE_LABELS[m]] for m in modes] + [[KEEP, "Keep the game's own"]],
            "upscalers": [list(p) for p in choice_labels(ups, best if best in ups else None)],
            "frame_gens": {u: [list(p) for p in choice_labels(gens(u), "x2" if "x2" in gens(u) else None)]
                           for u in ups},
            "qualities": [[q, LABELS[q]] for q in QUALITIES[1:]] + [[KEEP, "Keep the game's own"]],
            "resolutions": [[r, r] for r in RESOLUTIONS[1:]] + [[KEEP, "Keep the game's own"]],
            "ray_tracing": [["off", "Off"], ["on", "On"], [KEEP, "Keep the game's own"]]}


def page_game_lists(app_id, offered: Optional[Dict[str, list]], screens=None) -> dict:
    """One game's own lists for the remote page, as the run dialog shows them (owner, 2026-10-29): [value, text]
    pairs, [] where the game has no such setting; resolutions only the screens show (screens None: unknown)."""
    from app.games.screen_modes import refusal
    keep = [[KEEP, "Keep the game's own"]]

    def pairs(choice, candidates):
        found = game_values(app_id, offered, choice, candidates)
        return [] if found is None else [[v, LABELS.get(v, v)] for v in found] + keep
    res = [r for r in resolutions_offered([offered] if offered else [])[1:] if refusal(r, screens) is None]
    return {"qualities": pairs("quality", QUALITIES[1:]), "ray_tracing": pairs("ray_tracing", RAY_TRACING[1:]),
            "upscaler_modes": pairs("upscaler_mode", UPSCALER_MODES[1:]), "resolutions": pairs("resolution", res),
            # plain values the card's lists are cut to; None: the game has no such setting
            "upscalers": game_values(app_id, offered, "upscaler", UPSCALERS[1:]),
            "frame_gens": game_values(app_id, offered, "frame_gen", FRAME_GEN[1:])}


def from_page(data) -> Optional[GamePreset]:
    """A preset sent by the remote page ({mode, upscaler, frame_gen, quality, resolution, ray_tracing}), checked
    like a saved one; None when it is not a preset at all."""
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    if mode in MODES:
        upscaler = data.get("upscaler") if data.get("upscaler") in UPSCALERS[2:] else "off"
        frame_gen = data.get("frame_gen") if data.get("frame_gen") in FRAME_GEN[2:] else "off"
        if mode != "raw" and upscaler == "off":
            return None
        data = dict(data, upscaler="off" if mode == "raw" else upscaler,
                    upscaler_mode=KEEP if mode == "raw" else data.get("upscaler_mode", KEEP),
                    frame_gen=frame_gen if mode == "upscaling_fg" else "off")
    else:
        data = dict(data, upscaler=KEEP, frame_gen=KEEP, upscaler_mode=KEEP)
    return GamePreset.from_dict(data)

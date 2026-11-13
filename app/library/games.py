"""Game results in the library (owner, 2026-11-12, Decisions.md).

A group is the game + its build + resolution + preset level: results in one group line up and compare. The
upscaler (with its mode), frame generation and ray tracing (on/off only) are kept on each result and shown next to
it, with filters, so a result with and without them can be compared. Each result keeps the game's own preset name
and a common level: the game's top preset is "Max", the ones below it "Max-1", "Max-2"... Only the games below,
measured over their built-in benchmark, are taken.
"""

import re
from typing import Mapping, Optional

# the game's presets, lowest first (its top one is "Max"); has RT, has frame generation, has an upscaler, has an upscaler
GAMES = {
    "borderlands 3": (("Very Low", "Low", "Medium", "High", "Ultra", "Badass"), False, False, False),
    "cyberpunk 2077": (("Low", "Medium", "High", "Ultra"), True, True, True),   # Ultra without RT is its top (owner)
    "far cry 6": (("Low", "Medium", "High", "Ultra"), True, False, True),
    "red dead redemption 2": (("Low", "Medium", "High", "Ultra"), False, False, True),
    "shadow of the tomb raider": (("Lowest", "Low", "Medium", "High", "Highest"), True, False, True),
}
# the first line measured after SOTTR's span and the trust fixes (owner: only the last full test goes in)
FROM = "2026-11-12T16:54:00"
NOT_READ = ("not in the game's file", "the game has none", "not read")
OFF = ("off", "false", "0", "none", "no", "disabled")


ALIASES = {"bl3": "borderlands 3", "fc6": "far cry 6", "rdr2": "red dead redemption 2", "sottr": "shadow of the tomb raider"}


def _game(name: str) -> Optional[str]:
    """By its name or its stored id (cyberpunk2077_builtin_benchmark, frames_bl3)."""
    flat = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return next((g for g in GAMES if re.sub(r"[^a-z0-9]", "", g) in flat), None) or         next((g for a, g in ALIASES.items() if re.search(r"(^|_)" + a + r"($|_)", (name or "").lower())), None)


def _parts(note: str) -> dict:
    out = {}
    for part in str(note or "").split("; "):
        key, sep, value = part.partition(": ")
        if sep and key in ("Settings used", "Forced"):
            out[key] = value
    return out


def _value(settings: str, *labels: str) -> Optional[str]:
    for label in labels:
        m = re.search(r"(?:^|, )" + re.escape(label) + r": ([^,]+)", settings)
        if m and m.group(1).strip().lower() not in NOT_READ:
            return m.group(1).strip()
    return None


def _forced(forced: str, *keys: str) -> Optional[str]:
    for key in keys:
        m = re.search(r"(?:^|, )" + re.escape(key) + r" ([^,]+)", forced)
        if m:
            return m.group(1).strip()
    return None


def _off(value: Optional[str]) -> Optional[bool]:
    return None if value is None else value.strip().lower() in OFF


def game_settings(name: str, note: Optional[str], whole: bool) -> Optional[dict]:
    """The group and options of a game result from its run note, or None when the library does not take it."""
    game = _game(name)
    if game is None or not whole:
        return None
    presets, has_rt, has_fg, has_up = GAMES[game]
    p = _parts(note)
    used, forced = p.get("Settings used", ""), p.get("Forced", "")
    res = _value(used, "Resolution", "Display Resolution (the game's report)") or (
        re.search(r"Screen mode during the benchmark: (\d+x\d+)", used) or [None, None])[1]
    res = res.replace(" ", "") if res else None
    preset = _forced(forced, "preset") or _value(used, "Quality preset", "Preset", "Graphics Quality (the game's report)")
    hit = next((x for x in presets if preset and x.lower() == preset.lower()), None)
    if res is None or hit is None:
        return None                                 # no group to put it in
    place = len(presets) - 1 - presets.index(hit)
    upscaler = (_forced(forced, "upscaler", "dlss") or _value(used, "Upscaler", "DLSS")) if has_up else "Off"
    mode = _value(used, "Upscaler mode", "DLSS mode")
    if upscaler and not _off(upscaler) and mode and mode.lower() not in upscaler.lower():
        upscaler = f"{upscaler} {mode}"
    fg = (_forced(forced, "frame_gen") or _value(used, "Frame generation")) if has_fg else "Off"
    rt = [_off(v) for v in (_forced(forced, "ray_tracing"), _forced(forced, "dxr_reflections"),
                            _forced(forced, "dxr_shadows"), _value(used, "Ray tracing"),
                            _value(used, "Ray traced reflections"), _value(used, "Ray traced shadows"))
          if v is not None] if has_rt else [True]
    return {"game": game, "resolution": res, "preset": hit, "level": "Max" if place == 0 else f"Max-{place}",
            "build": _value(used, "Game version"),
            "upscaler": "Off" if _off(upscaler) else upscaler,
            "frame_gen": "Off" if _off(fg) else fg,
            "ray_tracing": None if not rt else "Off" if all(rt) else "On"}


def of(pkg: Mapping) -> Optional[dict]:
    return (pkg.get("test", {}).get("settings") or {}).get("game")


def paired(pkg: Mapping) -> Optional[str]:
    """The other part a game result ran with: the CPU on a graphics card's page, the card on a CPU's page."""
    a = pkg.get("attribution") or {}
    if a.get("paired"):
        return a["paired"]
    return next((x.get("part_name") for x in a.get("linked") or [] if x.get("part") == "cpu"), None)


def options_text(pkg: Mapping) -> str:
    """'RT off, upscaler Off, frame gen Off, with <the other part>' next to a game result; '' for any other."""
    g = of(pkg)
    if not g:
        return ""
    show = lambda v: v or "not recorded"
    other = paired(pkg)
    return (f"RT {show(g.get('ray_tracing')).lower()}, upscaler {show(g.get('upscaler'))}, "
            f"frame gen {show(g.get('frame_gen'))}" + (f", with {other}" if other else ""))


def with_cpu(results):
    """The results plus each game result again under its CPU (owner, 2026-11-13: a game compares processors as
    well as graphics cards). The copy keeps the package id; only the library screen uses it."""
    out = list(results)
    for p in results:
        a = p.get("attribution") or {}
        cpu = next((x for x in a.get("linked") or [] if x.get("part") == "cpu"), None)
        if of(p) and cpu and a.get("part") == "gpu":
            out.append(dict(p, attribution=dict(a, part="cpu", part_key=cpu["part_key"],
                                                 part_name=cpu["part_name"], variant=None,
                                                 context=dict(cpu.get("context") or {}), linked=[],
                                                 paired=a.get("variant") or a.get("part_name"))))
    return out


def group_name(name: str, g: Mapping) -> str:
    return f"{name} - {g['resolution']}, {g['level']} ({g['preset']})" + (f", build {g['build']}" if g.get("build") else "")


def upscaled(pkg: Mapping) -> Optional[bool]:
    g = of(pkg)
    if not g or g.get("upscaler") is None:
        return None
    return g["upscaler"] != "Off" or (g.get("frame_gen") or "Off") != "Off"


def passes(pkg: Mapping, rt: str = "", upscaling: str = "") -> bool:
    """The library's game filters: rt / upscaling '' (any), 'on' or 'off'. Other results always pass."""
    g = of(pkg)
    if not g:
        return True
    if rt and (g.get("ray_tracing") or "").lower() != rt:
        return False
    if upscaling and upscaled(pkg) is not (upscaling == "on"):
        return False
    return True

"""Shared rules for adapters whose settings are named text values (Red Dead Redemption 2, The Witcher 3).

A game module gives SETTINGS {key: Setting} and two accessors over its parsed data:
get(data, ref) -> text or None, and put(data, ref, text) -> new data (the input is not changed).
Kinds: "map" offers every value in Setting.values ((shown value, file text), ...; with several refs the file text
is a tuple, one per ref, all written together); "learned" is free
text, so only values this game has written before (learned by app/games/settings_file.py) are offered
or written; "resolution" is two refs (width, height) shown as "WIDTHxHEIGHT" and learned the same way.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

NOT_SEEN = "not seen in this game yet; choose it once in the game's menu so the app can learn it"


@dataclass(frozen=True)
class Setting:
    kind: str
    refs: Tuple[str, ...]
    values: Tuple[Tuple[object, str], ...] = ()


@dataclass(frozen=True)
class Change:
    key: str
    old: object
    new: object
    index: Optional[int] = None


def read_settings(spec: Dict[str, Setting], get: Callable, data) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for key, s in spec.items():
        texts = [get(data, r) for r in s.refs]
        if None in texts:
            continue
        if s.kind == "map":
            now = texts[0] if len(s.refs) == 1 else tuple(texts)
            named = [shown for shown, text in s.values if text == now]
            if named:
                out[key] = named[0]
            elif len(s.refs) > 1:            # a mix with no name yet: shown as it is (a long list: Custom), and can be changed
                out[key] = "/".join(texts) if len(s.refs) <= 3 else "Custom"
        elif s.kind == "learned":
            out[key] = texts[0]
        elif s.kind == "resolution" and all(t.isdigit() for t in texts):
            out[key] = "x".join(texts)
    return out


def observed_indices(spec, get, data) -> Dict[str, Dict[str, int]]:
    now = read_settings(spec, get, data)
    return {k: {now[k]: 0} for k, s in spec.items() if s.kind != "map" and k in now}


def merge_indices(known, seen) -> Dict[str, Dict[str, int]]:
    known = known if isinstance(known, dict) else {}
    seen = seen if isinstance(seen, dict) else {}
    return {k: {**(known.get(k) if isinstance(known.get(k), dict) else {}),
                **(seen.get(k) if isinstance(seen.get(k), dict) else {})}
            for k in list(known) + [k for k in seen if k not in known]}


def _seen(known, key) -> List[str]:
    inner = known.get(key) if isinstance(known, dict) else None
    return [t for t in inner if isinstance(t, str)] if isinstance(inner, dict) else []


def choices(spec, get, data, known) -> Dict[str, list]:
    now = read_settings(spec, get, data)
    return {k: [shown for shown, _ in s.values] if s.kind == "map" else sorted({now[k]} | set(_seen(known, k)))
            for k, s in spec.items() if k in now}


def plan(spec, get, data, desired, known) -> Tuple[List[Change], Dict[str, str]]:
    now = read_settings(spec, get, data)
    changes: List[Change] = []
    refused: Dict[str, str] = {}
    for key, want in (desired or {}).items():
        s = spec.get(key)
        if s is None:
            refused[key] = "unknown setting"
        elif key not in now:
            refused[key] = "not in the settings file"
        elif now[key] == want and type(now[key]) is type(want):
            continue
        elif s.kind == "map" and not any(want == v and type(want) is type(v) for v, _ in s.values):
            refused[key] = "not one of: " + ", ".join(str(v) for v, _ in s.values)
        elif s.kind != "map" and not isinstance(want, str):
            refused[key] = "expects text"
        elif s.kind != "map" and want not in _seen(known, key):
            refused[key] = NOT_SEEN
        else:
            changes.append(Change(key, now[key], want))
    return changes, refused


def apply(spec, put: Callable, data, changes: List[Change]):
    for c in changes:
        s = spec[c.key]
        if s.kind == "map":
            text = next(t for v, t in s.values if v == c.new and type(v) is type(c.new))
            texts = list(text) if isinstance(text, tuple) else [text]
        elif s.kind == "resolution":
            texts = c.new.split("x")
        else:
            texts = [c.new]
        for ref, text in zip(s.refs, texts):
            data = put(data, ref, text)
    return data


def verify(spec, get, data, desired) -> Dict[str, Tuple[object, object]]:
    now = read_settings(spec, get, data)
    return {k: (v, now.get(k)) for k, v in (desired or {}).items()
            if now.get(k) != v or type(now.get(k)) is not type(v)}

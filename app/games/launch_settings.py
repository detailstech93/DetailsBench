"""Shared rules for games whose settings are forced with launch arguments (Counter-Strike 2, DOOM).

Nothing is written before the launch: the chosen values become steam://run arguments for that launch
only. The game's own settings files are read (current values in the window, values learned so far) and
read again after the run, when the game may have saved what it used; a value the files do not hold is
recorded as not checked, never as matching. data is a flat {name: text} made by the game module's parse.

Option kinds: "map" offers every value in Option.values ((shown value, file text), ...); "learned" offers
only values this game has written before; "resolution" ("WIDTHxHEIGHT" from two names) offers the values
in Option.values plus those seen. Option.args turns the file text (map) or the value into arguments.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from app.games.text_settings import NOT_SEEN, Change, merge_indices  # noqa: F401  (re-exported)


@dataclass(frozen=True)
class Option:
    kind: str
    refs: Tuple[str, ...]
    values: Tuple[Tuple[object, str], ...]
    args: Callable[[str], List[str]]


def read_settings(spec: Dict[str, Option], data) -> Dict[str, object]:
    data = data if isinstance(data, dict) else {}
    out: Dict[str, object] = {}
    for key, o in spec.items():
        texts = [data.get(r) for r in o.refs]
        if None in texts:
            continue
        if o.kind == "map":
            out.update({key: shown for shown, text in o.values if text == texts[0]})
        elif o.kind == "learned":
            out[key] = texts[0]
        elif all(t.isdigit() for t in texts):
            out[key] = "x".join(texts)
    return out


def observed_indices(spec, data) -> Dict[str, Dict[str, int]]:
    now = read_settings(spec, data)
    return {k: {now[k]: 0} for k, o in spec.items() if o.kind != "map" and k in now}


def _seen(known, key) -> List[str]:
    inner = known.get(key) if isinstance(known, dict) else None
    return [t for t in inner if isinstance(t, str)] if isinstance(inner, dict) else []


def _allowed(o: Option, key, known) -> list:
    if o.kind == "map":
        return [shown for shown, _ in o.values]
    return sorted(set(_seen(known, key)) | ({v for v, _ in o.values} if o.kind == "resolution" else set()))


def choices(spec, data, known) -> Dict[str, list]:
    """Every option is offered, also when the files do not hold it yet (the argument still applies)."""
    now = read_settings(spec, data)
    out = {}
    for key, o in spec.items():
        values = _allowed(o, key, known)
        if key in now and now[key] not in values:
            values = sorted(values + [now[key]]) if o.kind != "map" else values
        if values:
            out[key] = values
    return out


def plan(spec, data, desired, known) -> Tuple[List[Change], Dict[str, str]]:
    """changes: every accepted value (it is passed on the command line even when the file already has it)."""
    now = read_settings(spec, data)
    changes: List[Change] = []
    refused: Dict[str, str] = {}
    for key, want in (desired or {}).items():
        o = spec.get(key)
        if o is None:
            refused[key] = "unknown setting"
        elif not any(want == v and type(want) is type(v) for v in _allowed(o, key, known)):
            refused[key] = NOT_SEEN if o.kind == "learned" else "not one of: " + ", ".join(
                str(v) for v in _allowed(o, key, known))
        else:
            changes.append(Change(key, now.get(key), want))
    return changes, refused


def launch_args(spec, values: Dict[str, object]) -> List[str]:
    out: List[str] = []
    for key, o in spec.items():
        if key not in (values or {}):
            continue
        want = values[key]
        text = next((t for v, t in o.values if v == want and type(v) is type(want)), None) \
            if o.kind == "map" else want
        if text is not None:
            out += o.args(text)
    return out


def verify(spec, data, desired) -> Dict[str, Tuple[object, object]]:
    now = read_settings(spec, data)
    return {k: (v, now.get(k)) for k, v in (desired or {}).items()
            if now.get(k) != v or type(now.get(k)) is not type(v)}

"""Keys the app presses inside a game after launching it, to get from its menus into the scene it measures
(a gameplay capture or a built-in benchmark without a launch argument).

    GAME_START_KEYS = {app_id: {"keys": [[seconds after the launch, "chord"], ...],
                                "verified": bool, "note": "what the keys do"}}

Chords use app/simulation/desktop.py names ("enter", "esc", "down", "alt+t"). A key is pressed only when the
foreground window belongs to a process the launch started inside the game's folder; otherwise it is skipped
and the run says so (keys never go to another program). Menus change between game versions, so an entry
stays "verified": False until it was watched on a real run with the owner; the times include loading, so
they are generous. Nothing is listed yet: each game's path is found together with the owner, screenshot by
screenshot, before it is written here.
"""

from typing import Dict, List, Tuple

GAME_START_KEYS: Dict[int, dict] = {}


def start_keys_for(app_id) -> List[Tuple[float, str]]:
    try:
        entry = GAME_START_KEYS.get(int(app_id))
    except (TypeError, ValueError):
        return []
    if not entry:
        return []
    return [(float(at), str(chord)) for at, chord in entry.get("keys", [])]

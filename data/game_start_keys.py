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

from typing import Dict, List, Optional, Tuple

GAME_START_KEYS: Dict[int, dict] = {}

# Checked menu steps to a game's built-in benchmark (app/frames/menu_steps.py): every key waits for a word read
# on the screen first. Paths come from LTT Labs' MarkBench harnesses (github.com/LTTLabsOSS/markbench-tests,
# read 2026-10-18; facts only, no code taken) and stay "verified": False until watched on a real run here.
GAME_MENU_STEPS: Dict[int, dict] = {
    1174180: {      # Red Dead Redemption 2: main menu -> Settings (Z) -> Graphics -> hold X "Run Benchmark Test"
        "steps": [
            {"see": "settings", "within": 300, "dismiss": {"strange": "enter"}, "press": "z", "wait": 3},
            {"see": "graphics", "within": 30, "press": "up, up, left, left, down, enter", "wait": 3},
            {"see": "benchmark", "within": 30, "hold": "x", "seconds": 1.5, "wait": 1},
            {"press": "enter"},
            # "Stop Benchmarking" in the corner: it runs. Loading took over 3 min on the first try here
            {"see": "stop", "within": 300, "mark": True},
        ],
        "verified": False,
        "note": "Settings, Graphics, hold X on Run Benchmark Test, confirm; measuring starts when "
                "'Stop Benchmarking' shows. The whole benchmark takes about 4.5 min.",
    },
}


def menu_steps_for(app_id) -> Optional[list]:
    try:
        entry = GAME_MENU_STEPS.get(int(app_id))
    except (TypeError, ValueError):
        return None
    return [dict(s) for s in entry["steps"]] if entry else None


def start_keys_for(app_id) -> List[Tuple[float, str]]:
    try:
        entry = GAME_START_KEYS.get(int(app_id))
    except (TypeError, ValueError):
        return []
    if not entry:
        return []
    return [(float(at), str(chord)) for at, chord in entry.get("keys", [])]

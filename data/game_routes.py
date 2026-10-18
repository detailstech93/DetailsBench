"""Routes played inside games without a built-in benchmark during a gameplay capture (app/frames/route.py):
the same keys held and mouse moves in every pass, so the measured windows can be compared like a benchmark.

    GAME_ROUTES = {app_id: {"loop_from": s, "loop_seconds": s, "loop": [steps], "verified": bool,
                            "start": "what the owner does before loop_from", "note": "what the route does"}}

A route never goes online: only single-player games, and never games with anti-cheat. The owner loads the save
(or start keys from data/game_start_keys.py do) before "loop_from"; the measurement starts no earlier than that
(the settle time is raised to it). The mouse counts that turn the camera half round depend on the game's
sensitivity setting, so every route stays "verified": False until it was watched on a real run with the owner
and its counts adjusted. Each pass walks forward, turns round, walks back and turns again, so the character
stays on one stretch of ground.
"""

from typing import Dict, Optional

GAME_ROUTES: Dict[int, dict] = {
    292030: {   # The Witcher 3: Wild Hunt
        "loop_from": 60, "loop_seconds": 20,
        "loop": [[0, "hold", "w", 8], [8.5, "mouse", 1800, 0, 1],
                 [10, "hold", "w", 8], [18.5, "mouse", -1800, 0, 1]],
        "verified": False,
        "start": "load a save standing in a town or on a road (Novigrad, White Orchard), not in combat",
        "note": "draft: jog 8 s, turn round, jog back; the turn amount is a guess (to be checked together)",
    },
    379720: {   # DOOM (2016)
        "loop_from": 60, "loop_seconds": 16,
        "loop": [[0, "hold", "w", 6], [6.5, "mouse", 1800, 0, 1],
                 [8, "hold", "w", 6], [14.5, "mouse", -1800, 0, 1]],
        "verified": False,
        "start": "load a checkpoint in an area already cleared of demons",
        "note": "draft: run 6 s, turn round, run back; the turn amount is a guess (to be checked together)",
    },
}


def route_for(app_id) -> Optional[dict]:
    try:
        return GAME_ROUTES.get(int(app_id))
    except (TypeError, ValueError):
        return None

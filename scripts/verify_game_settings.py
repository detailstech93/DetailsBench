"""Read-only check of the game settings adapters on this computer (nothing is written).

Prints, for every game with an adapter whose settings file exists: the settings as read, the values the
Game settings... window would offer, and the plan for keeping the current values (must be empty).
Run: .venv/Scripts/python.exe scripts/verify_game_settings.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.games.settings_file import ADAPTERS, setting_choices  # noqa: E402


def main() -> int:
    found = 0
    for app_id, adapter in ADAPTERS.items():
        path = adapter.settings_path()
        print(f"Steam {app_id}: {path}")
        if path is None or not path.is_file():
            print("  no settings file (start the game once)")
            continue
        found += 1
        data = adapter.module.parse(path.read_bytes().decode("utf-8"))
        mod = adapter.module
        current = mod.read_settings(data)
        print("  read:", json.dumps(current))
        print("  missing:", [k for k in mod.SETTINGS if k not in current])
        known = mod.observed_indices(data)
        print("  choices:", json.dumps(setting_choices(adapter, data, known)))
        changes, refused = mod.plan(data, current, known)
        print("  keep-current plan:", changes, refused)
        print("  verify current:", mod.verify(data, current))
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""
Real-machine Steam check (no mocks). Run manually, paste the output.

Prints the Steam client, every reachable library (and unreachable ones as
notes), and each installed app with appid, kind, installed state, runtime and
built-in benchmark status. Read-only: nothing is launched or changed, and no
account data (loginusers.vdf, LastOwner) is read or printed.

Does NOT run as part of pytest (depends on the host machine).

Usage:
    python scripts/verify_steam.py
Exit code: 0 if no discovery step failed, 1 otherwise.
"""

import os
import sys
import time

# Ensure the project root is on sys.path so app/ and data/ are importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.discovery.runner import discover_installed


def main() -> int:
    start = time.time()
    result = discover_installed()
    print(f"Discovery finished in {time.time() - start:.2f} seconds.\n")

    client = result.stores.get("steam") or {}
    print(f"Steam client: {client.get('status')}  path={client.get('path')}  <- {client.get('source')}")
    for lib in client.get("libraries") or []:
        print(f"  library: {lib}")
    for note in client.get("notes") or []:
        print(f"  note: {note}")

    games = sorted((g for g in result.games if g.get("store") == "steam"), key=lambda g: g["name"].lower())
    print(f"\nInstalled Steam apps ({len(games)}):")
    for g in games:
        print(f"  {g['app_id']:>8}  {g.get('kind', 'game'):<4}  installed={g.get('installed')!s:<5}  "
              f"runtime={g.get('runtime'):<11}  benchmark={g.get('builtin_benchmark') or '-':<10}  {g['name']}")

    steps = {k: v for k, v in result.step_seconds.items() if k.startswith("steam")}
    print("\nSteam step timings (seconds):")
    for step, seconds in steps.items():
        print(f"  {step:<18} {seconds}")
    print("\nDiagnostics:")
    for line in result.diagnostics or ["(none)"]:
        print(f"  {line}")
    return 1 if result.diagnostics else 0


if __name__ == "__main__":
    sys.exit(main())

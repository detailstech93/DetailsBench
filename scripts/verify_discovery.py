#!/usr/bin/env python
"""
Real-machine installed-software / game discovery check (no mocks). Run manually, paste the output.

Prints elapsed time, every MATCHED tool and game (unmatched software is never
kept, so it cannot be printed), store/client status, step timings and
diagnostics. Nothing is launched or changed.

Does NOT run as part of pytest (depends on the host machine).

Usage:
    python scripts/verify_discovery.py
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

    print(f"Matched software ({len(result.software)}):")
    for s in result.software:
        print(f"  [{s['kind']:<9}] {s['name']:<40} version={s['version']}  path={s['path']}  <- {s['source']}")

    print(f"\nGames ({len(result.games)}):")
    for g in result.games:
        print(f"  [{g['store']}] {g['name']}  id={g['app_id']}  path={g['install_path']}  version={g['version']}")
    if not result.games:
        print("  (none)")

    print("\nStores / clients:")
    for name, info in sorted(result.stores.items()):
        print(f"  {name:<6} {info['status']:<13} path={info['path']}  <- {info['source']}")

    print("\nStep timings (seconds):")
    for step, seconds in result.step_seconds.items():
        print(f"  {step:<20} {seconds}")

    print("\nDiagnostics:")
    for line in result.diagnostics or ["(none)"]:
        print(f"  {line}")
    return 1 if result.diagnostics else 0


if __name__ == "__main__":
    sys.exit(main())

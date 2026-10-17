#!/usr/bin/env python
"""
Real-machine Environment Profile check (no mocks). Run manually, paste the output.

Prints elapsed time, every profile field with value, status and source, the
per-step timings and diagnostics, and the compatibility result for each
benchmark definition. Nothing is changed on the system.

Does NOT run as part of pytest (depends on the host machine).

Usage:
    python scripts/verify_environment.py
Exit code: 0 if the profile was built and no step failed, 1 otherwise.
"""

import os
import sys
import time

# Ensure the project root is on sys.path so app/ and data/ are importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.environment.adapters import select_adapter
from app.environment.compatibility import check_compatibility
from data.benchmark_definitions import BENCHMARK_DEFINITIONS


def main() -> int:
    adapter = select_adapter()
    print(f"Adapter: {type(adapter).__name__}")
    start = time.time()
    profile = adapter.build_profile()
    print(f"Profile built in {time.time() - start:.2f} seconds.\n")

    print(f"{'Field':<20} {'Status':<12} Value  <- source")
    print("-" * 100)
    for name in profile.field_names():
        f = getattr(profile, name)
        print(f"{name:<20} {f.status:<12} {f.value!r}  <- {f.source}")

    print("\nStep timings (seconds):")
    for step, seconds in profile.step_seconds.items():
        print(f"  {step:<14} {seconds}")

    print("\nDiagnostics:")
    for line in profile.diagnostics or ["(none)"]:
        print(f"  {line}")

    print("\nCompatibility per benchmark definition:")
    for definition in BENCHMARK_DEFINITIONS:
        result = check_compatibility(definition, profile)
        note = "" if result.comparable_to_native else "  [not comparable to native]"
        print(f"  {definition.id:<12} {result.status:<13} {'; '.join(result.reasons) or '-'}{note}")

    return 1 if profile.diagnostics else 0


if __name__ == "__main__":
    sys.exit(main())

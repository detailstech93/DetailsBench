#!/usr/bin/env python
"""
Real-machine Geekbench major-version check (no mocks). Run manually, paste the output.

Prints every Geekbench entry the scanner finds and checks that each installed
major version is a separate entry (geekbench_<major>) whose name matches its
file version, with distinct paths, pointing only at the main GUI executable.
It does not assert which majors are installed.

Does NOT run as part of pytest (depends on the host machine).

Usage:
    python scripts/verify_geekbench_versions.py
Exit code: 0 if all checks hold, 1 otherwise.
"""

import os
import re
import sys

# Ensure the project root is on sys.path so app/ and data/ are importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.benchmark_manager import BenchmarkManager, BenchmarkStatus

ENTRY_NAME = re.compile(r"^geekbench_(\d+)$")
GEEKBENCH_MAIN_EXE = re.compile(r"^geekbench\s+\d+\.exe$", re.IGNORECASE)


def main() -> int:
    entries = BenchmarkManager()._detect_geekbench()
    print(f"Geekbench entries found: {len(entries)}")
    for gb in entries:
        print(f"  name={gb.name} status={gb.status.value} version={gb.version} "
              f"arch={gb.architecture} path={gb.executable_path}")

    installed = [gb for gb in entries if gb.status != BenchmarkStatus.NOT_INSTALLED]
    if not installed:
        print("\nNo Geekbench installation detected on this machine; nothing to compare.")
        return 0

    problems = []
    for gb in installed:
        m = ENTRY_NAME.match(gb.name)
        if not m:
            problems.append(f"{gb.name}: installed entry is not named geekbench_<major>")
            continue
        if gb.version and gb.version.split(".")[0] != m.group(1):
            problems.append(f"{gb.name}: version {gb.version} does not match major {m.group(1)}")
        if gb.executable_path and not GEEKBENCH_MAIN_EXE.match(os.path.basename(gb.executable_path)):
            problems.append(f"{gb.name}: points at non-main executable {gb.executable_path}")

    names = [gb.name for gb in installed]
    paths = [gb.executable_path for gb in installed if gb.executable_path]
    if len(names) != len(set(names)):
        problems.append(f"duplicate entry names: {names}")
    if len(paths) != len(set(paths)):
        problems.append(f"two entries share one executable: {paths}")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nEach installed Geekbench major is a separate, consistent entry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

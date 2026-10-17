#!/usr/bin/env python
"""
Real-machine discovery check (no mocks). Run manually, paste the output.

Prints the system summary and every scan() entry, then checks invariants that
must hold on ANY machine (all six suites listed, normalized architecture, web
entries have no executable, reported paths exist, Geekbench entries never point
at helper binaries). It does not assert which software is installed: compare
the printed table with what you know is on the machine.

Does NOT run as part of pytest (depends on the host machine).

Usage:
    python scripts/verify_real_scan.py
Exit code: 0 if all invariants hold, 1 otherwise.
"""

import os
import re
import sys
import time

# Ensure the project root is on sys.path so app/ and data/ are importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.benchmark_manager import BenchmarkManager, BenchmarkStatus
from app.system_info import get_system_summary
from data.benchmark_definitions import BENCHMARK_DEFINITIONS

ALLOWED_ARCH = {"x64", "x86", "arm64", "unknown", None}
GEEKBENCH_MAIN_EXE = re.compile(r"^geekbench\s+\d+\.exe$", re.IGNORECASE)


def check_invariants(benchmarks) -> list:
    """Return a list of human-readable invariant violations (empty = OK)."""
    problems = []
    names = [bm.name for bm in benchmarks]
    if len(names) != len(set(names)):
        problems.append(f"duplicate entry names: {names}")

    for definition in BENCHMARK_DEFINITIONS:
        matching = [n for n in names
                    if n == definition.id or n.startswith(definition.id + "_")]
        if not matching:
            problems.append(f"{definition.id}: no entry in scan results")

    categories = {d.id: d.category for d in BENCHMARK_DEFINITIONS}
    for bm in benchmarks:
        if not isinstance(bm.status, BenchmarkStatus):
            problems.append(f"{bm.name}: status is not a BenchmarkStatus ({bm.status!r})")
        if bm.architecture not in ALLOWED_ARCH:
            problems.append(f"{bm.name}: architecture {bm.architecture!r} not normalized")
        base_id = bm.name.split("_")[0]
        if categories.get(base_id) == "web":
            if bm.executable_path is not None:
                problems.append(f"{bm.name}: web benchmark has executable_path")
            if bm.file_exists is not None:
                problems.append(f"{bm.name}: web benchmark file_exists must be None")
        if bm.executable_path and not os.path.isfile(bm.executable_path):
            problems.append(f"{bm.name}: executable_path does not exist: {bm.executable_path}")
        if base_id == "geekbench" and bm.executable_path:
            exe = os.path.basename(bm.executable_path)
            if not GEEKBENCH_MAIN_EXE.match(exe):
                problems.append(f"{bm.name}: points at non-main executable {exe}")
    return problems


def main() -> int:
    print("=== System summary ===")
    for key, value in get_system_summary().items():
        print(f"  {key}: {value}")

    print("\n=== Running REAL BenchmarkManager.scan() (no mocks) ===")
    start_time = time.time()
    benchmarks = BenchmarkManager().scan()
    elapsed = time.time() - start_time
    print(f"Scan completed in {elapsed:.2f} seconds; {len(benchmarks)} entries.\n")

    print("-" * 100)
    print(f"{'Benchmark':<15} | {'Status':<18} | {'Version':<14} | {'Arch':<7} | {'Detection Method':<25}")
    print("-" * 100)
    for bm in benchmarks:
        status_str = "assisted/manual" if bm.status == BenchmarkStatus.READY_MANUAL else bm.status.value
        print(f"{bm.name:<15} | {status_str:<18} | {bm.version or 'None':<14} | "
              f"{bm.architecture or 'None':<7} | {bm.detection_method or 'not_found':<25}")
        print(f"   Display Name: {bm.display_name}")
        print(f"   Path:         {bm.executable_path or 'no exe path'}")
        print(f"   Reason:       {bm.reason}")
        if bm.scan_diagnostics:
            print(f"   Diagnostics:  {bm.scan_diagnostics}")
        print("-" * 100)

    problems = check_invariants(benchmarks)
    if problems:
        print("\nINVARIANT VIOLATIONS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll invariants hold. Installed/not-installed status is NOT checked: "
          "compare the table above with this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

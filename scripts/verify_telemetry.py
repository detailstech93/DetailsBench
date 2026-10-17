#!/usr/bin/env python
"""
Real-machine check of telemetry (Phase 7), with no mocks:
  1. each default source: available or the reason it is not;
  2. the raw nvidia-smi query output (when nvidia-smi exists);
  3. sample all sources for 5 s (1 per second) and print every sample and the avg/max summary.
Nothing is stored and nothing is installed or started.

Usage:
    python scripts/verify_telemetry.py
"""

import os
import subprocess
import sys
import time

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.telemetry.nvidia_smi import query_args
from app.telemetry.sampler import TelemetrySampler, summarize
from app.telemetry.sources import NvidiaSmiSource, default_sources


def main() -> int:
    print("== 1. Sources ==")
    sources = default_sources()
    for src in sources:
        ok, reason = src.available()
        print(f"  {src.name}: {'available' if ok else 'NOT available'} - {reason}")

    print("\n== 2. Raw nvidia-smi output ==")
    exe = NvidiaSmiSource().executable
    if exe:
        res = subprocess.run([exe] + query_args(), capture_output=True, text=True, timeout=10)
        print(f"  command: {subprocess.list2cmdline([exe] + query_args())}")
        print(f"  exit={res.returncode} stdout={res.stdout!r} stderr={res.stderr!r}")
    else:
        print("  nvidia-smi not found")

    print("\n== 3. Sampling 5 s ==")
    sampler = TelemetrySampler(sources, interval_seconds=1.0).start()
    time.sleep(5.2)
    result = sampler.stop()
    for t, reading in result.samples:
        print(f"  t={t:5.2f}s " + ", ".join(f"{k}={v:g} {u}" for k, (v, u) in sorted(reading.items())))
    print(f"  unavailable: {result.unavailable or 'none'}")
    print(f"  read errors: {result.errors or 'none'} {result.last_error or ''}")
    for name, (value, unit) in sorted(summarize(result).items()):
        print(f"  {name} = {value:.2f} {unit}")
    ok = len(result.samples) >= 4 and not result.errors
    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

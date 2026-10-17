#!/usr/bin/env python
"""Test real benchmark discovery on this machine (no mocks)."""

import time
import sys
import os

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(__file__))

from app.benchmark_manager import BenchmarkManager, normalize_version, BenchmarkStatus


def test_normalize_version_unit():
    """Unit test for version normalization helper with exact ground truth strings."""
    print("=== Testing Version Normalization Helper ===")
    test_cases = [
        ("7,1,0,0", "7.1.0.0"),
        ("6,7,1,0", "6.7.1.0"),
        ("6.7.1.0", "6.7.1.0"),
        ("2026.1.3.0", "2026.1.3.0"),
        ("5, 2, 1", "5.2.1"),
        ("4.3, 2.1", "4.3.2.1"),
        ("4.3.2.1.0", "4.3.2.1.0"),
        ("", ""),
        (None, None),
        ("Unknown", "Unknown"),
    ]
    for inp, expected in test_cases:
        res = normalize_version(inp)
        print(f"normalize_version({repr(inp)}) -> {repr(res)}")
        assert res == expected, f"Failed: expected {repr(expected)}, got {repr(res)}"
    print("All version normalization tests passed successfully!\n")


def test_real_scan():
    """Run real scan() with no mocks and print discovery results."""
    print("=== Running REAL BenchmarkManager.scan() (No Mocks) ===")
    start_time = time.time()
    
    manager = BenchmarkManager()
    benchmarks = manager.scan()
    
    elapsed = time.time() - start_time
    print(f"\nReal scan completed in {elapsed:.2f} seconds.")
    print(f"Total benchmarks evaluated: {len(benchmarks)}\n")
    
    print("-" * 100)
    print(f"{'Benchmark':<15} | {'Status':<18} | {'Version':<14} | {'Arch':<6} | {'Detection Method':<25}")
    print("-" * 100)
    
    for bm in benchmarks:
        status_str = "assisted/manual" if bm.status == BenchmarkStatus.READY_MANUAL else bm.status.value
        ver_str = bm.version or "None"
        arch_str = bm.architecture or "None"
        method_str = bm.detection_method or "not_found"
        print(f"{bm.name:<15} | {status_str:<18} | {ver_str:<14} | {arch_str:<6} | {method_str:<25}")
        print(f"   Display Name: {bm.display_name}")
        print(f"   Path:         {bm.executable_path or 'no exe path'}")
        print(f"   Reason:       {bm.reason}")
        print("-" * 100)
    
    # Assertions on real machine state
    bm_map = {bm.name: bm for bm in benchmarks}
    
    # Geekbench 6
    assert 'geekbench_6' in bm_map, "geekbench_6 entry missing"
    gb6 = bm_map['geekbench_6']
    assert gb6.status == BenchmarkStatus.READY_MANUAL
    assert gb6.version == "6.7.1.0"
    assert gb6.architecture == "x64"
    assert gb6.executable_path == r"C:\Program Files (x86)\Geekbench 6\Geekbench 6.exe"
    assert "filesystem pattern" in gb6.detection_method or "registry" in gb6.detection_method
    
    # Geekbench 7
    assert 'geekbench_7' in bm_map, "geekbench_7 entry missing"
    gb7 = bm_map['geekbench_7']
    assert gb7.status == BenchmarkStatus.READY_MANUAL
    assert gb7.version == "7.1.0.0"
    assert gb7.architecture == "x64"
    assert gb7.executable_path == r"C:\Program Files\Geekbench 7\Geekbench 7.exe"
    assert "filesystem pattern" in gb7.detection_method or "registry" in gb7.detection_method
    
    # Cinebench
    assert 'cinebench' in bm_map, "cinebench entry missing"
    cb = bm_map['cinebench']
    assert cb.status == BenchmarkStatus.READY_MANUAL
    assert cb.version == "2026.1.3.0"
    assert cb.architecture == "x64"
    assert cb.executable_path is None
    assert "Appx" in cb.detection_method
    
    # 3DMark
    assert 'd3dmark' in bm_map
    assert bm_map['d3dmark'].status == BenchmarkStatus.NOT_INSTALLED
    
    # glmark2
    assert 'glmark2' in bm_map
    assert bm_map['glmark2'].status == BenchmarkStatus.NOT_INSTALLED
    
    # Speedometer & JetStream
    assert 'speedometer' in bm_map
    assert bm_map['speedometer'].status == BenchmarkStatus.READY_MANUAL
    assert 'jetstream' in bm_map
    assert bm_map['jetstream'].status == BenchmarkStatus.READY_MANUAL
    
    print("\nALL REAL MACHINE SCAN ASSERTIONS PASSED!")


if __name__ == "__main__":
    test_normalize_version_unit()
    test_real_scan()
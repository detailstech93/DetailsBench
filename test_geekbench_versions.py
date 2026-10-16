#!/usr/bin/env python
"""Test Geekbench 6 vs 7 version distinction."""

from app.benchmark_manager import BenchmarkManager, BenchmarkStatus

def test_geekbench_version_detection():
    """Test that Geekbench 6 and 7 are detected as distinct versions."""
    print("Testing Geekbench version detection...")
    
    manager = BenchmarkManager()
    geekbench_versions = manager._detect_geekbench()
    
    assert len(geekbench_versions) == 2, f"Expected 2 Geekbench entries, got {len(geekbench_versions)}"
    
    gb_map = {gb.name: gb for gb in geekbench_versions}
    assert 'geekbench_6' in gb_map, "geekbench_6 missing"
    assert 'geekbench_7' in gb_map, "geekbench_7 missing"
    
    gb6 = gb_map['geekbench_6']
    gb7 = gb_map['geekbench_7']
    
    print(f"Geekbench 6: Name={gb6.name}, Version={gb6.version}, Status={gb6.status.value}, Arch={gb6.architecture}")
    print(f"Geekbench 7: Name={gb7.name}, Version={gb7.version}, Status={gb7.status.value}, Arch={gb7.architecture}")
    
    # On this machine, both are installed
    assert gb6.status == BenchmarkStatus.READY_MANUAL
    assert gb6.version == "6.7.1.0"
    assert gb6.architecture == "x64"
    assert gb6.executable_path == r"C:\Program Files (x86)\Geekbench 6\Geekbench 6.exe"
    
    assert gb7.status == BenchmarkStatus.READY_MANUAL
    assert gb7.version == "7.1.0.0"
    assert gb7.architecture == "x64"
    assert gb7.executable_path == r"C:\Program Files\Geekbench 7\Geekbench 7.exe"
    
    assert gb6.name != gb7.name
    assert gb6.version != gb7.version
    assert gb6.executable_path != gb7.executable_path
    
    print("\nAll Geekbench version detection tests passed successfully!")

if __name__ == "__main__":
    test_geekbench_version_detection()
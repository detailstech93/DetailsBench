"""Compare one benchmark between two sessions and explain why results may not be comparable.

compare_benchmark() returns the means, delta, delta %, a status (ok / warning /
not_comparable) and ordered issues. Blocking issues: no results, different
major versions (e.g. Geekbench 6 vs 7), different native architecture,
different runtimes, emulation. Drafted by the local agent, reviewed.
"""

from collections.abc import Mapping
from numbers import Real
from typing import Any, Dict, List, Optional


def _as_mapping(value: Any) -> Optional[Mapping]:
    """Return value if it is a Mapping, otherwise None."""
    if isinstance(value, Mapping):
        return value
    return None


def _get(mapping: Any, key: Any) -> Any:
    """Safely get a key from a Mapping."""
    mapping = _as_mapping(mapping)
    if mapping is None:
        return None
    try:
        return mapping.get(key)
    except Exception:
        return None


def _path(mapping: Any, *keys: Any) -> Any:
    """Safely traverse nested Mappings."""
    current: Any = mapping
    for key in keys:
        current = _get(current, key)
        if current is None:
            return None
    return current


def _known(value: Any) -> bool:
    """Return True when a value should be treated as known."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def _number(value: Any) -> Optional[float]:
    """Return a numeric value as float when possible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        try:
            return float(value)
        except Exception:
            return None
    return None


def _int(value: Any) -> Optional[int]:
    """Return an integer value when possible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _bool(value: Any) -> bool:
    """True for True or a non-zero int (SQLite stores booleans as 0/1); never for strings."""
    return isinstance(value, (bool, int)) and bool(value)


def _runtime_set(value: Any) -> Optional[set]:
    """Return a set for runtime list-like values."""
    if isinstance(value, (list, tuple, set, frozenset)):
        try:
            return set(value)
        except Exception:
            return None
    return None


def _major_version(version: Any) -> Optional[str]:
    """Return the first dot-separated component of a version string."""
    if not _known(version):
        return None
    text = str(version).strip()
    if not text:
        return None
    major = text.split(".", 1)[0].strip()
    if not major:
        return None
    return major


def _issue(level: str, code: str, message: str) -> Dict[str, str]:
    """Create an issue dictionary."""
    return {"level": level, "code": code, "message": message}


def compare_benchmark(
    benchmark: str,
    a: Mapping,
    b: Mapping,
    high_cv_percent: float = 5.0,
) -> dict:
    """Compare two benchmark sides and return comparison metadata."""
    issues: List[Dict[str, str]] = []

    mean_a_raw = _path(a, "summary", "stats", "mean")
    mean_b_raw = _path(b, "summary", "stats", "mean")

    mean_a_num = _number(mean_a_raw)
    mean_b_num = _number(mean_b_raw)

    mean_a_out = mean_a_raw if mean_a_num is not None else None
    mean_b_out = mean_b_raw if mean_b_num is not None else None

    delta: Optional[float] = None
    delta_percent: Optional[float] = None
    if mean_a_num is not None and mean_b_num is not None:
        delta = mean_b_num - mean_a_num
        if mean_a_num != 0:
            delta_percent = (delta / mean_a_num) * 100.0

    included_a = _int(_path(a, "summary", "included"))
    included_b = _int(_path(b, "summary", "included"))

    # 1. no_results
    if included_a == 0 or included_b == 0 or mean_a_num is None or mean_b_num is None:
        issues.append(_issue("block", "no_results", "No results on one or both sides."))

    version_a = _get(a, "version")
    version_b = _get(b, "version")
    version_a_known = _known(version_a)
    version_b_known = _known(version_b)
    major_a = _major_version(version_a)
    major_b = _major_version(version_b)

    # 2. major_version
    if version_a_known and version_b_known and major_a is not None and major_b is not None:
        if major_a != major_b:
            issues.append(_issue("block", "major_version", "Major benchmark versions differ."))
        elif str(version_a) != str(version_b):
            # 3. version
            issues.append(_issue("warn", "version", "Benchmark versions differ."))

    # 4. version_unknown
    if not (version_a_known and version_b_known):
        issues.append(_issue("warn", "version_unknown", "Benchmark version unknown on one or both sides."))

    os_a = _path(a, "manifest", "environment", "os_name", "value")
    os_b = _path(b, "manifest", "environment", "os_name", "value")

    # 5. os
    if _known(os_a) and _known(os_b) and str(os_a) != str(os_b):
        issues.append(_issue("warn", "os", "Operating systems differ."))

    arch_a = _path(a, "manifest", "environment", "native_arch", "value")
    arch_b = _path(b, "manifest", "environment", "native_arch", "value")

    # 6. arch
    if _known(arch_a) and _known(arch_b) and str(arch_a) != str(arch_b):
        issues.append(_issue("block", "arch", "Native architectures differ."))

    runtimes_a = _path(a, "manifest", "comparability", "runtimes")
    runtimes_b = _path(b, "manifest", "comparability", "runtimes")
    runtime_set_a = _runtime_set(runtimes_a)
    runtime_set_b = _runtime_set(runtimes_b)

    # 7. runtime
    if runtime_set_a is not None and runtime_set_b is not None and runtime_set_a != runtime_set_b:
        issues.append(_issue("block", "runtime", "Runtime lists differ."))

    emulated_a = _path(a, "manifest", "comparability", "any_emulated")
    emulated_b = _path(b, "manifest", "comparability", "any_emulated")

    # 8. emulated
    if _bool(emulated_a) or _bool(emulated_b):
        issues.append(_issue("block", "emulated", "Emulated runtimes detected."))

    # 9. few_runs
    for side, included in (("A", included_a), ("B", included_b)):
        if included is not None and included < 2:
            issues.append(_issue("warn", "few_runs", f"Side {side} has fewer than 2 runs."))

    cv_a = _number(_path(a, "summary", "stats", "cv_percent"))
    cv_b = _number(_path(b, "summary", "stats", "cv_percent"))
    threshold = _number(high_cv_percent)

    # 10. high_variance
    if threshold is not None:
        for side, cv in (("A", cv_a), ("B", cv_b)):
            if cv is not None and cv > threshold:
                issues.append(_issue("warn", "high_variance", f"Side {side} CV {cv:.1f}% exceeds threshold."))

    mixed_a = _path(a, "summary", "mixed_sources")
    mixed_b = _path(b, "summary", "mixed_sources")

    # 11. mixed_sources
    for side, value in (("A", mixed_a), ("B", mixed_b)):
        if _bool(value):
            issues.append(_issue("warn", "mixed_sources", f"Side {side} has mixed sources."))

    manual_a = _int(_path(a, "summary", "manual_included"))
    real_a = _int(_path(a, "summary", "real_included"))
    manual_b = _int(_path(b, "summary", "manual_included"))
    real_b = _int(_path(b, "summary", "real_included"))

    def _all_manual(manual: Optional[int], real: Optional[int]) -> bool:
        return manual is not None and real is not None and manual > 0 and real == 0

    def _all_real(real: Optional[int], manual: Optional[int]) -> bool:
        return real is not None and manual is not None and real > 0 and manual == 0

    # 12. manual_vs_real
    if (
        (_all_manual(manual_a, real_a) and _all_real(real_b, manual_b))
        or (_all_manual(manual_b, real_b) and _all_real(real_a, manual_a))
    ):
        issues.append(_issue("warn", "manual_vs_real", "One side is all manual and the other is all real."))

    test_a = _get(a, "is_test")
    test_b = _get(b, "is_test")

    # 13. test_data
    if _bool(test_a) or _bool(test_b):
        issues.append(_issue("warn", "test_data", "Test data present on one or both sides."))

    if any(issue["level"] == "block" for issue in issues):
        status = "not_comparable"
    elif any(issue["level"] == "warn" for issue in issues):
        status = "warning"
    else:
        status = "ok"

    return {
        "benchmark": benchmark,
        "mean_a": mean_a_out,
        "mean_b": mean_b_out,
        "delta": delta,
        "delta_percent": delta_percent,
        "status": status,
        "issues": issues,
    }

"""Tests for app.compare.compare_benchmark."""

from typing import Any, Dict, List, Optional

from app.compare import compare_benchmark


def make_manifest(
    os_name: Optional[str] = None,
    native_arch: Optional[str] = None,
    runtimes: Optional[List[str]] = None,
    any_emulated: bool = False,
) -> Dict[str, Any]:
    return {
        "environment": {
            "os_name": {"value": os_name},
            "native_arch": {"value": native_arch},
        },
        "comparability": {
            "runtimes": runtimes,
            "any_emulated": any_emulated,
        },
    }


def make_side(
    mean: Optional[float] = 100.0,
    included: int = 3,
    cv_percent: Optional[float] = 2.0,
    version: Optional[str] = "6.7.1.0",
    manifest: Optional[Dict[str, Any]] = None,
    is_test: bool = False,
    manual_included: int = 0,
    real_included: int = 3,
    mixed_sources: bool = False,
) -> Dict[str, Any]:
    return {
        "summary": {
            "stats": {
                "mean": mean,
                "cv_percent": cv_percent,
            },
            "included": included,
            "manual_included": manual_included,
            "real_included": real_included,
            "mixed_sources": mixed_sources,
        },
        "version": version,
        "manifest": manifest,
        "is_test": is_test,
    }


def issue_codes(result: Dict[str, Any]) -> List[str]:
    return [issue["code"] for issue in result["issues"]]


def issue_messages(result: Dict[str, Any], code: str) -> List[str]:
    return [issue["message"] for issue in result["issues"] if issue["code"] == code]


def test_identical_good_sides_ok_with_zero_delta() -> None:
    a = make_side(mean=100.0, included=3, cv_percent=2.0)
    b = make_side(mean=100.0, included=3, cv_percent=2.0)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "ok"
    assert result["delta"] == 0.0
    assert result["delta_percent"] == 0.0
    assert result["issues"] == []


def test_b_faster_by_ten_percent() -> None:
    a = make_side(mean=100.0, included=3, cv_percent=2.0)
    b = make_side(mean=110.0, included=3, cv_percent=2.0)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "ok"
    assert result["delta"] == 10.0
    assert result["delta_percent"] == 10.0


def test_geekbench_major_version_difference_blocks_but_delta_computed() -> None:
    a = make_side(mean=100.0, included=3, cv_percent=2.0, version="6.7.1.0")
    b = make_side(mean=110.0, included=3, cv_percent=2.0, version="7.1.0.0")

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "not_comparable"
    assert "major_version" in issue_codes(result)
    assert result["delta"] == 10.0
    assert result["delta_percent"] == 10.0


def test_minor_version_difference_warns() -> None:
    a = make_side(mean=100.0, included=3, cv_percent=2.0, version="6.7.1.0")
    b = make_side(mean=100.0, included=3, cv_percent=2.0, version="6.7.2.0")

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "warning"
    assert "version" in issue_codes(result)


def test_one_version_unknown_warns() -> None:
    a = make_side(mean=100.0, included=3, cv_percent=2.0, version="6.7.1.0")
    b = make_side(mean=100.0, included=3, cv_percent=2.0, version=None)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "warning"
    assert "version_unknown" in issue_codes(result)


def test_different_arch_blocks() -> None:
    a = make_side(manifest=make_manifest(native_arch="x86_64"))
    b = make_side(manifest=make_manifest(native_arch="arm64"))

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "not_comparable"
    assert "arch" in issue_codes(result)


def test_runtime_difference_blocks() -> None:
    a = make_side(manifest=make_manifest(runtimes=["native"]))
    b = make_side(manifest=make_manifest(runtimes=["proton"]))

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "not_comparable"
    assert "runtime" in issue_codes(result)


def test_emulated_blocks() -> None:
    a = make_side(manifest=make_manifest(any_emulated=True))
    b = make_side(manifest=make_manifest(any_emulated=False))

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "not_comparable"
    assert "emulated" in issue_codes(result)


def test_few_runs_on_side_b_warns_and_mentions_b() -> None:
    a = make_side(included=3)
    b = make_side(included=1)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "warning"
    assert "few_runs" in issue_codes(result)
    assert any("B" in message for message in issue_messages(result, "few_runs"))


def test_high_variance_message_contains_cv_one_decimal() -> None:
    a = make_side(cv_percent=2.0)
    b = make_side(cv_percent=7.5)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "warning"
    assert "high_variance" in issue_codes(result)
    assert any("7.5" in message for message in issue_messages(result, "high_variance"))


def test_manual_vs_real_warns() -> None:
    a = make_side(included=3, manual_included=3, real_included=0)
    b = make_side(included=3, manual_included=0, real_included=3)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "warning"
    assert "manual_vs_real" in issue_codes(result)


def test_test_data_warns() -> None:
    a = make_side(is_test=True)
    b = make_side(is_test=False)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "warning"
    assert "test_data" in issue_codes(result)


def test_no_results_when_included_zero() -> None:
    a = make_side(included=0)
    b = make_side(included=3)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "not_comparable"
    assert "no_results" in issue_codes(result)


def test_missing_manifest_on_both_sides_has_no_manifest_issues() -> None:
    a = make_side(manifest=None)
    b = make_side(manifest=None)

    result = compare_benchmark("bench", a, b)

    assert result["status"] == "ok"
    assert "os" not in issue_codes(result)
    assert "arch" not in issue_codes(result)
    assert "runtime" not in issue_codes(result)


def test_mean_a_zero_delta_percent_none() -> None:
    a = make_side(mean=0.0)
    b = make_side(mean=10.0)

    result = compare_benchmark("bench", a, b)

    assert result["delta"] == 10.0
    assert result["delta_percent"] is None


def test_issue_order_follows_check_order() -> None:
    a = make_side(
        mean=100.0,
        included=0,
        cv_percent=7.5,
        version="6.7.1.0",
        manifest=make_manifest(os_name="linux"),
        is_test=True,
        manual_included=0,
        real_included=0,
        mixed_sources=True,
    )
    b = make_side(
        mean=110.0,
        included=1,
        cv_percent=2.0,
        version=None,
        manifest=make_manifest(os_name="windows"),
        is_test=False,
        manual_included=0,
        real_included=0,
        mixed_sources=False,
    )

    result = compare_benchmark("bench", a, b)

    assert issue_codes(result) == [
        "no_results",
        "version_unknown",
        "os",
        "few_runs",        # side A (0 runs)
        "few_runs",        # side B (1 run)
        "high_variance",
        "mixed_sources",
        "test_data",
    ]


def test_sqlite_style_int_flags_count_as_true() -> None:
    """is_test comes from SQLite as 0/1; a string never counts as true."""
    assert "test_data" in issue_codes(compare_benchmark("b", make_side(is_test=1), make_side()))
    assert "test_data" not in issue_codes(compare_benchmark("b", make_side(is_test=0), make_side()))
    assert "test_data" not in issue_codes(compare_benchmark("b", make_side(is_test="yes"), make_side()))


def _run(name: str, score: Optional[float], version: Optional[str] = "7.1.0.0", **kw: Any) -> Dict[str, Any]:
    run = {"benchmark_name": name, "benchmark_version": version, "score": score, "status": "completed",
           "is_warmup": 0, "is_test": 0, "is_manual": 1}
    run.update(kw)
    return run


def test_compare_sessions_end_to_end() -> None:
    from app.compare import compare_sessions
    a = [_run("geekbench_7", s) for s in (2500.0, 2510.0, 2490.0)] + [_run("cinebench", 1000.0, "2026.1.3.0")]
    b = [_run("geekbench_7", s) for s in (2600.0, 2610.0, 2590.0)] + [_run("geekbench_7", 9999.0, is_warmup=1)]
    rows = {r["benchmark"]: r for r in compare_sessions(a, b)}
    gb = rows["geekbench_7"]
    assert gb["status"] == "ok" and round(gb["delta_percent"], 2) == 4.0     # warmup 9999 ignored
    assert rows["cinebench"]["status"] == "not_comparable"                   # only in session A
    assert "no_results" in issue_codes(rows["cinebench"])


def test_mixed_versions_inside_one_session_become_unknown() -> None:
    from app.compare import compare_sessions
    a = [_run("geekbench_7", 2500.0, "7.1.0.0"), _run("geekbench_7", 2500.0, "7.2.0.0")]
    b = [_run("geekbench_7", 2500.0), _run("geekbench_7", 2500.0)]
    assert "version_unknown" in issue_codes(compare_sessions(a, b)[0])

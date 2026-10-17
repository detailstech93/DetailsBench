"""Extra run metrics (frame capture and telemetry) summarized per benchmark and compared.

Runs are included by exactly the same rules as the score statistics (app.stats.summarize_runs):
completed, measured (not warmup), with a score, and test runs only when the session is a test
session. A metric whose unit differs between the included runs is reported without numbers.
"""

import math
from typing import Callable, Dict, List, Mapping, Optional

from app.stats import compute_stats, summarize_runs


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def run_included(run: Mapping, include_test: bool) -> bool:
    return summarize_runs([run], include_test=include_test)["included"] == 1


def summarize_metrics(runs: List[Mapping], metrics_of: Callable[[Mapping], Mapping],
                      include_test: bool = False) -> Dict[str, Dict[str, dict]]:
    """{benchmark: {metric: {unit, count, mean, min, max, mixed_units}}}; metrics_of(run) -> {name: (value, unit)}."""
    values: Dict[str, Dict[str, list]] = {}
    units: Dict[str, Dict[str, set]] = {}
    for run in runs:
        if not run_included(run, include_test):
            continue
        bench = run.get("benchmark_name")
        for name, pair in (metrics_of(run) or {}).items():
            value, unit = pair if isinstance(pair, (tuple, list)) else (pair, None)
            if _finite(value):
                values.setdefault(bench, {}).setdefault(name, []).append(float(value))
                units.setdefault(bench, {}).setdefault(name, set()).add(unit)
    out: Dict[str, Dict[str, dict]] = {}
    for bench, metrics in values.items():
        for name, vals in sorted(metrics.items()):
            unit_set = units[bench][name]
            if len(unit_set) > 1:
                out.setdefault(bench, {})[name] = {"unit": None, "count": len(vals), "mean": None,
                                                   "min": None, "max": None, "mixed_units": True}
                continue
            stats = compute_stats(vals)
            out.setdefault(bench, {})[name] = {"unit": next(iter(unit_set)), "count": stats["count"],
                                               "mean": stats["mean"], "min": stats["min"], "max": stats["max"],
                                               "mixed_units": False}
    return out


def compare_metrics(a: Mapping[str, Mapping[str, dict]], b: Mapping[str, Mapping[str, dict]]) -> List[dict]:
    """One row per (benchmark, metric) present on either side; delta only when both means exist with the same unit."""
    rows = []
    for bench in sorted(set(a) | set(b)):
        side_a, side_b = a.get(bench) or {}, b.get(bench) or {}
        for name in sorted(set(side_a) | set(side_b)):
            ma, mb = side_a.get(name), side_b.get(name)
            mean_a = ma.get("mean") if ma else None
            mean_b = mb.get("mean") if mb else None
            unit_a = ma.get("unit") if ma else None
            unit_b = mb.get("unit") if mb else None
            delta = delta_percent = None
            note = None
            if ma is None or mb is None:
                note = f"only on side {'A' if mb is None else 'B'}"
            elif (ma.get("mixed_units") or mb.get("mixed_units")) or unit_a != unit_b:
                note = "units differ"
            elif mean_a is not None and mean_b is not None:
                delta = mean_b - mean_a
                delta_percent = delta / mean_a * 100.0 if mean_a != 0 else None
            unit = None if note == "units differ" else (unit_a if ma else unit_b)
            rows.append({"benchmark": bench, "metric": name, "unit": unit,
                         "mean_a": mean_a, "mean_b": mean_b, "delta": delta, "delta_percent": delta_percent,
                         "note": note})
    return rows


def metrics_for_db(db) -> Callable[[Mapping], Mapping]:
    """metrics_of() reading result_metrics; a run without an id has none."""
    def metrics_of(run: Mapping) -> Mapping:
        rid = run.get("id")
        return db.get_result_metrics(rid) if rid is not None else {}
    return metrics_of


def metrics_dict(pairs: Mapping) -> Optional[dict]:
    """{name: (value, unit)} -> {name: {value, unit}} for JSON export."""
    if not pairs:
        return None
    return {name: {"value": value, "unit": unit} for name, (value, unit) in pairs.items()}

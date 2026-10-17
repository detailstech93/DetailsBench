"""Run statistics: mean, median, min/max, sample stdev, CV%.

Only completed, non-warmup, non-test runs with a finite score are counted;
every exclusion is counted by reason. Drafted by the local agent, reviewed.
"""

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Optional


def _is_real_number(value: object) -> bool:
    """Return True for finite int/float values, excluding booleans."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def compute_stats(values: Sequence[float]) -> dict[str, Optional[float]]:
    """Compute descriptive statistics for a sequence of numeric values."""
    count = len(values)

    if count == 0:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "stdev": None,
            "cv_percent": None,
        }

    mean = statistics.mean(values)
    median = statistics.median(values)
    min_value = min(values)
    max_value = max(values)

    if count < 2:
        stdev: Optional[float] = None
        cv_percent: Optional[float] = None
    else:
        stdev = statistics.stdev(values)
        if mean == 0 or stdev is None:
            cv_percent = None
        else:
            cv_percent = stdev / mean * 100

    return {
        "count": count,
        "mean": mean,
        "median": median,
        "min": min_value,
        "max": max_value,
        "stdev": stdev,
        "cv_percent": cv_percent,
    }


def summarize_runs(runs: Sequence[Mapping], include_test: bool = False) -> dict:
    """Summarize included run scores and count exclusion reasons."""
    included_scores: list[float] = []
    manual_included = 0
    real_included = 0

    excluded = {
        "test": 0,
        "warmup": 0,
        "not_completed": 0,
        "missing_score": 0,
    }

    for record in runs:
        if not isinstance(record, Mapping):
            excluded["missing_score"] += 1
            continue

        try:
            is_test = bool(record.get("is_test", False))
            is_warmup = bool(record.get("is_warmup", False))
            status = record.get("status")
            score = record.get("score")
            is_manual = bool(record.get("is_manual", False))

            if is_test and not include_test:
                reason = "test"
            elif is_warmup:
                reason = "warmup"
            elif status != "completed":
                reason = "not_completed"
            elif not _is_real_number(score):
                reason = "missing_score"
            else:
                reason = None
        except Exception:
            reason = "missing_score"

        if reason is not None:
            excluded[reason] += 1
        else:
            included_scores.append(score)
            if is_manual:
                manual_included += 1
            else:
                real_included += 1

    stats = compute_stats(included_scores)

    return {
        "stats": stats,
        "included": len(included_scores),
        "excluded": excluded,
        "manual_included": manual_included,
        "real_included": real_included,
        "mixed_sources": manual_included > 0 and real_included > 0,
    }

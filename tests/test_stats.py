"""Tests for app/stats.py (pure) plus one integration with Database.get_runs."""

"""Tests for app.stats."""

from app.stats import compute_stats, summarize_runs


def test_compute_stats_three_values():
    stats = compute_stats([100, 110, 90])

    assert stats["count"] == 3
    assert stats["mean"] == 100.0
    assert stats["median"] == 100.0
    assert stats["min"] == 90.0
    assert stats["max"] == 110.0
    assert stats["stdev"] == 10.0
    assert stats["cv_percent"] == 10.0


def test_compute_stats_single_value():
    stats = compute_stats([42])

    assert stats["count"] == 1
    assert stats["mean"] == 42.0
    assert stats["median"] == 42.0
    assert stats["min"] == 42.0
    assert stats["max"] == 42.0
    assert stats["stdev"] is None
    assert stats["cv_percent"] is None


def test_compute_stats_empty():
    stats = compute_stats([])

    assert stats["count"] == 0
    assert stats["mean"] is None
    assert stats["median"] is None
    assert stats["min"] is None
    assert stats["max"] is None
    assert stats["stdev"] is None
    assert stats["cv_percent"] is None


def test_compute_stats_mean_zero_has_no_cv():
    stats = compute_stats([-1, 1])

    assert stats["count"] == 2
    assert stats["mean"] == 0.0
    assert stats["stdev"] is not None
    assert stats["cv_percent"] is None


def test_summarize_runs_counts_and_stats():
    runs = [
        {"score": 80.0, "status": "completed", "is_warmup": 1, "is_test": 0, "is_manual": 0},
        {"score": 100.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": 110.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": 90.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": None, "status": "failed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": 0.0, "status": "failed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": 120.0, "status": "completed", "is_warmup": 0, "is_test": 1, "is_manual": 0},
        {"score": None, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": float("nan"), "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        None,
    ]

    result = summarize_runs(runs)

    assert result["included"] == 3
    assert result["excluded"] == {
        "test": 1,
        "warmup": 1,
        "not_completed": 2,
        "missing_score": 3,
    }

    stats = result["stats"]
    assert stats["count"] == 3
    assert stats["mean"] == 100.0
    assert stats["median"] == 100.0
    assert stats["min"] == 90.0
    assert stats["max"] == 110.0
    assert stats["stdev"] == 10.0
    assert stats["cv_percent"] == 10.0

    assert result["manual_included"] == 0
    assert result["real_included"] == 3
    assert result["mixed_sources"] is False


def test_summarize_runs_include_test():
    runs = [
        {"score": 100.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": 110.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": 90.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
        {"score": 120.0, "status": "completed", "is_warmup": 0, "is_test": 1, "is_manual": 0},
    ]

    result = summarize_runs(runs, include_test=True)

    assert result["included"] == 4
    assert result["excluded"]["test"] == 0
    assert result["stats"]["count"] == 4
    assert result["stats"]["mean"] == 105.0
    assert result["real_included"] == 4
    assert result["manual_included"] == 0
    assert result["mixed_sources"] is False


def test_summarize_runs_mixed_sources():
    runs = [
        {"score": 10.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 1},
        {"score": 20.0, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
    ]

    result = summarize_runs(runs)

    assert result["included"] == 2
    assert result["manual_included"] == 1
    assert result["real_included"] == 1
    assert result["mixed_sources"] is True


def test_summarize_runs_bool_score_is_not_numeric():
    runs = [
        {"score": True, "status": "completed", "is_warmup": 0, "is_test": 0, "is_manual": 0},
    ]

    result = summarize_runs(runs)

    assert result["included"] == 0
    assert result["excluded"]["missing_score"] == 1
    assert result["stats"]["count"] == 0


def test_summarize_rows_from_database(tmp_path):
    from app.database import Database
    db = Database(str(tmp_path / "s.db"))
    sid = db.create_session("s")
    db.create_result(sid, "cinebench", "cpu", 1500.0, is_warmup=True, is_manual=True)
    for score in (1000.0, 1100.0, 900.0):
        db.create_result(sid, "cinebench", "cpu", score, is_manual=True)
    db.create_result(sid, "cinebench", "cpu", None, status="timed_out", is_manual=True)
    summary = summarize_runs(db.get_runs(sid, "cinebench"))
    assert summary["included"] == 3
    assert summary["stats"]["mean"] == 1000.0 and summary["stats"]["cv_percent"] == 10.0
    assert summary["excluded"] == {"test": 0, "warmup": 1, "not_completed": 1, "missing_score": 0}
    assert summary["manual_included"] == 3 and summary["mixed_sources"] is False

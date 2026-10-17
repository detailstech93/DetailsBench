"""Tests for run-level result fields (Phase 4A): status, warmup, run order, get_runs."""

import sqlite3

import pytest

from app.database import RESULT_STATUSES, Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "runs.db"))


def test_status_defaults_from_passed(db):
    sid = db.create_session("s")
    db.create_result(sid, "geekbench_7", "cpu", 2500.0)
    db.create_result(sid, "geekbench_7", "cpu", None, passed=False)
    assert [r["status"] for r in db.get_runs(sid)] == ["completed", "failed"]


@pytest.mark.parametrize("status", [s for s in RESULT_STATUSES if s != "completed"])
def test_non_completed_run_cannot_have_a_score(db, status):
    sid = db.create_session("s")
    with pytest.raises(ValueError, match="must not have a score"):
        db.create_result(sid, "x", "cpu", 0.0, status=status)
    db.create_result(sid, "x", "cpu", None, status=status)   # allowed without a score
    assert db.get_runs(sid)[0]["score"] is None


def test_unknown_status_rejected(db):
    sid = db.create_session("s")
    with pytest.raises(ValueError, match="unknown result status"):
        db.create_result(sid, "x", "cpu", 1.0, status="done")


def test_warmup_order_and_filters(db):
    sid = db.create_session("s")
    other = db.create_session("t", is_test=True)
    db.create_result(sid, "cinebench", "cpu", 900.0, is_warmup=True, run_index=0)
    db.create_result(sid, "cinebench", "cpu", 1000.0, run_index=2, is_manual=True, note="typed in")
    db.create_result(sid, "cinebench", "cpu", 990.0, run_index=1, is_manual=True)
    db.create_result(sid, "geekbench_7", "cpu", 2500.0)
    db.create_result(other, "cinebench", "cpu", 5.0)
    runs = db.get_runs(sid, "cinebench")
    assert [(r["run_index"], r["is_warmup"], r["score"]) for r in runs] == [(0, 1, 900.0), (1, 0, 990.0), (2, 0, 1000.0)]
    assert runs[2]["note"] == "typed in" and runs[2]["is_manual"] == 1
    assert all(r["session_id"] == sid for r in db.get_runs(benchmark_name="cinebench"))   # test session hidden
    assert len(db.get_runs(benchmark_name="cinebench", include_test=True)) == 4
    assert db.get_runs(benchmark_name="cinebench", include_test=True)[-1]["is_test"] == 1


def test_migration_from_3c_schema_keeps_old_rows(tmp_path):
    """Previous schema (no status/is_warmup/run_index/note): old rows stay as written."""
    path = str(tmp_path / "prev.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                 "description TEXT, created_at TEXT NOT NULL, is_test BOOLEAN DEFAULT 0, is_manual BOOLEAN DEFAULT 0)")
    conn.execute("CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, "
                 "benchmark_name TEXT NOT NULL, benchmark_type TEXT NOT NULL, score REAL, unit TEXT DEFAULT 'points', "
                 "passed BOOLEAN DEFAULT 1, timestamp TEXT NOT NULL DEFAULT (datetime('now')), benchmark_version TEXT, "
                 "is_test BOOLEAN DEFAULT 0, is_manual BOOLEAN DEFAULT 0)")
    conn.execute("INSERT INTO sessions (name, created_at) VALUES ('old', '2026-10-15')")
    conn.execute("INSERT INTO results (session_id, benchmark_name, benchmark_type, score, passed) VALUES (1, 'gb', 'cpu', 1500, 1)")
    conn.execute("INSERT INTO results (session_id, benchmark_name, benchmark_type, score, passed) VALUES (1, 'gb', 'cpu', NULL, 0)")
    conn.commit()
    conn.close()

    db = Database(path)
    runs = db.get_runs(1)
    assert [(r["score"], r["status"], r["is_warmup"]) for r in runs] == [(1500.0, "completed", 0), (None, "failed", 0)]
    raw = sqlite3.connect(path).execute("SELECT status FROM results").fetchall()
    assert raw == [(None,), (None,)]   # old rows were not rewritten
    assert len(db.get_session(1)["results"]) == 2

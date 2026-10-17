"""Tests for Database.search_sessions (temporary database)."""

import sqlite3

import pytest

from app.database import Database


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / "search.db"))
    path = db.db_path

    def session(name, created, description=None, is_test=False):
        sid = db.create_session(name, description, is_test=is_test)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (created, sid))
        conn.commit()
        conn.close()
        return sid

    a = session("Laptop baseline", "2026-10-01T10:00:00", "fresh Windows install")
    b = session("After driver update", "2026-10-15T09:30:00", "NVIDIA 100% fan_curve")
    c = session("Test fixture", "2026-10-16T12:00:00", is_test=True)
    db.create_result(a, "geekbench_7", "cpu", 2500.0)
    db.create_result(a, "cinebench", "cpu", 1000.0)
    db.create_result(b, "cinebench", "cpu", 1050.0)
    db.save_session_manifest(b, {"schema_version": 1})
    db.ids = (a, b, c)
    return db


def ids(rows):
    return [r["id"] for r in rows]


def test_all_sessions_newest_first_without_test(db):
    a, b, c = db.ids
    rows = db.search_sessions()
    assert ids(rows) == [b, a]
    assert rows[1]["benchmarks"] == ["cinebench", "geekbench_7"] and rows[1]["run_count"] == 2
    assert rows[0]["has_manifest"] is True and rows[1]["has_manifest"] is False
    assert ids(db.search_sessions(include_test=True)) == [c, b, a]


def test_text_matches_name_or_description_case_insensitive(db):
    a, b, _ = db.ids
    assert ids(db.search_sessions("BASELINE")) == [a]
    assert ids(db.search_sessions("windows install")) == [a]


def test_percent_and_underscore_are_literal(db):
    _, b, _ = db.ids
    assert ids(db.search_sessions("100%")) == [b]
    assert ids(db.search_sessions("fan_curve")) == [b]
    assert db.search_sessions("fan%curve") == []
    assert ids(db.search_sessions("_")) == [b]   # only the real underscore in "fan_curve" matches


def test_benchmark_and_date_filters(db):
    a, b, _ = db.ids
    assert ids(db.search_sessions(benchmark_name="geekbench_7")) == [a]
    assert ids(db.search_sessions(benchmark_name="geekbench")) == [a]      # suite id matches its majors
    assert db.search_sessions(benchmark_name="geek") == []                # but not arbitrary prefixes
    assert ids(db.search_sessions(benchmark_name="cinebench")) == [b, a]
    assert ids(db.search_sessions(date_from="2026-10-15")) == [b]
    assert ids(db.search_sessions(date_to="2026-10-01")) == [a]            # inclusive end date
    assert ids(db.search_sessions("driver", "cinebench", "2026-10-01", "2026-10-31")) == [b]

"""Tests for environment snapshot storage (drafted by the local agent, reviewed).

Every database is a tmp_path file."""

import sqlite3

from app.database import Database


def _make_db(tmp_path):
    return Database(str(tmp_path / "benchmark.db"))


def test_save_and_get_round_trip(tmp_path):
    db = _make_db(tmp_path)
    profile = {"b": 2, "a": {"z": 1, "y": [1, 2, 3]}}

    snapshot_id = db.save_environment_snapshot(profile)
    snapshot = db.get_environment_snapshot(snapshot_id)

    assert snapshot is not None
    assert snapshot["id"] == snapshot_id
    assert snapshot["session_id"] is None
    assert snapshot["profile"] == profile


def test_get_missing_snapshot_returns_none(tmp_path):
    db = _make_db(tmp_path)

    assert db.get_environment_snapshot(999999) is None


def test_get_latest_skips_test_rows_by_default(tmp_path):
    db = _make_db(tmp_path)

    normal_id = db.save_environment_snapshot({"kind": "normal"}, is_test=False)
    test_id = db.save_environment_snapshot({"kind": "test"}, is_test=True)

    latest = db.get_latest_environment_snapshot()
    assert latest is not None
    assert latest["id"] == normal_id
    assert latest["is_test"] is False

    latest_with_tests = db.get_latest_environment_snapshot(include_test=True)
    assert latest_with_tests is not None
    assert latest_with_tests["id"] == test_id
    assert latest_with_tests["is_test"] is True


def test_snapshot_can_be_linked_to_session(tmp_path):
    db = _make_db(tmp_path)
    session_id = db.create_session("Linked session", "description")

    snapshot_id = db.save_environment_snapshot({"linked": True}, session_id=session_id)
    snapshot = db.get_environment_snapshot(snapshot_id)

    assert snapshot is not None
    assert snapshot["session_id"] == session_id


def test_migration_from_previous_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL,
            is_test BOOLEAN DEFAULT 0,
            is_manual BOOLEAN DEFAULT 0
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            benchmark_name TEXT NOT NULL,
            benchmark_type TEXT NOT NULL,
            score REAL,
            unit TEXT DEFAULT 'points',
            passed BOOLEAN DEFAULT 1,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            benchmark_version TEXT,
            is_test BOOLEAN DEFAULT 0,
            is_manual BOOLEAN DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS hardware_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            cpu_brand TEXT,
            cpu_model TEXT,
            core_count INTEGER,
            thread_count INTEGER,
            ram_size_gb REAL,
            gpu_name TEXT,
            operating_system TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
        """
    )

    cursor.execute(
        "INSERT INTO sessions (name, description, created_at, is_test, is_manual) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Legacy session", "Legacy description", "2024-01-01T00:00:00", 0, 0)
    )
    old_session_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO results (session_id, benchmark_name, benchmark_type, score, unit, passed, "
        "timestamp, benchmark_version, is_test, is_manual) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (old_session_id, "CPU", "cpu", 42.0, "points", 1, "2024-01-01T00:00:00", "6", 0, 0)
    )

    conn.commit()
    conn.close()

    db = Database(str(db_path))
    session = db.get_session(old_session_id)

    assert session is not None
    assert len(session["results"]) == 1
    assert session["results"][0]["score"] == 42.0

    snapshot_id = db.save_environment_snapshot({"migrated": True}, session_id=old_session_id)
    snapshot = db.get_environment_snapshot(snapshot_id)

    assert snapshot is not None
    assert snapshot["session_id"] == old_session_id
    assert snapshot["profile"] == {"migrated": True}


def test_discovery_snapshot_round_trip_and_link(tmp_path):
    db = _make_db(tmp_path)
    env_id = db.save_environment_snapshot({"os": "x"})
    result = {"software": [{"id": "steam", "name": "Steam"}], "games": [], "stores": {},
              "diagnostics": [], "step_seconds": {}}
    sid = db.save_discovery_snapshot(result, env_id)
    db.save_discovery_snapshot({"software": []}, is_test=True)
    latest = db.get_latest_discovery_snapshot()
    assert latest["id"] == sid
    assert latest["environment_snapshot_id"] == env_id
    assert latest["result"] == result
    assert db.get_latest_discovery_snapshot(include_test=True)["is_test"] is True


def test_discovery_table_added_to_previous_schema(tmp_path):
    """Database created by the 3A schema (no discovery table) gains it additively."""
    path = str(tmp_path / "prev.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                 "description TEXT, created_at TEXT NOT NULL, is_test BOOLEAN DEFAULT 0, is_manual BOOLEAN DEFAULT 0)")
    conn.execute("INSERT INTO sessions (name, created_at) VALUES ('kept', '2026-10-17')")
    conn.commit(); conn.close()
    db = Database(path)
    assert [s["name"] for s in db.get_sessions()] == ["kept"]
    assert db.save_discovery_snapshot({"software": []}) == 1

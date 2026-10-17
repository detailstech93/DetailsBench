"""SQLite database models for PC Benchmark Manager."""

import json
import sqlite3
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Result states (Phase 2 spec). Only "completed" runs may carry a score.
RESULT_STATUSES = ("completed", "failed", "timed_out", "cancelled", "skipped",
                   "invalid", "awaiting_manual_input")


class Database:
    """Thread-safe database manager with connection pooling support."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a new database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    
    def _init_db(self):
        """Initialize database tables if they don't exist."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Create sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Create results table - stores individual benchmark results from sessions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    benchmark_name TEXT NOT NULL,
                    benchmark_type TEXT NOT NULL,
                    score REAL,
                    unit TEXT DEFAULT 'points',
                    passed BOOLEAN DEFAULT 1,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    benchmark_version TEXT,  -- Store Geekbench 6 vs 7 version
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)

            # Hardware info table - stores system information from sessions
            cursor.execute("""
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
            """)

            # Environment snapshots (Phase 3A) - one EnvironmentProfile as JSON
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS environment_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NULL REFERENCES sessions(id),
                    created_at TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    is_test BOOLEAN DEFAULT 0
                )
            """)

            # Discovery snapshots (Phase 3B) - matched software/games only, as JSON
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    environment_snapshot_id INTEGER NULL REFERENCES environment_snapshots(id),
                    created_at TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    is_test BOOLEAN DEFAULT 0
                )
            """)

            # Session manifests (Phase 4A) - one reproduction record per session
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_manifests (
                    session_id INTEGER PRIMARY KEY REFERENCES sessions(id),
                    created_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                )
            """)

            # ── Additive migrations ──────────────────────────────────────────
            cursor.execute("PRAGMA table_info(results)")
            results_cols = [col[1] for col in cursor.fetchall()]

            if 'benchmark_version' not in results_cols:
                cursor.execute("ALTER TABLE results ADD COLUMN benchmark_version TEXT")
                logger.info("Migrated results table: added benchmark_version column")

            cursor.execute("PRAGMA table_info(sessions)")
            sessions_cols = [col[1] for col in cursor.fetchall()]

            # Add is_test column to sessions (default 0 = real data)
            if 'is_test' not in sessions_cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN is_test BOOLEAN DEFAULT 0")
                logger.info("Migrated sessions table: added is_test column")

            # Add is_manual column to sessions (default 0 = auto-run)
            if 'is_manual' not in sessions_cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN is_manual BOOLEAN DEFAULT 0")
                logger.info("Migrated sessions table: added is_manual column")

            # Add is_test column to results (default 0 = real data)
            if 'is_test' not in results_cols:
                cursor.execute("ALTER TABLE results ADD COLUMN is_test BOOLEAN DEFAULT 0")
                logger.info("Migrated results table: added is_test column")

            # Add is_manual column to results (default 0 = auto-run)
            if 'is_manual' not in results_cols:
                cursor.execute("ALTER TABLE results ADD COLUMN is_manual BOOLEAN DEFAULT 0")
                logger.info("Migrated results table: added is_manual column")

            # Phase 4A: run status, warmup flag, run order, free-text note.
            # status stays NULL on old rows; readers derive it from `passed`.
            for column, ddl in (("status", "TEXT"), ("is_warmup", "BOOLEAN DEFAULT 0"),
                                ("run_index", "INTEGER"), ("note", "TEXT")):
                if column not in results_cols:
                    cursor.execute(f"ALTER TABLE results ADD COLUMN {column} {ddl}")
                    logger.info("Migrated results table: added %s column", column)

            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            raise
        finally:
            if 'conn' in locals():
                conn.close()
    
    def create_session(self, name: str, description: Optional[str] = None,
                       is_test: bool = False, is_manual: bool = False) -> int:
        """Create a new benchmark session and return its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO sessions (name, description, created_at, is_test, is_manual) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, description, datetime.now().isoformat(), is_test, is_manual)
        )

        session_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return session_id

    def create_result(self, session_id: int, benchmark_name: str,
                      benchmark_type: str, score: Optional[float] = None,
                      unit: str = "points", passed: bool = True,
                      benchmark_version: Optional[str] = None,
                      is_test: bool = False, is_manual: bool = False,
                      status: Optional[str] = None, is_warmup: bool = False,
                      run_index: Optional[int] = None, note: Optional[str] = None) -> int:
        """Add one run result to the specified session.

        status defaults to "completed" when passed else "failed". A run that
        is not completed must not carry a score (a failure is never stored
        as a number, in particular never as zero).
        """
        if status is None:
            status = "completed" if passed else "failed"
        if status not in RESULT_STATUSES:
            raise ValueError(f"unknown result status {status!r}")
        if status != "completed" and score is not None:
            raise ValueError(f"a {status} run must not have a score (got {score!r})")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO results (session_id, benchmark_name, benchmark_type, score, unit,
                                   passed, benchmark_version, is_test, is_manual,
                                   status, is_warmup, run_index, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, benchmark_name, benchmark_type, score, unit,
             status == "completed", benchmark_version or None, is_test, is_manual,
             status, is_warmup, run_index, note)
        )
        result_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return result_id

    def get_runs(self, session_id: Optional[int] = None, benchmark_name: Optional[str] = None,
                 include_test: bool = False) -> list:
        """Run rows for statistics, with an effective status for old rows.

        Old rows (status NULL) count as "completed" if passed else "failed".
        is_test is true when either the result or its session is a test.
        """
        where, params = [], []
        if session_id is not None:
            where.append("r.session_id = ?")
            params.append(session_id)
        if benchmark_name is not None:
            where.append("r.benchmark_name = ?")
            params.append(benchmark_name)
        if not include_test:
            where.append("COALESCE(r.is_test, 0) = 0 AND COALESCE(s.is_test, 0) = 0")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT r.id, r.session_id, r.benchmark_name, r.benchmark_type, r.benchmark_version,
                   r.score, r.unit, r.timestamp, r.run_index, r.note,
                   COALESCE(r.status, CASE WHEN r.passed THEN 'completed' ELSE 'failed' END) AS status,
                   COALESCE(r.is_warmup, 0) AS is_warmup,
                   CASE WHEN COALESCE(r.is_test, 0) OR COALESCE(s.is_test, 0) THEN 1 ELSE 0 END AS is_test,
                   COALESCE(r.is_manual, 0) AS is_manual
            FROM results r JOIN sessions s ON r.session_id = s.id
            {clause}
            ORDER BY r.session_id, r.benchmark_name, COALESCE(r.run_index, r.id)
        """, params)
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def save_environment_snapshot(self, profile: dict, session_id: Optional[int] = None,
                                  is_test: bool = False) -> int:
        """Save an environment snapshot and return its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO environment_snapshots (session_id, created_at, profile_json, is_test) "
            "VALUES (?, ?, ?, ?)",
            (session_id, datetime.now().isoformat(), json.dumps(profile, sort_keys=True), is_test)
        )

        snapshot_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return snapshot_id

    def get_environment_snapshot(self, snapshot_id: int) -> Optional[dict]:
        """Return a single environment snapshot by ID, or None if missing."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, session_id, created_at, is_test, profile_json "
            "FROM environment_snapshots WHERE id = ?",
            (snapshot_id,)
        )

        row = cursor.fetchone()
        conn.close()

        if row is None:
            return None

        return self._snapshot_row(row)

    def get_latest_environment_snapshot(self, include_test: bool = False) -> Optional[dict]:
        """Return the newest environment snapshot, optionally including test rows."""
        conn = self._get_connection()
        cursor = conn.cursor()

        if include_test:
            cursor.execute(
                "SELECT id, session_id, created_at, is_test, profile_json "
                "FROM environment_snapshots ORDER BY id DESC LIMIT 1"
            )
        else:
            cursor.execute(
                "SELECT id, session_id, created_at, is_test, profile_json "
                "FROM environment_snapshots WHERE is_test = 0 ORDER BY id DESC LIMIT 1"
            )

        row = cursor.fetchone()
        conn.close()

        if row is None:
            return None

        return self._snapshot_row(row)

    def save_discovery_snapshot(self, result: dict, environment_snapshot_id: Optional[int] = None,
                                is_test: bool = False) -> int:
        """Save a DiscoveryResult dict (matched entries only) and return its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO discovery_snapshots (environment_snapshot_id, created_at, result_json, is_test) "
            "VALUES (?, ?, ?, ?)",
            (environment_snapshot_id, datetime.now().isoformat(), json.dumps(result, sort_keys=True), is_test)
        )
        snapshot_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return snapshot_id

    def get_latest_discovery_snapshot(self, include_test: bool = False) -> Optional[dict]:
        """Newest discovery snapshot: {id, environment_snapshot_id, created_at, is_test, result}."""
        conn = self._get_connection()
        cursor = conn.cursor()
        where = "" if include_test else "WHERE is_test = 0 "
        cursor.execute(
            "SELECT id, environment_snapshot_id, created_at, is_test, result_json "
            f"FROM discovery_snapshots {where}ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        return {"id": row[0], "environment_snapshot_id": row[1], "created_at": row[2],
                "is_test": bool(row[3]), "result": json.loads(row[4])}

    def save_session_manifest(self, session_id: int, manifest: dict) -> None:
        """Store the session's manifest once; a second save raises ValueError."""
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO session_manifests (session_id, created_at, manifest_json) VALUES (?, ?, ?)",
                (session_id, datetime.now().isoformat(), json.dumps(manifest, sort_keys=True)))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"session {session_id} already has a manifest or does not exist: {exc}")
        finally:
            conn.close()

    def get_session_manifest(self, session_id: int) -> Optional[dict]:
        conn = self._get_connection()
        row = conn.execute("SELECT manifest_json FROM session_manifests WHERE session_id = ?",
                           (session_id,)).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _snapshot_row(row) -> dict:
        """(id, session_id, created_at, is_test, profile_json) -> dict."""
        return {
            "id": row[0],
            "session_id": row[1],
            "created_at": row[2],
            "is_test": bool(row[3]),
            "profile": json.loads(row[4]),
        }

    def get_session(self, session_id: int) -> Optional[dict]:
        """Get a session by its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        
        if row is None:
            return None
        
        columns = [desc[0] for desc in cursor.description]
        session_data = dict(zip(columns, row))
        
        # Load results for this session
        cursor.execute(
            "SELECT * FROM results WHERE session_id = ?", 
            (session_id,)
        )
        result_rows = cursor.fetchall()
        result_columns = [desc[0] for desc in cursor.description]
        
        hardware_rows = []
        cursor.execute("SELECT * FROM hardware_info WHERE session_id = ?", (session_id,))
        if cursor.fetchone():
            hardware_rows = cursor.fetchall()
        
        session_data['results'] = [dict(zip(result_columns, row)) for row in result_rows]
        session_data['hardware_info'] = hardware_rows
        
        conn.close()
        
        return session_data
    
    def get_sessions(self, include_test: bool = False) -> list:
        """Get all sessions with their results and hardware info.

        By default excludes test data (is_test=1). Set include_test=True to show all.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        where_clause = "WHERE is_test = 0" if not include_test else ""
        # Get all sessions
        cursor.execute(f"SELECT id, name, description, created_at, is_test, is_manual FROM sessions {where_clause}")
        sessions = []

        for row in cursor.fetchall():
            session_id = row[0]

            # Load results
            r_where = "AND is_test = 0" if not include_test else ""
            cursor.execute(
                f"SELECT benchmark_name, benchmark_type, score, unit, passed, benchmark_version, is_test, is_manual FROM results WHERE session_id = ? {r_where}",
                (session_id,)
            )
            result_rows = cursor.fetchall()

            sessions.append({
                'id': session_id,
                'name': row[1],
                'description': row[2],
                'created_at': row[3],
                'is_test': bool(row[4]),
                'is_manual': bool(row[5]),
                'results': [dict(zip(['benchmark_name', 'benchmark_type', 'score', 'unit', 'passed', 'benchmark_version', 'is_test', 'is_manual'], r)) for r in result_rows]
            })

            # Load hardware info
            cursor.execute("SELECT cpu_brand, cpu_model, core_count, ram_size_gb FROM hardware_info WHERE session_id = ?", (session_id,))
            if row := cursor.fetchone():
                sessions[-1]['hardware'] = {
                    'cpu_brand': row[0] or None,
                    'cpu_model': row[1] or None,
                    'core_count': row[2],
                    'ram_size': row[3]
                }

        conn.close()

        return sessions

    def get_all_results(self, include_test: bool = False) -> list:
        """Get all results across all sessions.

        By default excludes test data (is_test=1). Set include_test=True to show all.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        test_filter = "WHERE s.is_test = 0 AND r.is_test = 0" if not include_test else ""

        cursor.execute(f"""
            SELECT s.id, s.name, r.benchmark_name, r.benchmark_type, r.score,
                   r.unit, r.passed, r.benchmark_version, s.created_at,
                   COALESCE(s.is_test, 0) as is_test,
                   COALESCE(s.is_manual, 0) as is_manual,
                   COALESCE(r.is_test, 0) as r_is_test,
                   COALESCE(r.is_manual, 0) as r_is_manual
            FROM results r
            JOIN sessions s ON r.session_id = s.id
            {test_filter}
            ORDER BY s.created_at DESC, r.timestamp DESC
        """)

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        return [dict(zip(columns, row)) for row in rows]
    
    def close(self):
        """Close the database connection."""
        conn = self._get_connection()
        conn.close()


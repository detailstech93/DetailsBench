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

            # Run profiles (Phase 4B) - user-saved run settings; built-ins live in app/profiles.py
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS run_profiles (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    profile_json TEXT NOT NULL
                )
            """)

            # Extra numbers of one run (Phase 6), e.g. 1% low FPS of a frame capture.
            # Only finite numbers; a run that is not completed gets none.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS result_metrics (
                    result_id INTEGER NOT NULL REFERENCES results(id),
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT,
                    PRIMARY KEY (result_id, name)
                )
            """)

            # Values over time of one run, for charts (stress tests: work per second, telemetry readings).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS result_series (
                    result_id INTEGER NOT NULL REFERENCES results(id),
                    name TEXT NOT NULL,
                    unit TEXT,
                    points_json TEXT NOT NULL,
                    PRIMARY KEY (result_id, name)
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
                                ("run_index", "INTEGER"), ("note", "TEXT"),
                                # how long the run took (owner, 2026-10-20: the summary report shows each test's time)
                                ("duration_seconds", "REAL")):
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
                      run_index: Optional[int] = None, note: Optional[str] = None,
                      duration_seconds: Optional[float] = None) -> int:
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
                                   status, is_warmup, run_index, note, duration_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, benchmark_name, benchmark_type, score, unit,
             status == "completed", benchmark_version or None, is_test, is_manual,
             status, is_warmup, run_index, note, duration_seconds)
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
                   COALESCE(r.is_manual, 0) AS is_manual, r.duration_seconds
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

    def search_sessions(self, text: Optional[str] = None, benchmark_name: Optional[str] = None,
                        date_from: Optional[str] = None, date_to: Optional[str] = None,
                        include_test: bool = False) -> list:
        """Sessions matching all given filters, newest first.

        text: substring of name or description (case-insensitive, % and _ literal).
        benchmark_name: session has at least one result for it.
        date_from / date_to: ISO dates (YYYY-MM-DD), both inclusive.
        Each row: id, name, description, created_at, is_test, is_manual,
        benchmarks (sorted names), run_count, has_manifest.
        """
        where, params = [], []
        if text:
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(s.name LIKE ? ESCAPE '\\' OR COALESCE(s.description, '') LIKE ? ESCAPE '\\')")
            params += [f"%{escaped}%", f"%{escaped}%"]
        if benchmark_name:
            # A suite id also matches its per-major entries: "geekbench" -> geekbench_6, geekbench_7
            family = benchmark_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "\\_%"
            where.append("EXISTS (SELECT 1 FROM results r2 WHERE r2.session_id = s.id AND "
                         "(r2.benchmark_name = ? OR r2.benchmark_name LIKE ? ESCAPE '\\'))")
            params += [benchmark_name, family]
        if date_from:
            where.append("substr(s.created_at, 1, 10) >= ?")
            params.append(date_from)
        if date_to:
            where.append("substr(s.created_at, 1, 10) <= ?")
            params.append(date_to)
        if not include_test:
            where.append("COALESCE(s.is_test, 0) = 0")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        conn = self._get_connection()
        rows = conn.execute(f"""
            SELECT s.id, s.name, s.description, s.created_at,
                   COALESCE(s.is_test, 0), COALESCE(s.is_manual, 0),
                   (SELECT GROUP_CONCAT(DISTINCT r.benchmark_name) FROM results r WHERE r.session_id = s.id),
                   (SELECT COUNT(*) FROM results r WHERE r.session_id = s.id),
                   EXISTS (SELECT 1 FROM session_manifests m WHERE m.session_id = s.id)
            FROM sessions s {clause}
            ORDER BY s.created_at DESC, s.id DESC
        """, params).fetchall()
        conn.close()
        return [{"id": r[0], "name": r[1], "description": r[2], "created_at": r[3],
                 "is_test": bool(r[4]), "is_manual": bool(r[5]),
                 "benchmarks": sorted((r[6] or "").split(",")) if r[6] else [],
                 "run_count": r[7], "has_manifest": bool(r[8])} for r in rows]

    def save_run_profile(self, profile) -> None:
        """Insert or replace a user RunProfile; built-in names and invalid profiles are refused."""
        from dataclasses import asdict
        from app.profiles import BUILTIN_PROFILES, validate_profile
        problems = validate_profile(profile)
        if problems:
            raise ValueError("; ".join(problems))
        if profile.name.strip().lower() in {p.name.lower() for p in BUILTIN_PROFILES}:
            raise ValueError(f"{profile.name!r} is a built-in profile name")
        data = asdict(profile)
        data["builtin"] = False
        conn = self._get_connection()
        conn.execute("INSERT OR REPLACE INTO run_profiles (name, created_at, profile_json) VALUES (?, ?, ?)",
                     (profile.name.strip(), datetime.now().isoformat(), json.dumps(data, sort_keys=True)))
        conn.commit()
        conn.close()

    def list_run_profiles(self) -> list:
        """Built-in profiles first, then saved ones by name."""
        from app.profiles import BUILTIN_PROFILES, RunProfile
        conn = self._get_connection()
        rows = conn.execute("SELECT profile_json FROM run_profiles ORDER BY name COLLATE NOCASE").fetchall()
        conn.close()
        saved = []
        for (text,) in rows:
            try:
                saved.append(RunProfile(**json.loads(text)))
            except (TypeError, ValueError):
                logger.warning("Skipping unreadable run profile row")
        return list(BUILTIN_PROFILES) + saved

    def delete_run_profile(self, name: str) -> bool:
        """Delete a saved (never built-in) profile; True if one was removed."""
        conn = self._get_connection()
        cur = conn.execute("DELETE FROM run_profiles WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    def save_result_metrics(self, result_id: int, metrics: dict) -> int:
        """Store {name: (value, unit)} for a completed run; returns the number stored.

        Raises ValueError (nothing stored) for a run that is missing or not completed,
        a non-finite value, or a name that is already stored for this run.
        """
        import math
        rows = []
        for name, (value, unit) in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"metric {name!r} is not a finite number: {value!r}")
            rows.append((result_id, str(name), float(value), unit))
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT status, passed FROM results WHERE id = ?", (result_id,)).fetchone()
            if row is None:
                raise ValueError(f"result {result_id} does not exist")
            if (row[0] or ("completed" if row[1] else "failed")) != "completed":
                raise ValueError(f"result {result_id} is {row[0]}; only completed runs get metrics")
            conn.executemany("INSERT INTO result_metrics (result_id, name, value, unit) VALUES (?, ?, ?, ?)", rows)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"metrics not stored for result {result_id}: {exc}")
        finally:
            conn.close()
        return len(rows)

    def save_result_series(self, result_id: int, series: dict) -> int:
        """Store {name: (unit, [(t, value)])} for a completed run; finite points only. Returns how many series."""
        import math
        rows = []
        for name, (unit, points) in series.items():
            clean = [[float(t), float(v)] for t, v in points
                     if all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                            for x in (t, v))]
            if clean:
                rows.append((result_id, str(name), unit, json.dumps(clean, separators=(",", ":"))))
        if not rows:
            return 0
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT status FROM results WHERE id = ?", (result_id,)).fetchone()
            if row is None or row[0] != "completed":
                raise ValueError(f"result {result_id} is not a completed run; no series stored")
            conn.executemany("INSERT INTO result_series (result_id, name, unit, points_json) VALUES (?, ?, ?, ?)",
                             rows)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"series not stored for result {result_id}: {exc}")
        finally:
            conn.close()
        return len(rows)

    def get_result_series(self, result_id: int) -> dict:
        """{name: {"unit": unit, "points": [[t, value], ...]}}; {} when the run has none."""
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT name, unit, points_json FROM result_series WHERE result_id = ? ORDER BY name",
                                (result_id,)).fetchall()
        finally:
            conn.close()
        return {name: {"unit": unit, "points": json.loads(points)} for name, unit, points in rows}

    def get_result_metrics(self, result_id: int) -> dict:
        """{name: (value, unit)}; {} when the run has none."""
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT name, value, unit FROM result_metrics WHERE result_id = ? ORDER BY name",
                                (result_id,)).fetchall()
        finally:
            conn.close()
        return {name: (value, unit) for name, value, unit in rows}

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


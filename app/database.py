"""SQLite database models for PC Benchmark Manager."""

import sqlite3
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


DATABASE_PATH = "benchmark_manager.db"


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
                      is_test: bool = False, is_manual: bool = False) -> int:
        """Add a result to the specified session."""
        conn = self._get_connection()
        cursor = conn.cursor()

        if benchmark_version:
            cursor.execute(
                """INSERT INTO results (session_id, benchmark_name, benchmark_type,
                                       score, unit, passed, benchmark_version,
                                       is_test, is_manual)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, benchmark_name, benchmark_type, score, unit, passed,
                 benchmark_version, is_test, is_manual)
            )
        else:
            cursor.execute(
                """INSERT INTO results (session_id, benchmark_name, benchmark_type,
                                       score, unit, passed, is_test, is_manual)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, benchmark_name, benchmark_type, score, unit, passed,
                 is_test, is_manual)
            )

        result_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return result_id
    
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


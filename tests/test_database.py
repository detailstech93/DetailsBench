"""Tests for database module."""

import pytest


@pytest.fixture(scope="module")
def system_info():
    """Mock system information."""
    return {
        'cpu_brand': 'Intel Core i7-12700K',
        'core_count': 16,
        'ram_size_gb': 32,
        'gpu_name': 'NVIDIA GeForce RTX 4080'
    }


class TestDatabase:
    """Test database functionality."""
    
    def test_database_init(self):
        """Test that Database initializes correctly with a real db file."""
        import tempfile
        import os
        
        # Create a temporary directory for testing
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, 'test.db')
        
        try:
            from app.database import Database
            
            db = Database(db_path)
            
            # Verify database was created and tables exist
            assert os.path.exists(db_path)
            sessions = db.get_sessions()
            assert isinstance(sessions, list)
            
        finally:
            # Cleanup
            try:
                os.remove(db_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass


class TestSystemInfo:
    """Test system information gathering."""
    
    @pytest.fixture
    def mock_system_info(self):
        return {
            'cpu_brand': 'Intel Core i7-12700K',
            'core_count': 16,
            'ram_size_gb': 32,
            'gpu_name': 'NVIDIA GeForce RTX 4080'
        }
    
    def test_system_info_fields(self, mock_system_info):
        """Test that all required fields are present."""
        mock = mock_system_info
        
        assert mock.get('cpu_brand') is not None
        assert mock.get('core_count') is not None
        assert mock.get('ram_size_gb') is not None
        assert mock.get('gpu_name') is not None


class TestApplication:
    """Test application startup and basic functionality."""
    
    def test_import_modules(self):
        """Test that all required modules can be imported."""
        from app.database import Database
        from app.system_info import get_cpu_brand, get_system_summary
        
        assert True
    
    @pytest.mark.skip(reason="Full integration tests require running application")
    def test_ui_startup(self, tmp_path):
        """Test that UI can start without errors."""
        # Would test PySide6 application startup here
        pass


class TestDatabaseOperations:
    """Test actual database operations."""
    
    def test_session_creation_and_retrieval(self, tmp_path):
        """Test creating sessions and retrieving them."""
        import tempfile
        import os
        from app.database import Database
        
        # Create a temporary directory for testing
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, 'test.db')
        
        try:
            db = Database(db_path)
            
            # Test creating a session
            session_id = db.create_session("Test Session", "Testing database operations")
            assert session_id > 0
            
            # Test retrieving the session
            session = db.get_session(session_id)
            assert session is not None
            assert session['name'] == "Test Session"
            assert session['description'] == "Testing database operations"
            
        finally:
            # Cleanup
            try:
                os.remove(db_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass
    
    def test_result_creation_and_retrieval(self, tmp_path):
        """Test creating results and retrieving them."""
        import tempfile
        import os
        from app.database import Database
        
        # Create a temporary directory for testing
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, 'test.db')
        
        try:
            db = Database(db_path)
            
            # Create a session first
            session_id = db.create_session("Test Session")
            
            # Test creating results
            result_id1 = db.create_result(session_id, "Geekbench", "CPU", 2500.5, "points", True)
            result_id2 = db.create_result(session_id, "Cinebench", "Render", 850.3, "points", True)
            
            assert result_id1 > 0
            assert result_id2 > 0
            
            # Test retrieving the session with results
            session = db.get_session(session_id)
            assert session is not None
            assert len(session['results']) == 2
            
            # Verify result data
            results = {r['benchmark_name']: r for r in session['results']}
            assert 'Geekbench' in results
            assert results['Geekbench']['score'] == 2500.5
            assert results['Cinebench']['score'] == 850.3
            
        finally:
            # Cleanup
            try:
                os.remove(db_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def test_migration_from_old_schema(self, tmp_path):
        """Test that opening an old-schema database (without benchmark_version) is additive and preserves data."""
        import sqlite3
        from app.database import Database
        
        db_path = str(tmp_path / "old_schema.db")
        
        # 1. Create an old-schema database without benchmark_version
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                benchmark_name TEXT NOT NULL,
                benchmark_type TEXT NOT NULL,
                score REAL,
                unit TEXT DEFAULT 'points',
                passed BOOLEAN DEFAULT 1,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE hardware_info (
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
        cursor.execute(
            "INSERT INTO sessions (name, description, created_at) VALUES (?, ?, ?)",
            ("Legacy Session", "Recorded before benchmark_version was added", "2026-10-10T12:00:00")
        )
        old_session_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO results (session_id, benchmark_name, benchmark_type, score, unit, passed) VALUES (?, ?, ?, ?, ?, ?)",
            (old_session_id, "Geekbench", "CPU", 1500.0, "points", 1)
        )
        conn.commit()
        conn.close()
        
        # 2. Open with new Database code
        db = Database(db_path)
        
        # 3. Confirm old session and result are readable
        session = db.get_session(old_session_id)
        assert session is not None
        assert session["name"] == "Legacy Session"
        assert len(session["results"]) == 1
        assert session["results"][0]["benchmark_name"] == "Geekbench"
        assert session["results"][0]["score"] == 1500.0
        assert session["results"][0]["benchmark_version"] is None
        
        # 4. Insert new result with benchmark_version
        new_result_id = db.create_result(
            old_session_id, "geekbench_7", "CPU", 2800.0, "points", True, benchmark_version="7.1.0.0"
        )
        assert new_result_id > 0
        
        # 5. Confirm both old and new results are readable and accurate
        all_results = db.get_all_results()
        assert len(all_results) == 2
        results_by_name = {r["benchmark_name"]: r for r in all_results}
        assert results_by_name["Geekbench"]["score"] == 1500.0
        assert results_by_name["Geekbench"]["benchmark_version"] is None
        assert results_by_name["geekbench_7"]["score"] == 2800.0
        assert results_by_name["geekbench_7"]["benchmark_version"] == "7.1.0.0"

    def test_existing_database_compatibility(self, tmp_path):
        """Reopening an existing on-disk database (temp file) keeps its data.

        Never opens the real databases in the project root.
        """
        from app.database import Database

        db_file = str(tmp_path / 'existing.db')
        first = Database(db_file)
        session_id = first.create_session('Earlier run')
        first.create_result(session_id, 'cinebench', 'CPU', 1234.0, 'points', True)

        reopened = Database(db_file)
        sessions = reopened.get_sessions()
        assert [s['id'] for s in sessions] == [session_id]
        all_res = reopened.get_all_results()
        assert len(all_res) == 1
        assert all_res[0]['benchmark_name'] == 'cinebench'
        assert all_res[0]['score'] == 1234.0



"""Integration test: session and result round-trip through a temporary database."""

from app.database import Database


def test_session_and_results_round_trip(tmp_path):
    """Create a session with two results in a temp DB and read them back."""
    db = Database(str(tmp_path / 'integration.db'))
    session_id = db.create_session('Integration Test', 'Testing full application flow',
                                   is_test=True)
    db.create_result(session_id, 'Geekbench', 'CPU', 2500.5, 'points', True, is_test=True)
    db.create_result(session_id, 'Cinebench', 'Render', 850.3, 'points', True, is_test=True)

    session = db.get_session(session_id)
    assert session['name'] == 'Integration Test'
    assert len(session['results']) == 2

    results = {r['benchmark_name']: r for r in session['results']}
    assert results['Geekbench']['score'] == 2500.5
    assert results['Cinebench']['score'] == 850.3

    # Test rows are excluded by default and included on request
    assert db.get_sessions() == []
    assert [s['id'] for s in db.get_sessions(include_test=True)] == [session_id]

#!/usr/bin/env python
"""Integration test for the full application."""

import sys
import os
sys.path.insert(0, '.')

from app.system_info import get_system_summary
from app.database import Database

def main():
    print('=== Application Startup Test ===')
    
    # Test system information gathering
    print('System Summary:')
    system_info = get_system_summary()
    for key, value in system_info.items():
        print(f'  {key}: {value}')
    
    # Test database operations
    print('\nDatabase Test:')
    db = Database('test_benchmark.db')
    session_id = db.create_session('Integration Test', 'Testing full application flow')
    result_id1 = db.create_result(session_id, 'Geekbench', 'CPU', 2500.5, 'points', True)
    result_id2 = db.create_result(session_id, 'Cinebench', 'Render', 850.3, 'points', True)
    
    sessions = db.get_sessions()
    print(f'Total sessions: {len(sessions)}')
    
    session = db.get_session(session_id)
    print(f'Session name: {session["name"]}')
    print(f'Results count: {len(session["results"])}')
    
    # Verify results data
    results = {r['benchmark_name']: r for r in session['results']}
    assert 'Geekbench' in results, "Geekbench result not found"
    assert results['Geekbench']['score'] == 2500.5, "Geekbench score mismatch"
    assert results['Cinebench']['score'] == 850.3, "Cinebench score mismatch"
    
    print('\n=== All Tests Passed ===')

if __name__ == '__main__':
    main()
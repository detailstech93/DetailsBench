#!/usr/bin/env python
"""
Compare two sessions from the production database (same rules as the Compare Results tab).

Usage:
    python scripts/verify_compare.py            # the two newest non-test sessions (older = A)
    python scripts/verify_compare.py 3 7        # session 3 as A (baseline), 7 as B
    python scripts/verify_compare.py --include-test
"""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.compare import compare_sessions
from app.database import Database
from app.paths import resolve_database_path


def _fmt(value, digits=2):
    return "-" if value is None else f"{value:,.{digits}f}"


def main(argv) -> int:
    include_test = "--include-test" in argv
    ids = [int(a) for a in argv if a.isdigit()]
    path, _ = resolve_database_path()
    db = Database(str(path))
    sessions = {s["id"]: s for s in db.search_sessions(include_test=include_test)}
    if len(ids) != 2:
        newest = sorted(sessions, reverse=True)[:2]
        if len(newest) < 2:
            print(f"Database: {path}\nNeed at least two sessions to compare (found {len(sessions)}).")
            return 0
        ids = [newest[1], newest[0]]
    missing = [i for i in ids if i not in sessions]
    if missing:
        print(f"Unknown session(s): {missing} (test sessions need --include-test)")
        return 1
    a, b = (sessions[i] for i in ids)
    print(f"Database: {path}\nA = #{a['id']} {a['name']}   B = #{b['id']} {b['name']}\n")
    rows = compare_sessions(db.get_runs(a["id"], include_test=True), db.get_runs(b["id"], include_test=True),
                            db.get_session_manifest(a["id"]), db.get_session_manifest(b["id"]),
                            a["is_test"], b["is_test"])
    for row in rows:
        pct = "-" if row["delta_percent"] is None else f"{row['delta_percent']:+.2f} %"
        print(f"{row['benchmark']:<14} A={_fmt(row['mean_a'])}  B={_fmt(row['mean_b'])}  "
              f"delta={pct}  -> {row['status']}")
        for issue in row["issues"]:
            print(f"    {issue['level']:<5} {issue['code']}: {issue['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

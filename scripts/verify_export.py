#!/usr/bin/env python
"""
Export one session of the production database as HTML, JSON and CSV, and
optionally back up the database. Read-only on the database itself.

Usage:
    python scripts/verify_export.py                    # newest non-test session
    python scripts/verify_export.py 3 --include-test   # session 3 (test sessions need --include-test)
    python scripts/verify_export.py --out C:\\temp\\x   # output folder (default: %TEMP%\\pcbm_export_check)
    python scripts/verify_export.py --backup           # also write a backup into the backups folder
"""

import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.backup import backup_database, verify_backup
from app.database import Database
from app.export import build_session_export, default_filename, write_export
from app.paths import resolve_database_path


def main(argv) -> int:
    include_test = "--include-test" in argv
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path(tempfile.gettempdir()) / "pcbm_export_check"
    ids = [int(a) for a in argv if a.isdigit()]
    path, _ = resolve_database_path()
    db = Database(str(path))
    print(f"Database: {path}")
    sessions = db.search_sessions(include_test=include_test)
    if ids:
        session = next((s for s in sessions if s["id"] == ids[0]), None)
        if session is None:
            print(f"Unknown session {ids[0]} (test sessions need --include-test)")
            return 1
    elif sessions:
        session = sessions[0]
    else:
        print("No sessions to export (test sessions need --include-test).")
        session = None
    if session:
        export = build_session_export(db, session["id"])
        out.mkdir(parents=True, exist_ok=True)
        for fmt in ("html", "json", "csv"):
            target = write_export(export, out / default_filename(export, fmt), fmt)
            print(f"{fmt:<4} {target}  ({target.stat().st_size} bytes)")
        data = json.loads((out / default_filename(export, "json")).read_text(encoding="utf-8"))
        rows = list(csv.reader(io.StringIO(
            (out / default_filename(export, "csv")).read_text(encoding="utf-8-sig"), newline="")))
        html = (out / default_filename(export, "html")).read_text(encoding="utf-8")
        print(f"session #{data['session']['id']} {data['session']['name']!r}: runs {len(data['runs'])}, "
              f"csv rows {len(rows) - 1}, benchmarks {sorted(data['summaries'])}")
        print(f"html has <script: {'<script' in html}, external links: {'https://' in html or 'http://' in html}")
    if "--backup" in argv:
        info = backup_database(path)
        check = verify_backup(info["path"])
        print(f"backup {info['path']}  integrity {info['integrity']}  counts {info['counts']}  "
              f"sha256 ok {check['sha256_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

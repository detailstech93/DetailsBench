"""This PC's index of the results library: a SQLite file next to the database, built from the library's files.

Fast to search and compare, and still there when the NAS or cloud folder is offline. It is only a copy: it can be
rebuilt from the folder at any time. Each file's size and change time are kept, so a refresh reads only new or
changed files. A package of a newer format, or one whose hash does not match, is not taken and is listed.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.library.package import check
from app.units import canonical
from app.library.store import open_library

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    package_id TEXT PRIMARY KEY, path TEXT NOT NULL, unit_id TEXT, session_id INTEGER, test_id TEXT,
    part TEXT, part_key TEXT, part_name TEXT, variant TEXT, ranked INTEGER, mean REAL, runs INTEGER,
    created_at TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (path TEXT PRIMARY KEY, package_id TEXT NOT NULL, action TEXT NOT NULL,
    at TEXT, reason TEXT, by TEXT);
CREATE TABLE IF NOT EXISTS names (path TEXT PRIMARY KEY, part_key TEXT NOT NULL, name TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS seen (path TEXT PRIMARY KEY, size INTEGER, mtime REAL);
CREATE TABLE IF NOT EXISTS info (key TEXT PRIMARY KEY, value TEXT);
"""


class LibraryIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        """One connection per use, committed and closed after it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def refresh(self, library: Path) -> dict:
        """Reads the library's new and changed files. {"added", "events", "skipped": [why]}."""
        library = open_library(library)
        added, events, skipped = 0, 0, []
        with self._db() as db:
            seen = {p: (s, m) for p, s, m in db.execute("SELECT path, size, mtime FROM seen")}
            files = (sorted(library.glob("packages/*/*/*.json")) + sorted(library.glob("events/*.json"))
                     + sorted(library.glob("names/*.json")))
            for f in files:
                rel = f.relative_to(library).as_posix()
                try:
                    st = f.stat()
                except OSError:
                    continue
                if seen.get(rel) == (st.st_size, st.st_mtime):
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    skipped.append(f"{rel}: cannot be read")
                    continue
                if rel.startswith("names/"):
                    if not isinstance(data, dict) or not data.get("part_key"):
                        skipped.append(f"{rel}: not a name")
                        continue
                    db.execute("INSERT OR REPLACE INTO names VALUES (?, ?, ?, ?)",
                               (rel, data["part_key"], data.get("name"), data.get("at")))
                elif rel.startswith("events/"):
                    if not isinstance(data, dict) or data.get("action") not in ("delete", "restore"):
                        skipped.append(f"{rel}: not a delete or restore")
                        continue
                    db.execute("INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?, ?)",
                               (rel, data.get("package_id"), data["action"], data.get("at"), data.get("reason"),
                                data.get("by")))
                    events += 1
                else:
                    why = check(data) if isinstance(data, dict) else "not a package"
                    if why:
                        skipped.append(f"{rel}: {why}")
                        continue
                    a, r = data["attribution"], data["result"]
                    db.execute("INSERT OR REPLACE INTO packages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (data["package_id"], rel, data["pc"]["unit_id"], data["session"]["id"],
                                data["test"]["id"], a.get("part"), a.get("part_key"), a.get("part_name"),
                                a.get("variant"), int(bool(a.get("ranked"))), r.get("mean"), r.get("runs"),
                                data.get("created_at"), json.dumps(data, ensure_ascii=False)))
                    added += 1
                db.execute("INSERT OR REPLACE INTO seen VALUES (?, ?, ?)", (rel, st.st_size, st.st_mtime))
            db.execute("INSERT OR REPLACE INTO info VALUES ('library', ?)", (str(library),))
            db.execute("INSERT OR REPLACE INTO info VALUES ('refreshed_at', ?)",
                       (datetime.now().isoformat(timespec="seconds"),))
        return {"added": added, "events": events, "skipped": skipped}

    def rebuild(self, library: Path) -> dict:
        """Forgets what was read and reads the whole folder again (the folder is the source)."""
        with self._db() as db:
            for table in ("packages", "events", "names", "seen"):
                db.execute(f"DELETE FROM {table}")
        return self.refresh(library)

    def deleted(self) -> dict:
        """{package id: the delete event} for each package whose newest event is a delete."""
        out = {}
        with self._db() as db:
            for pid, action, at, reason, by, path in db.execute(
                    "SELECT package_id, action, at, reason, by, path FROM events ORDER BY path"):
                out[pid] = {"action": action, "at": at, "reason": reason, "by": by}
        return {pid: e for pid, e in out.items() if e["action"] == "delete"}

    def names(self) -> dict:
        """{part key: the owner's newest name} (an empty newest name takes it back)."""
        out = {}
        with self._db() as db:
            for key, name in db.execute("SELECT part_key, name FROM names ORDER BY path"):
                out[key] = name
        return {k: v for k, v in out.items() if v}

    def results(self, include_deleted: bool = False, part: Optional[str] = None) -> List[dict]:
        gone = self.deleted()
        query, args = "SELECT json FROM packages", []
        if part:
            query, args = query + " WHERE part = ?", [part]
        out = []
        with self._db() as db:
            for (text,) in db.execute(query + " ORDER BY created_at", args):
                pkg = json.loads(text)
                pkg["deleted"] = gone.get(pkg["package_id"])
                pkg["test"]["unit"] = canonical(pkg["test"].get("unit"))     # "pts" written before 2026-10-25
                if include_deleted or not pkg["deleted"]:
                    out.append(pkg)
        return out

    def info(self) -> dict:
        with self._db() as db:
            return dict(db.execute("SELECT key, value FROM info"))

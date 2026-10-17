"""Database backups: a consistent copy plus a SHA-256 sidecar, never overwriting.

backup_database() copies the live database with the SQLite backup API (source
opened read-only, integrity-checked before the file gets its final name, see
app.paths.copy_database), then writes "<backup>.sha256". Existing backups are
never overwritten or deleted; a name clash gets a numeric suffix.

Restoring is deliberately not automated: point "database_path" in
settings.json at a backup file (the original stays untouched).
"""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.paths import copy_database

BACKUP_PREFIX = "benchmark_manager_"
BACKUP_SUFFIX = ".db"


def default_backup_dir(db_path: Path) -> Path:
    return Path(db_path).parent / "backups"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _free_name(backup_dir: Path, stamp: str) -> Path:
    target = backup_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    n = 2
    while target.exists() or target.with_name(target.name + ".sha256").exists():
        target = backup_dir / f"{BACKUP_PREFIX}{stamp}_{n}{BACKUP_SUFFIX}"
        n += 1
    return target


def _inspect(path: Path) -> dict:
    """Integrity check and row counts, opened read-only."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("sessions", "results") if t in tables}
    finally:
        conn.close()
    return {"integrity": integrity, "counts": counts}


def backup_database(db_path: Path, backup_dir: Optional[Path] = None,
                    now: Optional[datetime] = None) -> dict:
    """Copy db_path into backup_dir; returns path, sha256, size, integrity and row counts."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    backup_dir = default_backup_dir(db_path) if backup_dir is None else Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    target = _free_name(backup_dir, stamp)
    copy_database(db_path, target)
    digest = sha256_file(target)
    target.with_name(target.name + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    info = _inspect(target)
    return {"path": target, "sha256": digest, "size_bytes": target.stat().st_size, **info}


def list_backups(backup_dir: Path) -> List[Path]:
    """Backup files in backup_dir, oldest first (by name, which starts with the timestamp)."""
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []
    return sorted(p for p in backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}") if p.is_file())


def verify_backup(path: Path) -> dict:
    """Recompute the hash against the sidecar and re-run the integrity check.

    sha256_ok is None when the sidecar is missing or unreadable (unknown, not failed).
    """
    path = Path(path)
    actual = sha256_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    expected = None
    try:
        expected = sidecar.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        pass
    return {"path": path, "sha256": actual,
            "sha256_ok": None if expected is None else expected == actual, **_inspect(path)}

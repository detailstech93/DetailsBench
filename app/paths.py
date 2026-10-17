"""Location of the production database and the settings file.

The production database lives in a per-user data folder, never in the
project folder, so its location does not depend on the working directory:
  Windows: %LOCALAPPDATA%\\PCBenchmarkManager\\benchmark_manager.db
  other:   $XDG_DATA_HOME/PCBenchmarkManager (default ~/.local/share/...)

settings.json in that folder may set "database_path" to use another file.

The first time the new location is used, an existing database from the
old location (benchmark_manager.db in the project root) is copied there
with the SQLite backup API. The source is opened read-only and is never
modified or deleted.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

APP_DIR_NAME = "PCBenchmarkManager"
DB_FILENAME = "benchmark_manager.db"
SETTINGS_FILENAME = "settings.json"
LEGACY_DB_PATH = Path(__file__).resolve().parent.parent / DB_FILENAME


def get_data_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """Return the per-user data folder (not created here)."""
    env = os.environ if env is None else env
    base = env.get("LOCALAPPDATA") or env.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def load_settings(data_dir: Path) -> Tuple[dict, List[str]]:
    """Read settings.json; a missing or malformed file yields {} plus a note."""
    path = data_dir / SETTINGS_FILENAME
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"settings file ignored ({path}): {exc}"]
    if not isinstance(data, dict):
        return {}, [f"settings file ignored ({path}): top level is not an object"]
    return data, []


def save_setting(data_dir: Path, key: str, value) -> None:
    """Set one key in settings.json, keeping every other key. A malformed file is
    never overwritten (ValueError); the new file is written under a temporary name first."""
    path = data_dir / SETTINGS_FILENAME
    data, notes = load_settings(data_dir)
    if notes:
        raise ValueError(notes[0])
    data[key] = value
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def copy_database(source: Path, target: Path) -> None:
    """Copy a SQLite database with the backup API, source opened read-only.

    Writes to a temporary file first and renames it only after an integrity
    check, so an interrupted copy never leaves a half-written target.
    """
    partial = target.with_name(target.name + ".partial")
    if partial.exists():
        partial.unlink()   # leftover from an interrupted copy of ours
    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(partial))
        try:
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if check != "ok":
        partial.unlink()
        raise RuntimeError(f"integrity check of copied database failed: {check}")
    partial.replace(target)


def resolve_database_path(
    data_dir: Optional[Path] = None,
    legacy_path: Path = LEGACY_DB_PATH,
) -> Tuple[Path, List[str]]:
    """Return (database path to open, notes describing what was decided).

    Creates the parent folder when needed. Copies the legacy database once,
    only when the target does not exist yet and the legacy file does.
    A failed copy raises instead of silently starting with an empty database.
    """
    data_dir = get_data_dir() if data_dir is None else data_dir
    settings, notes = load_settings(data_dir)

    custom = settings.get("database_path")
    if isinstance(custom, str) and custom.strip():
        target = Path(os.path.expandvars(os.path.expanduser(custom.strip())))
        notes.append(f"database_path from settings: {target}")
    else:
        if custom is not None:
            notes.append("settings database_path ignored: not a non-empty string")
        target = data_dir / DB_FILENAME

    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists() and legacy_path.is_file():
        copy_database(legacy_path, target)
        notes.append(f"copied legacy database {legacy_path} -> {target} (original kept)")

    for note in notes:
        logger.info(note)
    return target, notes

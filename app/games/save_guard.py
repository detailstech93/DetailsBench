"""Save folder guard (owner's plan step 3, 2026-11-04): a game's save folder is copied before a benchmark run and
its files are put back after, so a benchmark save or an autosave never takes the place of the owner's own.

Nothing is deleted: a file the run changed is kept in the run folder before the copy goes back over it, and files
the run added are left where they are and named. A run cut off before the files went back (app closed, crash) is
put back at the app's next start, unless the game is open then; what the folder held at that moment is kept too.
"""

import filecmp
import json
import shutil
from pathlib import Path
from typing import Callable, Dict, List

SAVE_FOLDERS_KEY = "game_save_folders"      # settings.json: {app_id: the save folder the owner picked}
PENDING_FILE = "pending_save_restore.json"  # data dir: {app_id: {"folder", "backup"}} until the files are back
BACKUP_NAME = "game_saves_backup"           # in the run folder: the save folder as it was before the run
CHANGED_NAME = "game_saves_after_run"       # in the run folder: files the run changed, kept before putting back
LIMIT_BYTES = 2 * 1024 ** 3                 # a bigger folder is not a save folder (or not one to copy each run)


def _files(folder: Path) -> Dict[str, Path]:
    return {p.relative_to(folder).as_posix(): p for p in folder.rglob("*") if p.is_file()}


def _read_pending(data_dir: Path) -> Dict[str, dict]:
    try:
        data = json.loads((Path(data_dir) / PENDING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _write_pending(data_dir: Path, pending: Dict[str, dict]) -> None:
    path = Path(data_dir) / PENDING_FILE
    if not pending:
        if path.is_file():
            path.unlink()                   # our own marker
        return
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(pending, indent=1), encoding="utf-8")
    partial.replace(path)


def put_back(folder: Path, backup: Path, keep: Path) -> str:
    """Every file of the copy goes back to its place; one that differs now is first copied into keep."""
    now = _files(folder)
    changed = 0
    for rel, src in _files(backup).items():
        dst = folder / rel
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            continue
        if dst.is_file():
            (keep / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, keep / rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)           # dated now, so Steam Cloud sees a newer local file
        changed += 1
    added = sorted(set(now) - set(_files(backup)))
    text = (f"saves: {changed} file(s) put back" + (f" (the run's versions kept in {keep})" if changed else "")
            if changed else "saves: unchanged by the run")
    if added:
        text += f"; {len(added)} new file(s) from the run left in the save folder: {', '.join(added[:5])}"
    return text


class SaveGuard:
    def __init__(self, app_id, folder, run_dir: Path, data_dir: Path):
        self.app_id, self.folder = str(app_id), Path(folder)
        self.backup = Path(run_dir) / BACKUP_NAME
        self.data_dir = Path(data_dir)
        self.note = ""

    def begin(self) -> None:
        """Copies the save folder (OSError/ValueError: the run must not start)."""
        if not self.folder.is_dir():
            raise ValueError(f"the save folder {self.folder} is not there")
        size = sum(p.stat().st_size for p in _files(self.folder).values())
        if size > LIMIT_BYTES:
            raise ValueError(f"the save folder {self.folder} holds {size / 1024 ** 3:.1f} GB; pick the folder "
                             "with the saves only")
        shutil.copytree(self.folder, self.backup)
        pending = _read_pending(self.data_dir)
        pending[self.app_id] = {"folder": str(self.folder), "backup": str(self.backup)}
        _write_pending(self.data_dir, pending)

    def finish(self) -> str:
        """After the game is closed: the saves go back. Returns one line for the run's message."""
        try:
            self.note = put_back(self.folder, self.backup, self.backup.parent / CHANGED_NAME)
        except OSError as e:
            self.note = f"saves: could not be put back ({e}); the copy is {self.backup}"
            return self.note                # stays pending: tried again at the next start
        pending = _read_pending(self.data_dir)
        pending.pop(self.app_id, None)
        _write_pending(self.data_dir, pending)
        return self.note


def recover_saves(data_dir: Path, running: Callable = lambda app_id: False) -> List[str]:
    """Puts back the saves of every run cut off before finish(), except of a game open now."""
    pending = _read_pending(data_dir)
    lines = []
    for app_id, entry in list(pending.items()):
        backup, folder = Path(str(entry.get("backup"))), Path(str(entry.get("folder")))
        if not backup.is_dir():
            lines.append(f"Steam app {app_id}: the copy of its saves is gone ({backup}); nothing put back")
            pending.pop(app_id)
            continue
        if running(app_id):
            continue
        try:
            lines.append(f"Steam app {app_id}: a run was cut off; "
                         + put_back(folder, backup, backup.parent / "game_saves_before_recovery"))
        except OSError as e:
            lines.append(f"Steam app {app_id}: saves could not be put back ({e}); the copy is {backup}")
            continue
        pending.pop(app_id)
    _write_pending(data_dir, pending)
    return lines

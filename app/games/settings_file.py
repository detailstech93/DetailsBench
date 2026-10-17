"""Force a game's graphics settings for one benchmark, verify them, then put the user's own file back.

Only games with an adapter (a module that knows the settings file, see ADAPTERS) are supported. The
flow, all on the game's own settings file and only after the user confirmed it:

1. read the file and plan the changes (adapter.plan); values the game would not accept are refused
   with a reason and nothing is written for them;
2. copy the file unchanged into the run folder (the backup), then write the changed file under a
   temporary name and replace the original with it;
3. read it back and compare (verified before the launch);
4. after the game was closed, read it again (the game may rewrite it with what it really used:
   verified after the run), then copy the backup back over it.

Some list settings of Cyberpunk 2077 carry the position of the value in a list the game builds at
runtime. Those positions are learned from the file each time it is read (the user picked the value
once in the game's menu) and kept in game_settings_indices.json in the data folder.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from app.games import borderlands3, cyberpunk2077

INDICES_FILENAME = "game_settings_indices.json"
BACKUP_NAME = "game_settings_backup"      # + the settings file's own extension


def documents_dir(env: Mapping[str, str]) -> Optional[Path]:
    """The user's Documents folder (it may be moved, e.g. to OneDrive); PCBM_DOCUMENTS overrides it in tests."""
    if env.get("PCBM_DOCUMENTS"):
        return Path(env["PCBM_DOCUMENTS"])
    if env is os.environ and os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
                return Path(buf.value)
        except Exception:
            pass
    home = env.get("USERPROFILE") or env.get("HOME")
    return Path(home) / "Documents" if home else None


@dataclass(frozen=True)
class Adapter:
    """module: parse, dump, read_settings, choices, observed_indices, merge_indices, plan, apply, verify,
    APP_ID, LAUNCH_ARGS. base: "localappdata" or "documents"."""
    module: object
    relative_path: str
    base: str = "localappdata"

    def settings_path(self, env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
        env = os.environ if env is None else env
        if self.base == "documents":
            root = documents_dir(env)
        else:
            root = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else None
        return root / self.relative_path if root else None


ADAPTERS: Dict[str, Adapter] = {
    str(cyberpunk2077.APP_ID): Adapter(cyberpunk2077, os.path.join("CD Projekt Red", "Cyberpunk 2077",
                                                                   "UserSettings.json")),
    str(borderlands3.APP_ID): Adapter(borderlands3, os.path.join(
        "My Games", "Borderlands 3", "Saved", "Config", "WindowsNoEditor", "GameUserSettings.ini"), "documents"),
}


def adapter_for(app_id) -> Optional[Adapter]:
    return ADAPTERS.get(str(app_id))


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".pcbm-tmp")
    tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
    os.replace(tmp, path)


def _read_game(adapter: Adapter, path: Path):
    """Parse the game's file; bytes are decoded as they are (line endings kept)."""
    return adapter.module.parse(path.read_bytes().decode("utf-8"))


def _write_game(adapter: Adapter, path: Path, data) -> None:
    tmp = path.with_name(path.name + ".pcbm-tmp")
    tmp.write_bytes(adapter.module.dump(data).encode("utf-8"))
    os.replace(tmp, path)


def load_indices(data_dir: Path) -> Dict[str, Dict[str, Dict[str, int]]]:
    """{app_id: {key: {value: index}}}; a missing or malformed file is empty."""
    try:
        data = _read_json(Path(data_dir) / INDICES_FILENAME)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def learn_indices(data_dir: Path, app_id, adapter: Adapter, data) -> Dict[str, Dict[str, int]]:
    """Merge the positions seen in `data` into the stored ones and return them for this game."""
    every = load_indices(data_dir)
    known = every.get(str(app_id))
    known = known if isinstance(known, dict) else {}
    merged = adapter.module.merge_indices(known, adapter.module.observed_indices(data))
    if merged != known:
        every[str(app_id)] = merged
        try:
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            _write_json(Path(data_dir) / INDICES_FILENAME, every)
        except OSError:
            pass                        # learning is a convenience; the run does not depend on it
    return merged


@dataclass
class SettingsRecord:
    """What was asked, done and seen; stored in the session manifest (run_settings["game_settings"])."""
    file: str
    requested: Dict[str, object]
    before: Dict[str, object] = field(default_factory=dict)       # the user's values, read first
    changed: List[str] = field(default_factory=list)
    refused: Dict[str, str] = field(default_factory=dict)
    mismatch_before_launch: Dict[str, list] = field(default_factory=dict)
    mismatch_after_run: Optional[Dict[str, list]] = None           # None: not read after the run
    restored: Optional[bool] = None
    notes: List[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """Every requested setting was accepted, written and still in place after the run."""
        return (not self.refused and not self.mismatch_before_launch
                and self.mismatch_after_run is not None and not self.mismatch_after_run)

    def to_dict(self) -> dict:
        return {"file": self.file, "requested": dict(self.requested), "before": dict(self.before),
                "changed": list(self.changed), "refused": dict(self.refused),
                "mismatch_before_launch": dict(self.mismatch_before_launch),
                "mismatch_after_run": self.mismatch_after_run, "restored": self.restored,
                "verified": self.verified, "notes": list(self.notes)}


def _mismatches(found: Dict[str, tuple]) -> Dict[str, list]:
    return {k: [expected, actual] for k, (expected, actual) in found.items()}


class ForcedSettings:
    """apply() before the launch, finish() after the game was closed (also after a failure)."""

    def __init__(self, app_id, requested: Dict[str, object], run_dir: Path, data_dir: Path,
                 env: Optional[Mapping[str, str]] = None):
        self.app_id = str(app_id)
        self.adapter = adapter_for(app_id)
        if self.adapter is None:
            raise ValueError(f"no settings adapter for Steam app {app_id}")
        self.path = self.adapter.settings_path(env)
        self.requested = dict(requested)
        self.backup = Path(run_dir) / (BACKUP_NAME + (self.path.suffix if self.path else ""))
        self.data_dir = Path(data_dir)
        self.record = SettingsRecord(str(self.path), self.requested)
        self._written = False

    def apply(self) -> SettingsRecord:
        """Back up and write the file. Raises OSError/ValueError when it cannot be read or written;
        nothing is changed then (the caller does not launch the game)."""
        mod = self.adapter.module
        if self.path is None or not self.path.is_file():
            raise OSError(f"the game's settings file was not found ({self.path}); start the game once first")
        data = _read_game(self.adapter, self.path)
        self.record.before = mod.read_settings(data)
        known = learn_indices(self.data_dir, self.app_id, self.adapter, data)
        changes, refused = mod.plan(data, self.requested, known)
        self.record.refused = dict(refused)
        self.record.changed = [c.key for c in changes]
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, self.backup)
        if changes:
            _write_game(self.adapter, self.path, mod.apply(data, changes))
            self._written = True
        accepted = {k: v for k, v in self.requested.items() if k not in refused}
        self.record.mismatch_before_launch = _mismatches(mod.verify(_read_game(self.adapter, self.path), accepted))
        return self.record

    def finish(self, restore: bool = True) -> SettingsRecord:
        """Read the file the game left, then put the backup back (only if we wrote anything)."""
        mod = self.adapter.module
        if self.backup.is_file():
            try:
                data = _read_game(self.adapter, self.path)
                accepted = {k: v for k, v in self.requested.items() if k not in self.record.refused}
                self.record.mismatch_after_run = _mismatches(mod.verify(data, accepted))
                learn_indices(self.data_dir, self.app_id, self.adapter, data)
            except (OSError, ValueError) as e:
                self.record.notes.append(f"could not read the settings after the run: {e}")
        if restore and self._written:
            try:
                shutil.copy2(self.backup, self.path)
                self.record.restored = True
            except OSError as e:
                self.record.restored = False
                self.record.notes.append(f"your settings were not put back ({e}); the copy is {self.backup}")
        elif self._written:
            self.record.restored = False
        return self.record


SAVED_KEY = "forced_game_settings"          # settings.json: {app_id: {key: value}}


def setting_choices(adapter: Adapter, data, known: Dict[str, Dict[str, int]]) -> Dict[str, list]:
    """Values the dialog offers per setting (the adapter decides: on/off, fixed lists, values seen so far)."""
    return adapter.module.choices(data, known)


def read_for_dialog(app_id, data_dir: Path, env: Optional[Mapping[str, str]] = None):
    """(choices, current values) from the game's file, or raises OSError/ValueError."""
    adapter = adapter_for(app_id)
    if adapter is None:
        raise ValueError(f"no settings adapter for Steam app {app_id}")
    path = adapter.settings_path(env)
    if path is None or not path.is_file():
        raise OSError(f"the game's settings file was not found ({path}); start the game once first")
    data = _read_game(adapter, path)
    known = learn_indices(data_dir, app_id, adapter, data)
    return setting_choices(adapter, data, known), adapter.module.read_settings(data)


def saved_choice(settings: Mapping, app_id) -> Dict[str, object]:
    every = settings.get(SAVED_KEY)
    chosen = every.get(str(app_id)) if isinstance(every, dict) else None
    return dict(chosen) if isinstance(chosen, dict) else {}

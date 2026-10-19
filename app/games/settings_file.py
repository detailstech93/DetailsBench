"""Force a game's graphics settings for one benchmark, verify them, then put the user's own file back.

Only games with an adapter (a module that knows the settings file, see ADAPTERS) are supported. The
flow, all on the game's own settings and only after the user confirmed it:

1. read the settings and plan the changes (adapter.plan); values the game would not accept are refused
   with a reason and nothing is written for them;
2. copy the settings unchanged into the run folder (the backup), then write the changed ones (a file:
   under a temporary name, then replacing the original);
3. read them back and compare (verified before the launch);
4. after the game was closed, read them again (the game may rewrite them with what it really used:
   verified after the run), then put the backup back.

A game that keeps its settings in the registry (Rise of the Tomb Raider: DWORD values under one key in
HKEY_CURRENT_USER, written only with the user's permission given on 2026-10-18) has a RegistryStore
instead of a file: the backup is a JSON copy of the key's DWORD values, and only values that differ are
written, both when forcing and when putting the user's values back.

Some list settings carry a value or position the file alone does not list (Cyberpunk 2077's runtime
lists, free text in Borderlands 3, numbers in Red Dead Redemption 2 and The Witcher 3). What was seen is learned each time the settings are read (the user
picked the value once in the game's menu) and kept in game_settings_indices.json in the data folder.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from app.games import (borderlands3, counter_strike2, cyberpunk2077, doom2016, red_dead_redemption2,
                       rise_tomb_raider, witcher3)

INDICES_FILENAME = "game_settings_indices.json"
BACKUP_NAME = "game_settings_backup"      # + the store's own extension
PENDING_FILE = "pending_game_restore.json"  # data dir: {app_id: backup path} written before the game's settings
                                            # are changed, removed once they are put back; what is left after a
                                            # run that was cut off (app closed, crash) is restored later


def _read_pending(data_dir: Path) -> Dict[str, str]:
    try:
        data = json.loads((Path(data_dir) / PENDING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_pending(data_dir: Path, pending: Dict[str, str]) -> None:
    path = Path(data_dir) / PENDING_FILE
    if not pending:
        if path.is_file():
            path.unlink()                   # our own marker
        return
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(pending, indent=1), encoding="utf-8")
    partial.replace(path)


def _game_name(app_id) -> str:
    try:
        from data.steam_benchmarks import STEAM_BENCHMARK_APPS
    except ImportError:
        return f"Steam app {app_id}"
    names = {str(g.get("appid")): g.get("store_name") for g in STEAM_BENCHMARK_APPS}
    return str(names.get(str(app_id)) or f"Steam app {app_id}")


def _env_for(app_id, env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    """The given environment, else this system's for the game (Linux: the folders inside its Proton prefix)."""
    if env is not None:
        return env
    from app.games.proton import game_env
    return game_env(app_id)


def recover_pending(data_dir: Path, app_ids=None, env: Optional[Mapping[str, str]] = None, reg=None,
                    running=lambda app_id: False) -> List[str]:
    """Put back the settings of every game whose run was cut off before finish() (only the app ids given, when
    given; never a game running(app_id) says is open, since it would write its own over them on exit).
    Returns one line per game: what was done."""
    pending = _read_pending(data_dir)
    lines = []
    for app_id, backup in list(pending.items()):
        if app_ids is not None and app_id not in {str(a) for a in app_ids}:
            continue
        adapter = adapter_for(app_id)
        if adapter is None or not Path(backup).exists():
            lines.append(f"{app_id}: the copy of its settings is gone ({backup}); nothing put back")
            pending.pop(app_id)
            continue
        if running(app_id):
            lines.append(f"{_game_name(app_id)}: still open; its settings are put back after it is closed")
            continue
        try:
            adapter.store(_env_for(app_id, env), reg).restore(Path(backup))
        except (OSError, ValueError) as e:
            lines.append(f"{_game_name(app_id)}: your settings could not be put back ({e}); the copy is {backup}")
            continue
        pending.pop(app_id)
        lines.append(f"{_game_name(app_id)}: a run was cut off before your settings were put back; they are back now")
    _write_pending(data_dir, pending)
    return lines


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


class FileStore:
    def __init__(self, adapter: "Adapter", path: Optional[Path]):
        self.adapter, self.path = adapter, path
        self.where = str(path)
        self.backup_suffix = path.suffix if path else ""

    def exists(self) -> bool:
        return self.path is not None and self.path.is_file()

    def read(self):
        """Parse the game's file; bytes are decoded as they are (line endings kept)."""
        return self.adapter.module.parse(self.path.read_bytes().decode("utf-8"))

    def write(self, data) -> None:
        tmp = self.path.with_name(self.path.name + ".pcbm-tmp")
        tmp.write_bytes(self.adapter.module.dump(data).encode("utf-8"))
        os.replace(tmp, self.path)

    def save_backup(self, backup: Path) -> None:
        shutil.copy2(self.path, backup)

    def restore(self, backup: Path) -> None:
        if self.path is None:
            raise OSError("the game's settings folder is not known on this system")
        shutil.copy2(backup, self.path)


class WinReg:
    """DWORD values of one HKEY_CURRENT_USER key (Windows only)."""

    def read(self, key: str) -> Dict[str, int]:
        import winreg
        out = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            i = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(k, i)
                except OSError:
                    break
                if kind == winreg.REG_DWORD:
                    out[name] = value
                i += 1
        return out

    def set_dword(self, key: str, name: str, value: int) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


class RegistryStore:
    backup_suffix = ".json"

    def __init__(self, key: str, reg=None):
        self.key, self.reg = key, reg or WinReg()
        self.where = "HKEY_CURRENT_USER\\" + key

    def exists(self) -> bool:
        try:
            self.reg.read(self.key)
            return True
        except (OSError, ImportError):
            return False

    def read(self) -> Dict[str, int]:
        return self.reg.read(self.key)

    def write(self, data: Dict[str, int]) -> None:
        now = self.reg.read(self.key)
        for name, value in data.items():
            if now.get(name) != value:
                self.reg.set_dword(self.key, name, value)

    def save_backup(self, backup: Path) -> None:
        backup.write_text(json.dumps(self.reg.read(self.key), indent=1), encoding="utf-8")

    def restore(self, backup: Path) -> None:
        self.write(json.loads(backup.read_text(encoding="utf-8")))


class FilesStore:
    """The settings files of a game forced with launch arguments (module.settings_files names them). Missing
    files are skipped. The backup is a folder; restore copies back only files whose bytes changed, and only
    when the module allows it (RESTORE: Counter-Strike 2's file is read only)."""
    backup_suffix = ""

    def __init__(self, module, env: Mapping[str, str]):
        self.module = module
        self.files, self.where = module.settings_files(env)
        self.writable = bool(getattr(module, "RESTORE", False))

    def exists(self) -> bool:
        return any(p.is_file() for p in self.files.values())

    def read(self):
        return self.module.parse({name: p.read_bytes().decode("utf-8")
                                  for name, p in self.files.items() if p.is_file()})

    def save_backup(self, backup: Path) -> None:
        backup.mkdir(parents=True, exist_ok=True)
        for name, p in self.files.items():
            if p.is_file():
                shutil.copy2(p, backup / name)

    def restore(self, backup: Path) -> None:
        for name, p in self.files.items():
            saved = backup / name
            if saved.is_file() and (not p.is_file() or p.read_bytes() != saved.read_bytes()):
                shutil.copy2(saved, p)


@dataclass(frozen=True)
class Adapter:
    """module: read_settings, choices, observed_indices, merge_indices, plan, apply, verify, APP_ID,
    LAUNCH_ARGS, and parse/dump for a file. base: "localappdata", "documents" or "registry" (relative_path
    is then a key under HKEY_CURRENT_USER), or "launch": the module has launch_args() and settings_files()
    instead of apply/dump (the values go on the command line, see app/games/launch_settings.py)."""
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

    def store(self, env: Optional[Mapping[str, str]] = None, reg=None):
        if self.base == "registry":
            return RegistryStore(self.relative_path, reg)
        if self.base == "launch":
            return FilesStore(self.module, os.environ if env is None else env)
        return FileStore(self, self.settings_path(env))


ADAPTERS: Dict[str, Adapter] = {
    str(cyberpunk2077.APP_ID): Adapter(cyberpunk2077, os.path.join("CD Projekt Red", "Cyberpunk 2077",
                                                                   "UserSettings.json")),
    str(borderlands3.APP_ID): Adapter(borderlands3, os.path.join(
        "My Games", "Borderlands 3", "Saved", "Config", "WindowsNoEditor", "GameUserSettings.ini"), "documents"),
    str(rise_tomb_raider.APP_ID): Adapter(rise_tomb_raider, rise_tomb_raider.REGISTRY_KEY, "registry"),
    str(red_dead_redemption2.APP_ID): Adapter(red_dead_redemption2, os.path.join(
        "Rockstar Games", "Red Dead Redemption 2", "Settings", "system.xml"), "documents"),
    str(witcher3.APP_ID): Adapter(witcher3, os.path.join("The Witcher 3", "dx12user.settings"), "documents"),
    str(counter_strike2.APP_ID): Adapter(counter_strike2, "", "launch"),
    str(doom2016.APP_ID): Adapter(doom2016, "", "launch"),
}


def adapter_for(app_id) -> Optional[Adapter]:
    return ADAPTERS.get(str(app_id))


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".pcbm-tmp")
    tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
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
    launch_args: List[str] = field(default_factory=list)          # launch adapters: how the values were set
    unchecked: List[str] = field(default_factory=list)            # launch adapters: not in the files after the run

    @property
    def verified(self) -> bool:
        """Every requested setting was accepted, written and still in place after the run."""
        return (not self.refused and not self.mismatch_before_launch and not self.unchecked
                and self.mismatch_after_run is not None and not self.mismatch_after_run)

    def to_dict(self) -> dict:
        return {"file": self.file, "requested": dict(self.requested), "before": dict(self.before),
                "changed": list(self.changed), "refused": dict(self.refused),
                "mismatch_before_launch": dict(self.mismatch_before_launch),
                "mismatch_after_run": self.mismatch_after_run, "restored": self.restored,
                "verified": self.verified, "notes": list(self.notes),
                "launch_args": list(self.launch_args), "unchecked": list(self.unchecked)}


def _mismatches(found: Dict[str, tuple]) -> Dict[str, list]:
    return {k: [expected, actual] for k, (expected, actual) in found.items()}


class ForcedSettings:
    """apply() before the launch, finish() after the game was closed (also after a failure)."""

    def __init__(self, app_id, requested: Dict[str, object], run_dir: Path, data_dir: Path,
                 env: Optional[Mapping[str, str]] = None, reg=None):
        self.app_id = str(app_id)
        self.adapter = adapter_for(app_id)
        if self.adapter is None:
            raise ValueError(f"no settings adapter for Steam app {app_id}")
        env = _env_for(app_id, env)
        self.store = self.adapter.store(env, reg)
        self.requested = dict(requested)
        self.backup = Path(run_dir) / (BACKUP_NAME + self.store.backup_suffix)
        self.data_dir = Path(data_dir)
        self.record = SettingsRecord(self.store.where, self.requested)
        self._written = False
        self._launch = False
        self.env, self.reg = env, reg

    def _mark_pending(self) -> None:
        pending = _read_pending(self.data_dir)
        pending[self.app_id] = str(self.backup)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _write_pending(self.data_dir, pending)

    def _clear_pending(self) -> None:
        pending = _read_pending(self.data_dir)
        if pending.get(self.app_id) == str(self.backup):
            pending.pop(self.app_id)
            _write_pending(self.data_dir, pending)

    def apply(self) -> SettingsRecord:
        """Back up and write the settings. Raises OSError/ValueError when they cannot be read or written;
        the caller then calls finish() (puts back anything written) and does not launch the game."""
        mod = self.adapter.module
        if self.adapter.base == "launch":
            return self._apply_launch(mod)
        if not self.store.exists():
            raise OSError(f"the game's settings were not found ({self.store.where}); start the game once first")
        self.record.notes += recover_pending(self.data_dir, [self.app_id], self.env, self.reg)
        data = self.store.read()
        self.record.before = mod.read_settings(data)
        known = learn_indices(self.data_dir, self.app_id, self.adapter, data)
        changes, refused = mod.plan(data, self.requested, known)
        self.record.refused = dict(refused)
        self.record.changed = [c.key for c in changes]
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        self.store.save_backup(self.backup)
        if changes:
            self._written = True            # a write that fails half way is still put back by finish()
            self._mark_pending()
            self.store.write(mod.apply(data, changes))
        accepted = {k: v for k, v in self.requested.items() if k not in refused}
        self.record.mismatch_before_launch = _mismatches(mod.verify(self.store.read(), accepted))
        return self.record

    def _apply_launch(self, mod) -> SettingsRecord:
        """Nothing is written: the accepted values become this launch's arguments (record.launch_args)."""
        if self.store.writable:
            self.record.notes += recover_pending(self.data_dir, [self.app_id], self.env, self.reg)
        data = self.store.read() if self.store.exists() else mod.parse({})
        self.record.before = mod.read_settings(data)
        known = learn_indices(self.data_dir, self.app_id, self.adapter, data)
        changes, refused = mod.plan(data, self.requested, known)
        self.record.refused = dict(refused)
        self.record.changed = [c.key for c in changes if c.old != c.new or type(c.old) is not type(c.new)]
        self.record.launch_args = mod.launch_args({c.key: c.new for c in changes})
        self._launch = True
        if self.store.writable and self.store.exists():
            self.store.save_backup(self.backup)
            self._written = True            # the game may save the values when it closes: put the files back
            self._mark_pending()
        return self.record

    def _finish_launch(self, mod) -> None:
        accepted = {k: v for k, v in self.requested.items() if k not in self.record.refused}
        try:
            data = self.store.read() if self.store.exists() else mod.parse({})
        except (OSError, ValueError) as e:
            self.record.notes.append(f"could not read the settings after the run: {e}")
            return
        found = mod.verify(data, accepted)
        self.record.unchecked = [k for k, (_, actual) in found.items() if actual is None]
        self.record.mismatch_after_run = _mismatches({k: v for k, v in found.items() if v[1] is not None})
        if self.record.unchecked:
            self.record.notes.append("not in the game's settings files after the run, so not checked: "
                                     + ", ".join(self.record.unchecked))
        learn_indices(self.data_dir, self.app_id, self.adapter, data)
        now = mod.read_settings(data)
        kept = [k for k in accepted if k in now and now[k] != self.record.before.get(k)]
        if kept and not self.store.writable:
            self.record.notes.append("the game saved these forced values in its own settings, which the app "
                                     "only reads; set them back in the game's menu if needed: " + ", ".join(kept))

    def finish(self, restore: bool = True) -> SettingsRecord:
        """Read what the game left, then put the backup back (only if we wrote anything)."""
        mod = self.adapter.module
        if self._launch:
            self._finish_launch(mod)
        elif self.backup.is_file():
            try:
                data = self.store.read()
                accepted = {k: v for k, v in self.requested.items() if k not in self.record.refused}
                self.record.mismatch_after_run = _mismatches(mod.verify(data, accepted))
                learn_indices(self.data_dir, self.app_id, self.adapter, data)
            except (OSError, ValueError) as e:
                self.record.notes.append(f"could not read the settings after the run: {e}")
        if restore and self._written:
            try:
                self.store.restore(self.backup)
                self.record.restored = True
                self._clear_pending()
            except (OSError, ValueError) as e:
                self.record.restored = False
                self.record.notes.append(f"your settings were not put back ({e}); the copy is {self.backup}")
        elif self._written:
            self.record.restored = False
        return self.record


SAVED_KEY = "forced_game_settings"          # settings.json: {app_id: {key: value}}
DECLARED_KEY = "declared_game_settings"     # games without an adapter: what the user says they set


def declared_record(values: Mapping[str, object]) -> dict:
    """Stored in the manifest like a SettingsRecord, but the values were only entered by the user."""
    return {"declared": dict(values), "verified": False,
            "notes": ["entered by the user; the app cannot read or force this game's settings"]}


def setting_choices(adapter: Adapter, data, known: Dict[str, Dict[str, int]]) -> Dict[str, list]:
    """Values the dialog offers per setting (the adapter decides: on/off, fixed lists, values seen so far)."""
    return adapter.module.choices(data, known)


def read_for_dialog(app_id, data_dir: Path, env: Optional[Mapping[str, str]] = None, reg=None):
    """(choices, current values) from the game's settings, or raises OSError/ValueError."""
    adapter = adapter_for(app_id)
    if adapter is None:
        raise ValueError(f"no settings adapter for Steam app {app_id}")
    store = adapter.store(_env_for(app_id, env), reg)
    if store.exists():
        data = store.read()
    elif adapter.base == "launch":
        data = adapter.module.parse({})        # launch arguments do not need the files
    else:
        raise OSError(f"the game's settings were not found ({store.where}); start the game once first")
    known = learn_indices(data_dir, app_id, adapter, data)
    return setting_choices(adapter, data, known), adapter.module.read_settings(data)


def saved_choice(settings: Mapping, app_id) -> Dict[str, object]:
    every = settings.get(SAVED_KEY)
    chosen = every.get(str(app_id)) if isinstance(every, dict) else None
    return dict(chosen) if isinstance(chosen, dict) else {}

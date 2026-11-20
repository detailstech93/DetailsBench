"""Games that pick their graphics card by a number in their settings file (RDR2: video/adapterIndex).

Fedora 2026-11-20: RDR2's number 1 pointed at Mesa's lavapipe (a Vulkan device that draws on the CPU), the night
before at the Radeon; the order of the Vulkan list is not fixed. When a run draws on the CPU the app tries the
next number, and keeps the one that drew on the graphics card for this PC (its data folder). The file is put back
as it was after the runs, like the forced settings.
"""

from pathlib import Path
from typing import Mapping, Optional

KEY = "game_graphics_card"      # settings.json: {app_id: the number that drew on the graphics card on this PC}
NUMBERS = ("0", "1", "2")       # shortcut: up to three Vulkan devices tried; more if a PC ever lists more
BACKUP_NAME = "graphics_card_backup"


class CardChoice:
    def __init__(self, app_id, ref: str, adapter, store, run_dir: Path, data_dir: Path):
        self.app_id, self.ref, self.adapter, self.store = str(app_id), ref, adapter, store
        self.backup = Path(run_dir) / (BACKUP_NAME + store.backup_suffix)
        self.data_dir = Path(data_dir)
        self.tried = []
        self._backed_up = False

    @classmethod
    def for_game(cls, app_id, run_dir: Path, data_dir: Path,
                 env: Optional[Mapping[str, str]] = None) -> Optional["CardChoice"]:
        """None for a game without such a number, or whose settings file is not there."""
        from app.games.settings_file import adapter_for
        adapter = adapter_for(app_id)
        ref = getattr(getattr(adapter, "module", None), "GRAPHICS_CARD_REF", None)
        if not ref:
            return None
        store = adapter.store(env)
        if not store.exists():
            return None
        return cls(app_id, ref, adapter, store, run_dir, data_dir)

    def current(self) -> Optional[str]:
        return self.adapter.module.get(self.store.read(), self.ref)

    def set(self, number: str, ran: bool = True) -> None:
        """Write the number (the file is copied once first, put back by restore()). ran: the number it replaces was
        run with (drew on the CPU), so it is not tried again. Raises OSError/ValueError."""
        if not self._backed_up:
            self.backup.parent.mkdir(parents=True, exist_ok=True)
            self.store.save_backup(self.backup)
            self._backed_up = True
        data = self.store.read()
        if ran:
            self.tried.append(self.adapter.module.get(data, self.ref))
        self.store.write(self.adapter.module.put(data, self.ref, number))

    def next_number(self) -> Optional[str]:
        """The next number not run with in these runs (the one in the file now is the one just run), or None."""
        now = self.current()
        done = set(self.tried) | ({now} if now is not None else set())
        return next((n for n in NUMBERS if n not in done), None)

    def remembered(self) -> Optional[str]:
        from app.paths import load_settings
        value = (load_settings(self.data_dir)[0].get(KEY) or {}).get(self.app_id)
        return value if value in NUMBERS else None

    def remember(self, number: str) -> None:
        from app.paths import load_settings, save_setting
        saved = dict(load_settings(self.data_dir)[0].get(KEY) or {})
        if saved.get(self.app_id) != number:
            saved[self.app_id] = number
            save_setting(self.data_dir, KEY, saved)

    def restore(self) -> str:
        """The file as it was before the first number was written; "" or why it could not be."""
        if not self._backed_up:
            return ""
        try:
            self.store.restore(self.backup)
            return ""
        except OSError as e:
            return f"; its graphics card number was not put back ({e}); the copy is {self.backup}"

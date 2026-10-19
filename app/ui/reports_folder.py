"""Where the reports are saved: one folder the owner picks, asked once (the first run from the Tests screen, or the
first time remote mode is switched on) and kept in settings.json. Every report of a run, a typed score and a remote
start goes there. Until a folder is picked the reports go to the app's data folder (reports\\ next to the database).
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SETTINGS_KEY = "reports_folder"
TITLE = "Where should the reports be saved?"


def pick(parent, title: str, start: str) -> str:
    """The folder window (the tests replace it: nothing may wait for a click there)."""
    from PySide6.QtWidgets import QFileDialog
    return QFileDialog.getExistingDirectory(parent, title, start)


def default_folder(window) -> Optional[Path]:
    root = getattr(window.automation_panel, "output_root", None)
    return Path(root).parent / "reports" if root else None


def saved_folder(window) -> Optional[Path]:
    """The folder picked earlier, or None (never picked, or the setting is not a usable path)."""
    from app.paths import load_settings
    try:
        value = load_settings(window.frame_panel.data_dir)[0].get(SETTINGS_KEY)
    except Exception:
        return None
    return Path(value) if isinstance(value, str) and value.strip() and Path(value).is_absolute() else None


def folder(window) -> Optional[Path]:
    """Where the next report goes (never asks)."""
    return saved_folder(window) or default_folder(window)


def choose(window, always: bool = False) -> Optional[Path]:
    """Ask for the folder when none was picked yet (or always: the "Reports folder..." button). A cancelled
    window keeps the folder there was; the question comes again next time."""
    saved = saved_folder(window)
    if saved is not None and not always:
        return saved
    current = saved or default_folder(window)
    try:
        chosen = pick(window, TITLE, str(current or Path.home()))
    except Exception:
        logger.exception("Could not show the folder window")
        chosen = ""
    if not chosen:
        return current
    path = Path(chosen)
    try:
        path.mkdir(parents=True, exist_ok=True)
        from app.paths import save_setting
        save_setting(window.frame_panel.data_dir, SETTINGS_KEY, str(path))
    except (OSError, ValueError) as e:
        logger.exception("Could not keep the reports folder")
        window.status_label.setText(f"The reports folder could not be kept: {e}")
        return current
    window.status_label.setText(f"Reports are saved in: {path}")
    return path

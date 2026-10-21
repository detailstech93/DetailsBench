"""Light / Dark / System look (owner, 2026-10-20: "a toggle to make the tool light or dark").

The choice is kept in settings.json ("theme": "system" | "light" | "dark"). Qt 6.8+ sets the color scheme itself
(QStyleHints.setColorScheme); where the platform's style does not follow it (some Linux desktops, WSL), the app
switches to Qt's Fusion style with its own light or dark palette, so the choice always shows. "System" follows the
system's own setting and clears any palette the app set.
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

KEY = "theme"
CHOICES = (("system", "System"), ("light", "Light"), ("dark", "Dark"))
SCHEMES = {"system": Qt.ColorScheme.Unknown, "light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}
_native = {}          # the style and palette the app started with (back to them for "system")


def saved(data_dir: Optional[Path] = None) -> str:
    from app.paths import get_data_dir, load_settings
    value = load_settings(Path(data_dir or get_data_dir()))[0].get(KEY)
    return value if value in SCHEMES else "system"


def save(mode: str, data_dir: Optional[Path] = None) -> None:
    from app.paths import get_data_dir, save_setting
    save_setting(Path(data_dir or get_data_dir()), KEY, mode)


def palette(mode: str) -> QPalette:
    """Fusion's palette for "light" or "dark"."""
    p = QPalette()
    if mode == "dark":
        colors = {QPalette.Window: "#202124", QPalette.WindowText: "#e8eaed", QPalette.Base: "#17181a",
                  QPalette.AlternateBase: "#26282b", QPalette.Text: "#e8eaed", QPalette.Button: "#2d2f33",
                  QPalette.ButtonText: "#e8eaed", QPalette.ToolTipBase: "#2d2f33", QPalette.ToolTipText: "#e8eaed",
                  QPalette.Highlight: "#3b82f6", QPalette.HighlightedText: "#ffffff", QPalette.Link: "#8ab4f8",
                  QPalette.PlaceholderText: "#9aa0a6", QPalette.BrightText: "#ffffff"}
        disabled = "#80868b"
    else:
        colors = {QPalette.Window: "#f3f4f6", QPalette.WindowText: "#1f2328", QPalette.Base: "#ffffff",
                  QPalette.AlternateBase: "#f6f8fa", QPalette.Text: "#1f2328", QPalette.Button: "#ffffff",
                  QPalette.ButtonText: "#1f2328", QPalette.ToolTipBase: "#ffffff", QPalette.ToolTipText: "#1f2328",
                  QPalette.Highlight: "#2563eb", QPalette.HighlightedText: "#ffffff", QPalette.Link: "#1d4ed8",
                  QPalette.PlaceholderText: "#6e7781", QPalette.BrightText: "#000000"}
        disabled = "#8c959f"
    for role, color in colors.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(disabled))
    return p


def apply(mode: str, app: Optional[QApplication] = None) -> str:
    """Show the app in this mode; returns how: "qt" (the style follows the color scheme), "fusion" (the app's own
    palette) or "system"."""
    app = app or QApplication.instance()
    if app is None or mode not in SCHEMES:
        return "system"
    how = _apply(mode, app)
    _restyle(app)
    return how


def _apply(mode: str, app: QApplication) -> str:
    _native.setdefault("style", app.style().name())
    hints = app.styleHints()
    hints.setColorScheme(SCHEMES[mode])
    if app.style().name() != _native["style"]:        # an earlier Light/Dark used Fusion: back to the own style
        app.setStyle(_native["style"])
    app.setPalette(QPalette())                         # the platform's palette for the scheme just set
    if mode == "system":
        return "system"
    if hints.colorScheme() == SCHEMES[mode]:
        return "qt"
    app.setStyle("Fusion")
    app.setPalette(palette(mode))
    return "fusion"


def _restyle(app: QApplication) -> None:
    """Widgets whose own style sheet uses palette(...) (muted text) keep the old colors until it is set again."""
    for widget in app.allWidgets():
        sheet = widget.styleSheet()
        if "palette(" in sheet:
            widget.setStyleSheet(sheet)

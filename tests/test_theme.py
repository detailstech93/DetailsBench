"""Light / Dark / System look: the choice is kept in settings.json and shows even where the platform's style does
not follow Qt's color scheme (the offscreen platform here, like some Linux desktops)."""

import sys

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from app.ui import theme


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_the_choice_is_kept_and_unknown_values_mean_system(tmp_path):
    assert theme.saved(tmp_path) == "system"
    theme.save("dark", tmp_path)
    assert theme.saved(tmp_path) == "dark"
    (tmp_path / "settings.json").write_text('{"theme": "purple"}', encoding="utf-8")
    assert theme.saved(tmp_path) == "system"


def test_dark_and_light_show_and_system_gives_the_style_back(qapp):
    style = qapp.style().name()
    how = theme.apply("dark", qapp)
    assert how in ("qt", "fusion")
    assert qapp.palette().color(QPalette.Window).lightness() < 100
    theme.apply("light", qapp)
    assert qapp.palette().color(QPalette.Window).lightness() > 200
    assert theme.apply("system", qapp) == "system" and qapp.style().name() == style
    assert theme.apply("blue", qapp) == "system"


def test_the_header_box_saves_the_choice(qapp, tmp_path, monkeypatch):
    from app.ui import main_window
    saved = []
    monkeypatch.setattr(theme, "save", lambda mode, data_dir=None: saved.append(mode))
    monkeypatch.setattr(theme, "apply", lambda mode, app=None: "fusion")
    window = main_window.MainWindow.__new__(main_window.MainWindow)
    main_window.MainWindow.set_theme(window, "dark")
    assert saved == ["dark"]

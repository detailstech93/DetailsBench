import sys

import pytest
from PySide6.QtWidgets import QApplication

from app.ui.game_settings_dialog import KEEP, GameSettingsDialog, describe, value_text


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_dialog_starts_from_the_saved_choice_and_returns_only_forced(qapp):
    d = GameSettingsDialog("Cyberpunk 2077", {"ray_tracing": [True, False], "dlss_mode": ["Quality", "Balanced"]},
                           {"ray_tracing": True, "dlss_mode": "Balanced"}, {"ray_tracing": False})
    assert d.combos["ray_tracing"].currentText() == "Off"
    assert d.combos["dlss_mode"].currentText() == KEEP
    assert d.combos["dlss_mode"].itemText(2) == "Balanced  (now)"
    assert d.selection() == {"ray_tracing": False}
    d.combos["dlss_mode"].setCurrentIndex(1)
    assert d.selection() == {"ray_tracing": False, "dlss_mode": "Quality"}
    assert isinstance(d.selection()["ray_tracing"], bool)


def test_describe():
    assert describe({}) is None
    assert describe({"ray_tracing": True, "resolution": "2560x1440"}) == "Ray tracing On, Resolution 2560x1440"
    assert value_text(False) == "Off"

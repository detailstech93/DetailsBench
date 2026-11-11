"""The emergency stop hotkey that works while a game is in front (app/ui/global_stop.py)."""

import ctypes
import sys
from ctypes import wintypes

import pytest
from PySide6.QtWidgets import QApplication

from app.ui.global_stop import HOTKEY_ID, KEY_X, WM_HOTKEY, GlobalStop


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class User32:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def RegisterHotKey(self, hwnd, ident, mods, key):
        self.calls.append(("register", hwnd, ident, key))
        return 1 if self.ok else 0

    def UnregisterHotKey(self, hwnd, ident):
        self.calls.append(("unregister", hwnd, ident))
        return 1


def test_the_hotkey_is_held_only_while_a_test_runs_and_stops_it(qapp):
    """2026-11-11: a full-screen game kept Ctrl+Shift+X from the app's window and the line could not be stopped."""
    from PySide6.QtWidgets import QWidget
    window = QWidget()
    running, stops, user32 = [False], [], User32()
    stop = GlobalStop(window, lambda: running[0], lambda: stops.append(1), user32=user32, platform="win32")
    try:
        hwnd = int(window.winId())
        stop.check()
        assert user32.calls == [] and not stop.registered          # nothing runs: the keys stay free
        running[0] = True
        stop.check()
        stop.check()
        assert user32.calls == [("register", hwnd, HOTKEY_ID, KEY_X)] and stop.registered      # once
        msg = wintypes.MSG()
        msg.message, msg.wParam = WM_HOTKEY, HOTKEY_ID
        assert stop.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (True, 0) and stops == [1]
        msg.wParam = HOTKEY_ID + 1                                  # another hotkey: not ours
        assert stop.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0) and stops == [1]
        running[0] = False
        stop.check()
        assert user32.calls[-1] == ("unregister", hwnd, HOTKEY_ID) and not stop.registered
    finally:
        stop.timer.stop()
        window.close()


def test_a_refused_hotkey_is_tried_again_and_other_systems_do_nothing(qapp):
    from PySide6.QtWidgets import QWidget
    window = QWidget()
    user32 = User32(ok=False)
    stop = GlobalStop(window, lambda: True, lambda: None, user32=user32, platform="win32")
    other = GlobalStop(window, lambda: True, lambda: None, platform="linux")
    try:
        stop.check()
        stop.check()
        assert len(user32.calls) == 2 and not stop.registered       # another program holds the keys
        other.check()
        assert other.user32 is None and not other.timer.isActive()
    finally:
        stop.timer.stop()
        window.close()

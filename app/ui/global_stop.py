"""The emergency stop while a game is in front: Ctrl+Shift+X as a Windows hotkey, only while a test runs.

The window's own shortcut needs this app in front, and a full-screen game takes the keyboard: on 2026-11-11 a line of
games could not be stopped from the keyboard. RegisterHotKey reaches the app whatever window is in front. It is
registered only while a test runs, so the keys stay free for other programs the rest of the time, and it is the
app's own hotkey (nothing in Windows' settings changes). Windows only; elsewhere this does nothing.
"""

import ctypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

WM_HOTKEY = 0x0312
HOTKEY_ID = 0x5842                                  # any number of this window's own
MODIFIERS = 0x0002 | 0x0004 | 0x4000                # Ctrl, Shift, no repeat while held
KEY_X = 0x58
CHECK_MS = 1000


class GlobalStop(QAbstractNativeEventFilter):
    def __init__(self, window, is_running, on_stop, user32=None, platform: str = sys.platform):
        super().__init__()
        self.window, self.is_running, self.on_stop = window, is_running, on_stop
        self.user32 = user32 if user32 is not None else (ctypes.windll.user32 if platform == "win32" else None)
        self.registered = False
        self.timer = QTimer(window)
        self.timer.timeout.connect(self.check)
        if self.user32 is not None:
            self.timer.start(CHECK_MS)

    def check(self) -> None:
        """Register while a test runs, give the keys back when none does."""
        if self.user32 is None:
            return
        running = bool(self.is_running())
        if running and not self.registered:
            # refused (another program holds the keys): tried again at the next check; the window's shortcut stays
            self.registered = bool(self.user32.RegisterHotKey(int(self.window.winId()), HOTKEY_ID, MODIFIERS, KEY_X))
        elif not running and self.registered:
            self.close()

    def close(self) -> None:
        if self.registered and self.user32 is not None:
            self.user32.UnregisterHotKey(int(self.window.winId()), HOTKEY_ID)
        self.registered = False

    def nativeEventFilter(self, event_type, message):
        if self.registered and bytes(event_type) == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.on_stop()
                return True, 0
        return False, 0

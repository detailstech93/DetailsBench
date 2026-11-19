"""The emergency stop while a game is in front: Ctrl+Shift+X as a Windows hotkey, only while a test runs.

The window's own shortcut needs this app in front, and a full-screen game takes the keyboard: on 2026-11-11 a line of
games could not be stopped from the keyboard. RegisterHotKey reaches the app whatever window is in front. It is
registered only while a test runs, so the keys stay free for other programs the rest of the time, and it is the
app's own hotkey (nothing in Windows' settings changes).

Linux (owner, 2026-11-19): the desktop's GlobalShortcuts portal on Wayland (it asks the owner once to allow the keys),
an X11 key grab on an X11 session (app/ui/linux_hotkey.py); when neither works, on_problem says how to stop instead.
"""

import ctypes
import sys
import threading

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

WM_HOTKEY = 0x0312
HOTKEY_ID = 0x5842                                  # any number of this window's own
MODIFIERS = 0x0002 | 0x0004 | 0x4000                # Ctrl, Shift, no repeat while held
KEY_X = 0x58
CHECK_MS = 1000
LINUX_CHECK_MS = 300                                # a press on Linux is seen at the next check
NO_SHORTCUT = ("Ctrl+Shift+X does not reach a game on this desktop ({why}): stop the test from the app's window "
               "or from the Remote page")


class GlobalStop(QAbstractNativeEventFilter):
    def __init__(self, window, is_running, on_stop, user32=None, platform: str = sys.platform,
                 on_problem=None, linux_shortcut=None):
        super().__init__()
        self.window, self.is_running, self.on_stop = window, is_running, on_stop
        self.user32 = user32 if user32 is not None else (ctypes.windll.user32 if platform == "win32" else None)
        self.registered = False
        self.timer = QTimer(window)
        self.timer.timeout.connect(self.check)
        self.linux = None
        if platform.startswith("linux"):
            from app.ui.linux_hotkey import shortcut_for
            self.linux = (linux_shortcut or shortcut_for)(self._pressed)
            self.on_problem, self.pressed, self.opening, self.told = on_problem, threading.Event(), None, False
            self.timer.start(LINUX_CHECK_MS)
        elif self.user32 is not None:
            self.timer.start(CHECK_MS)

    def _pressed(self):
        self.pressed.set()                  # from the shortcut's own thread: acted on at the next check (Qt thread)

    def _open(self):
        self.opened = bool(self.linux.open())

    def check_linux(self) -> None:
        running = bool(self.is_running())
        if self.opening is not None and not self.opening.is_alive():
            self.opening = None             # asked: the desktop answered (or not)
            self.registered = self.opened
            if not self.registered and running and not self.told and self.on_problem is not None:
                self.told = True            # once per test
                self.on_problem(NO_SHORTCUT.format(why=self.linux.problem or "not allowed"))
        if self.pressed.is_set():
            self.pressed.clear()
            if running:
                self.on_stop()
        if running and not self.registered and self.opening is None and not self.told:
            # the desktop may show its dialog the first time: asked on a thread, the app goes on
            self.opening = threading.Thread(target=self._open, daemon=True, name="linux-hotkey")
            self.opening.start()
        elif not running and self.opening is None:
            if self.registered:
                self.close()
            self.told = False

    def check(self) -> None:
        """Register while a test runs, give the keys back when none does."""
        if self.linux is not None:
            return self.check_linux()
        if self.user32 is None:
            return
        running = bool(self.is_running())
        if running and not self.registered:
            # refused (another program holds the keys): tried again at the next check; the window's shortcut stays
            self.registered = bool(self.user32.RegisterHotKey(int(self.window.winId()), HOTKEY_ID, MODIFIERS, KEY_X))
        elif not running and self.registered:
            self.close()

    def close(self) -> None:
        if self.linux is not None:
            self.linux.close()
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

"""Win32Desktop.focus falls back to minimize + restore when Windows refuses SetForegroundWindow (laptop 2026-11-27)."""
from types import SimpleNamespace

from app.simulation.desktop import Win32Desktop


class FakeUser32:
    def __init__(self, refuse):
        self.fg, self.refuse, self.calls = 1, refuse, []

    def GetForegroundWindow(self):
        return self.fg

    def AttachThreadInput(self, *a):
        return 0

    def keybd_event(self, *a):
        pass

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("show", cmd))

    def BringWindowToTop(self, hwnd):
        pass

    def SetForegroundWindow(self, hwnd):
        if not self.refuse:
            self.fg = hwnd

    def SwitchToThisWindow(self, hwnd, alt_tab):
        self.calls.append(("switch", hwnd))
        self.fg = hwnd


def _desk(refuse):
    desk = Win32Desktop.__new__(Win32Desktop)
    desk.user32 = FakeUser32(refuse)
    desk.kernel32 = SimpleNamespace(GetCurrentThreadId=lambda: 5)
    desk._pid_of = lambda hwnd: (0, 0)
    return desk


def test_refused_focus_minimizes_restores_and_switches():
    desk = _desk(refuse=True)
    desk.focus(42)
    assert desk.user32.calls[-3:] == [("show", 6), ("show", 9), ("switch", 42)]
    assert desk.user32.GetForegroundWindow() == 42


def test_granted_focus_does_not_minimize_the_game():
    desk = _desk(refuse=False)
    desk.focus(42)
    assert ("show", 6) not in desk.user32.calls

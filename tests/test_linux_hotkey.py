"""Ctrl+Shift+X while a game is in front on Linux (app/ui/linux_hotkey.py, app/ui/global_stop.py), with no real bus."""

import sys

import pytest

from app.ui import linux_hotkey as lh
from app.ui.linux_hotkey import Variant


def test_messages_are_written_and_read_back():
    shortcuts = [(lh.SHORTCUT_ID, {"description": Variant("s", "stop"), "preferred_trigger": Variant("s", "CTRL+SHIFT+x")})]
    args = ["/org/freedesktop/portal/desktop/session/1_2/t", shortcuts, "", {"handle_token": Variant("s", "t")}]
    data = lh.message(1, 7, {1: ("o", "/p"), 3: ("s", "BindShortcuts")}, "oa(sa{sv})sa{sv}", args)
    kind, fields, body, size = lh.parse(data + b"next")
    assert (kind, fields[1], fields[3], fields[8], size) == (1, "/p", "BindShortcuts", "oa(sa{sv})sa{sv}", len(data))
    assert body == args
    assert lh.parse(data[:-1]) is None                                  # not all there yet
    assert lh.split("oa(sa{sv})sa{sv}") == ["o", "a(sa{sv})", "s", "a{sv}"]
    response = [0, {"n": Variant("t", 2 ** 40), "ok": Variant("b", 1)}]
    assert lh.parse(lh.message(4, 1, {}, "ua{sv}", response))[2] == response


class FakePortal:
    """The session bus and the desktop's portal: each request answers with its Response signal."""

    def __init__(self, on_signal, refuse=False):
        self.on_signal, self.refuse, self.calls, self.closed = on_signal, refuse, [], False

    def call(self, dest, path, iface, member, sig="", args=()):
        self.calls.append(member)
        request = f"/request/{member}"
        if member == "CreateSession":
            self.on_signal({1: request, 2: "org.freedesktop.portal.Request", 3: "Response"},
                           [0, {"session_handle": Variant("s", "/session/1")}])
            return [request]
        if member == "BindShortcuts":
            assert args[0] == "/session/1" and args[1][0][0] == lh.SHORTCUT_ID and sig == "oa(sa{sv})sa{sv}"
            self.on_signal({1: request, 2: "org.freedesktop.portal.Request", 3: "Response"},
                           [1, {}] if self.refuse else [0, {"shortcuts": Variant("a(sa{sv})", [(lh.SHORTCUT_ID, {})])}])
            return [request]
        return []

    def press(self, shortcut=lh.SHORTCUT_ID):
        self.on_signal({2: lh.SHORTCUTS, 3: "Activated"}, ["/session/1", shortcut, 0, {}])

    def close(self):
        self.closed = True


def test_the_portal_gives_the_shortcut_and_tells_each_press():
    presses, made = [], []
    p = lh.PortalShortcut(lambda: presses.append(1), bus_factory=lambda s: made.append(FakePortal(s)) or made[-1])
    assert p.open() and p.session == "/session/1"
    bus = made[0]
    assert bus.calls == ["AddMatch", "AddMatch", "Register", "CreateSession", "BindShortcuts"]
    bus.press("something-else")
    bus.press()
    assert presses == [1]
    p.close()
    assert bus.calls[-1] == "Close" and bus.closed and p.session is None


def test_a_refused_shortcut_says_why_and_keeps_nothing():
    made = []
    p = lh.PortalShortcut(lambda: None, bus_factory=lambda s: made.append(FakePortal(s, refuse=True)) or made[-1])
    assert not p.open() and "refused" in p.problem and made[0].closed and p.session is None


def test_x11_session_grabs_the_keys_and_wayland_asks_the_portal():
    assert isinstance(lh.shortcut_for(lambda: None, {"XDG_SESSION_TYPE": "x11"}), lh.X11Shortcut)
    assert isinstance(lh.shortcut_for(lambda: None, {"XDG_SESSION_TYPE": "wayland"}), lh.PortalShortcut)


class FakeShortcut:
    def __init__(self, on_press, works=True):
        self.on_press, self.works, self.opened, self.closed, self.problem = on_press, works, 0, 0, None

    def open(self):
        self.opened += 1
        self.problem = None if self.works else "refused on the desktop"
        return self.works

    def close(self):
        self.closed += 1


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _stop(qapp, works):
    from PySide6.QtWidgets import QWidget
    from app.ui.global_stop import GlobalStop
    running, stops, told, made = [False], [], [], []
    stop = GlobalStop(QWidget(), lambda: running[0], lambda: stops.append(1), platform="linux",
                      on_problem=told.append,
                      linux_shortcut=lambda on_press: made.append(FakeShortcut(on_press, works)) or made[-1])
    stop.timer.stop()                       # the test calls check() itself

    def tick():
        stop.check()
        if stop.opening is not None:
            stop.opening.join(5)
        stop.check()
    return stop, running, stops, told, made[0], tick


def test_linux_stop_keys_only_while_a_test_runs(qapp):
    stop, running, stops, told, shortcut, tick = _stop(qapp, works=True)
    tick()
    assert shortcut.opened == 0 and not stop.registered                 # no test: the keys stay free
    running[0] = True
    tick()
    assert shortcut.opened == 1 and stop.registered and told == []
    shortcut.on_press()
    tick()
    assert stops == [1]
    running[0] = False
    tick()
    assert shortcut.closed == 1 and not stop.registered
    shortcut.on_press()                                                  # pressed with no test: nothing
    tick()
    assert stops == [1]


def test_linux_without_the_shortcut_says_how_to_stop_once_per_test(qapp):
    stop, running, stops, told, shortcut, tick = _stop(qapp, works=False)
    running[0] = True
    tick()
    tick()
    assert shortcut.opened == 1 and len(told) == 1
    assert "refused on the desktop" in told[0] and "Remote page" in told[0]
    running[0] = False
    tick()
    running[0] = True
    tick()
    assert shortcut.opened == 2 and len(told) == 2                      # the next test asks again

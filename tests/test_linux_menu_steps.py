"""Menu steps on Linux: the X11 desktop (fake Xlib), Tesseract's TSV, the install plan and the panel's checks."""

import ctypes
import struct
import zlib
from types import SimpleNamespace

from app.frames import screen_text
from app.frames.linux_install import tesseract_install_plan
from app.simulation import x11_desktop as xd
from app.simulation.desktop import parse_chord

TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
       "1\t1\t0\t0\t0\t0\t0\t0\t1920\t1080\t-1\t\n"
       "5\t1\t2\t1\t1\t1\t100\t50\t90\t20\t95.1\tGraphics\n"
       "5\t1\t1\t1\t1\t1\t10\t10\t60\t20\t96.0\tStory\n"
       "5\t1\t1\t1\t1\t2\t80\t10\t50\t20\t91.3\tMode\n"
       "5\t1\t1\t1\t2\t1\t10\t40\t70\t20\t-1\t \n")


def test_tesseract_tsv_gives_lines_and_word_boxes(tmp_path):
    lines = screen_text.parse_tsv(TSV)
    assert lines == ["Story Mode", "Graphics"]
    assert lines[1].words == (("Graphics", 100, 50, 90, 20),) and screen_text.word_center(lines, "graph") == (145, 60)
    seen = []
    ok = lambda cmd, **kw: (seen.append(cmd), SimpleNamespace(returncode=0, stdout=TSV.encode(), stderr=b""))[1]
    assert screen_text.read_image_tesseract(tmp_path / "s.png", run=ok, which=lambda n: "/usr/bin/tesseract")[0] \
        == ["Story Mode", "Graphics"]
    assert seen[0] == ["/usr/bin/tesseract", str(tmp_path / "s.png"), "stdout", "-l", "eng", "--psm", "11", "tsv"]
    bad = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=b"", stderr=b"Error opening data file eng\n")
    assert screen_text.read_image_tesseract("x", run=bad, which=lambda n: "/t")[1] == \
        "Tesseract failed: Error opening data file eng"


def test_tesseract_install_plan_per_distribution():
    which = lambda n: None if n == "tesseract" else f"/usr/bin/{n}"
    plan = tesseract_install_plan({"ID": "ubuntu"}, which, "x64")
    assert plan.command == ["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", "tesseract-ocr", "tesseract-ocr-eng"]
    assert plan.needs_update_first and "not verified" in plan.note
    assert tesseract_install_plan({"ID": "arch"}, which, "arm64").command[-2:] == ["tesseract", "tesseract-data-eng"]
    assert tesseract_install_plan({"ID": "fedora"}, which, "x64").command[-1] == "tesseract-langpack-eng"
    assert "already installed" in tesseract_install_plan({"ID": "ubuntu"}, lambda n: "/usr/bin/x", "x64").note


def test_session_check_and_keys():
    assert xd.session_problem({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}) is None     # XWayland is there
    assert xd.session_problem({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}) is None
    assert "XWayland" in xd.session_problem({"XDG_SESSION_TYPE": "wayland"})
    assert xd.is_wayland({"WAYLAND_DISPLAY": "wayland-0"}) and not xd.is_wayland({"XDG_SESSION_TYPE": "x11"})
    assert xd.session_problem({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0", "WAYLAND_DISPLAY": "x"}) is None
    assert "DISPLAY" in xd.session_problem({"XDG_SESSION_TYPE": "x11"})
    assert xd.X11Desktop.open({"XDG_SESSION_TYPE": "wayland"}) is None and "XWayland" in xd.X11Desktop.why
    names = [xd.keysym_name(c) for c in parse_chord("ctrl+shift+a") + parse_chord("enter") + parse_chord("f5")
             + parse_chord("up") + parse_chord("esc") + parse_chord("7")]
    assert names == ["Control_L", "Shift_L", "a", "Return", "F5", "Up", "Escape", "7"]


class FakeX:
    """Stands in for libX11 / libXtst: records the input, serves one 2x2 screen and the active window's pid."""

    def __init__(self):
        self.events, self.freed = [], []
        self.pixels = ctypes.create_string_buffer(bytes([255, 0, 0, 0, 0, 255, 0, 0, 0, 0, 255, 0, 9, 9, 9, 0]
                                                        + [0] * 8))      # 2 rows of 12 bytes (padded stride)
        self.image = xd._XImage(width=2, height=2, data=ctypes.cast(self.pixels, ctypes.c_void_p).value,
                                byte_order=0, depth=24, bytes_per_line=12, bits_per_pixel=32)
        self.props = {}

    def XDefaultRootWindow(self, d):
        return 1

    def XDefaultScreen(self, d):
        return 0

    def XDisplayWidth(self, d, s):
        return 2

    def XDisplayHeight(self, d, s):
        return 2

    def XGetImage(self, *a):
        return ctypes.addressof(self.image)

    def XFree(self, p):
        self.freed.append(p)

    def XInternAtom(self, d, name, only):
        return {b"_NET_ACTIVE_WINDOW": 10, b"_NET_WM_PID": 11}[name]

    def XGetWindowProperty(self, d, window, atom, *rest):
        value = {(1, 10): 77, (77, 11): 4242}.get((window, atom))
        if value is None:
            return 1
        buf = (ctypes.c_ulong * 1)(value)
        self.props[atom] = buf
        _, _, _, _, _, fmt, nitems, _, prop = rest      # offset, length, delete, type, then the out values
        fmt._obj.value, nitems._obj.value, prop._obj.value = 32, 1, ctypes.addressof(buf)
        return 0

    def XStringToKeysym(self, name):
        return {b"Return": 0xFF0D, b"Alt_L": 0xFFE9, b"t": 0x74}.get(name, 0)

    def XKeysymToKeycode(self, d, sym):
        return {0xFF0D: 36, 0xFFE9: 64, 0x74: 28}.get(sym, 0)

    def XFlush(self, d):
        self.events.append("flush")

    def XTestFakeKeyEvent(self, d, code, down, delay):
        self.events.append(("key", code, down))

    def XTestFakeMotionEvent(self, d, screen, x, y, delay):
        self.events.append(("to", x, y))

    def XTestFakeButtonEvent(self, d, button, down, delay):
        self.events.append(("button", button, down))

    # XWayland mode: the active window 77 is at (500, 300) on the X screen; focus on its child 78 (or elsewhere)
    focus = 78
    parents = {78: 77, 77: 1, 90: 1}

    def XGetGeometry(self, d, window, root, x, y, w, h, border, depth):
        w._obj.value, h._obj.value = 2, 2
        self.geometry_of = window
        return 1

    def XTranslateCoordinates(self, d, src, dst, sx, sy, rx, ry, child):
        rx._obj.value, ry._obj.value = 500, 300
        return 1

    def XGetInputFocus(self, d, focus, revert):
        focus._obj.value = self.focus

    def XQueryTree(self, d, window, root, parent, children, n):
        if window not in self.parents:
            return 0
        parent._obj.value = self.parents[window]
        return 1


def test_x11_desktop_with_a_fake_xlib(tmp_path):
    x = FakeX()
    desk = xd.X11Desktop(x, x, 123)
    assert desk.foreground_pid() == 4242
    desk.press(parse_chord("alt+t"))
    assert x.events == [("key", 64, True), ("key", 28, True), ("key", 28, False), ("key", 64, False), "flush"]
    x.events.clear()
    desk.mouse_to(300, 200)
    desk.mouse_button(True)
    desk.mouse_button(False)
    assert [e for e in x.events if e != "flush"] == [("to", 300, 200), ("button", 1, True), ("button", 1, False)]
    try:
        desk.press(parse_chord("f9"))
        raise AssertionError("an unknown key must not be sent")
    except OSError as e:
        assert "F9" in str(e)
    assert len(x.freed) == 2                       # both window properties given back
    x.freed.clear()
    assert desk.screenshot(tmp_path / "s.png") == (2, 2)
    png = (tmp_path / "s.png").read_bytes()
    idat = png[png.index(b"IDAT") + 4:]
    rows = zlib.decompress(idat[:struct.unpack(">I", png[png.index(b"IDAT") - 4:png.index(b"IDAT")])[0]])
    assert rows == b"\x00" + bytes([0, 0, 255, 0, 255, 0]) + b"\x00" + bytes([9, 9, 9, 0, 0, 0])  # BGRX -> RGB, row padding left out
    assert len(x.freed) == 2                       # the pixels and the image are given back to Xlib


def test_linux_panel_needs_x11_and_offers_tesseract(tmp_path):
    from PySide6.QtWidgets import QApplication
    import sys
    QApplication.instance() or QApplication(sys.argv)
    from tests.test_frame_capture_ui import make_panel
    p = make_panel(tmp_path, platform_name="linux")
    spec = SimpleNamespace(game_name="SOTTR", steps=[{"see": "x"}], start_keys=[])
    p.x11_check = lambda: "this is a Wayland session, ..."
    assert not p.linux_steps_ready(spec) and "Wayland" in p.status_label.text()
    p.x11_check = lambda: None
    offered = []
    p.install_tesseract = lambda which=None: offered.append(1)
    p.linux_which = lambda name: None
    assert not p.linux_steps_ready(spec) and offered == [1] and "Tesseract" in p.status_label.text()
    p.linux_which = lambda name: "/usr/bin/" + name
    assert p.linux_steps_ready(spec)
    assert p.linux_steps_ready(SimpleNamespace(game_name="G", steps=[], start_keys=[(5, "enter")]))


def test_xwayland_mode_uses_the_game_window_and_needs_its_focus(tmp_path):
    x = FakeX()
    desk = xd.X11Desktop(x, x, 123, xwayland=True)
    assert desk.foreground_pid() == 4242               # the X focus is on a child of the active window
    x.focus = 90                                       # another X window has the keyboard
    assert desk.foreground_pid() is None
    for gone in (0, 1):                                # None / PointerRoot: a Wayland window is in front
        x.focus = gone
        assert desk.foreground_pid() is None
    assert desk.screenshot(tmp_path / "w.png") == (2, 2) and x.geometry_of == 77 and desk.origin == (500, 300)
    x.events.clear()
    desk.mouse_to(10, 20)
    assert x.events[0] == ("to", 510, 320)             # a word in the window's picture, moved to the X screen
    plain = xd.X11Desktop(x, x, 123)
    plain.screenshot(tmp_path / "s.png")
    x.events.clear()
    plain.mouse_to(10, 20)
    assert plain.origin == (0, 0) and x.events[0] == ("to", 10, 20)


def test_xwayland_screenshot_without_an_active_window(tmp_path):
    x = FakeX()
    x.XGetWindowProperty = lambda *a: 1                 # no _NET_ACTIVE_WINDOW
    try:
        xd.X11Desktop(x, x, 123, xwayland=True).screenshot(tmp_path / "w.png")
        raise AssertionError("no window must not give a picture")
    except OSError as e:
        assert "not in front" in str(e)


def test_linux_text_mentions_xwayland_on_wayland(tmp_path):
    from PySide6.QtWidgets import QApplication
    import sys
    QApplication.instance() or QApplication(sys.argv)
    from tests.test_frame_capture_ui import make_panel
    p = make_panel(tmp_path, platform_name="linux")
    spec = SimpleNamespace(game_name="SOTTR", app_id="750920", launch_args=[], settle_seconds=5, seconds=60,
                           warmup_runs=0, repetitions=3, wait_seconds=lambda: 200.0, steps=[{"see": "x"}],
                           start_keys=[])
    p.session_env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}
    assert "XWayland" in p.linux_text(spec, SimpleNamespace(name="p"), 180, {})
    p.session_env = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}
    assert "XWayland" not in p.linux_text(spec, SimpleNamespace(name="p"), 180, {})
    spec.steps, spec.route = [], {"loop_from": 5, "loop_seconds": 10, "loop": [[0, "hold", "w", 4]]}
    text = p.linux_text(spec, SimpleNamespace(name="p"), 180, {})
    assert "Route (the app plays the game" in text and "hold w 4 s" in text and "draft" in text

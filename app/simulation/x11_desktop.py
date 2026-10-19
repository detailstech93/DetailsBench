"""The Linux desktop for menu steps and start keys (owner, 2026-10-19: menu steps on Linux): an X11 session.

Everything goes through the X libraries every X11 desktop already has, called with ctypes, so nothing is
installed: libX11 (XGetImage of the root window = the whole screen as shown, the active window from the window
manager's _NET_ACTIVE_WINDOW and its _NET_WM_PID) and libXtst (the XTEST extension: XTestFakeKeyEvent,
XTestFakeButtonEvent, XTestFakeMotionEvent, the documented way to send test input to the X server).

A Proton game is an X11 program (Wine's X11 driver sets _NET_WM_PID to the game's own process), so the same check
as on Windows holds: a key goes out only while the active window belongs to a process the launch started.

Wayland sessions (owner, 2026-10-19: look into Wayland): a Proton game still runs as an X11 program, under
XWayland, so the app works with the game's own X window there instead of the screen ("xwayland" mode):
- the screenshot is the game's window (XGetImage of that window; the root window of XWayland is not the screen),
  and a click on a word is moved by the window's place on the X screen (XTranslateCoordinates);
- XTEST works between the X11 programs inside XWayland (Xwayland is a Wayland client: its XTEST does not move the
  real pointer, but X11 programs get the events; xorg/xserver MR 465). Newer KWin can send XWayland's XTEST through
  the remote desktop portal (libei; KWin MR 5619): the desktop may then ask once to allow it;
- a key goes out only while the game's X window is the active one (_NET_ACTIVE_WINDOW, as on X11) AND has the X
  input focus (XGetInputFocus; with a Wayland window in front XWayland's focus is not on the game), so a key can
  never go to a Wayland window. A game running as a native Wayland program (PROTON_ENABLE_WAYLAND) has no X window:
  no key is sent and the run says the game was not in front.
Not checked on a real Wayland desktop yet. Xlib is not thread safe: every call holds one lock (the step player
runs in its own thread).
"""

import ctypes
import ctypes.util
import os
import threading
from typing import List, Optional, Tuple

from app.simulation.desktop import VK, write_png

# parse_chord's Windows virtual-key codes -> X keysym names (X11's keysymdef.h names)
KEYSYMS = {VK["alt"]: "Alt_L", VK["ctrl"]: "Control_L", VK["shift"]: "Shift_L", VK["enter"]: "Return",
           VK["esc"]: "Escape", VK["tab"]: "Tab", VK["space"]: "space", VK["up"]: "Up", VK["down"]: "Down",
           VK["left"]: "Left", VK["right"]: "Right", VK["backspace"]: "BackSpace", VK["home"]: "Home",
           VK["end"]: "End"}
KEYSYMS.update({VK[f"f{i}"]: f"F{i}" for i in range(1, 13)})
ZPIXMAP = 2
ALL_PLANES = 0xFFFFFFFF


def keysym_name(code: int) -> Optional[str]:
    if code in KEYSYMS:
        return KEYSYMS[code]
    if ord("A") <= code <= ord("Z"):
        return chr(code).lower()
    if ord("0") <= code <= ord("9"):
        return chr(code)
    return None


def is_wayland(env=None) -> bool:
    env = os.environ if env is None else env
    kind = (env.get("XDG_SESSION_TYPE") or "").lower()
    return kind == "wayland" or bool(env.get("WAYLAND_DISPLAY") and kind != "x11")


def session_problem(env=None) -> Optional[str]:
    """Why menu steps cannot run in this Linux session, or None (a Wayland session with XWayland is usable)."""
    env = os.environ if env is None else env
    if not env.get("DISPLAY"):
        if is_wayland(env):
            return ("this Wayland session has no XWayland (DISPLAY is not set), which the app needs to read the "
                    "game's window and send it keys; turn XWayland on, or log in to an X11 session")
        return "no X11 display (DISPLAY is not set)"
    return None


class _XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
                ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int)]


class X11Desktop:
    """The real X11 desktop. open() returns None when it cannot be used (the reason in .why)."""
    why: Optional[str] = None

    def __init__(self, x11, xtst, display, xwayland: bool = False):
        self.x, self.xtst, self.d = x11, xtst, display
        self.root = x11.XDefaultRootWindow(display)
        self.lock = threading.Lock()
        self._atoms = {}
        self.xwayland = xwayland
        self.origin = (0, 0)            # where the last screenshot's top left is on the X screen

    @classmethod
    def open(cls, env=None) -> "Optional[X11Desktop]":
        problem = session_problem(env)
        if problem:
            cls.why = problem
            return None
        try:
            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xtst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        except OSError as e:
            cls.why = f"the X11 libraries could not be loaded ({e}); libXtst is in the libxtst6 / libXtst package"
            return None
        _types(x11, xtst)
        display = x11.XOpenDisplay(None)
        if not display:
            cls.why = "the X11 display could not be opened"
            return None
        n = [ctypes.c_int() for _ in range(4)]
        if not xtst.XTestQueryExtension(display, *[ctypes.byref(i) for i in n]):
            x11.XCloseDisplay(display)
            cls.why = "the X server has no XTEST extension (needed to send keys)"
            return None
        cls.why = None
        return cls(x11, xtst, display, xwayland=is_wayland(env))

    # the screen
    def size(self) -> Tuple[int, int]:
        with self.lock:
            screen = self.x.XDefaultScreen(self.d)
            return self.x.XDisplayWidth(self.d, screen), self.x.XDisplayHeight(self.d, screen)

    def _window_box(self, window: int) -> Tuple[int, int, int, int]:
        """(x, y on the X screen, width, height) of a window."""
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        x, y, rx, ry = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        w, h, border, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if not self.x.XGetGeometry(self.d, window, ctypes.byref(root), ctypes.byref(x), ctypes.byref(y),
                                   ctypes.byref(w), ctypes.byref(h), ctypes.byref(border), ctypes.byref(depth)):
            raise OSError("the game's window size could not be read")
        self.x.XTranslateCoordinates(self.d, window, self.root, 0, 0, ctypes.byref(rx), ctypes.byref(ry),
                                     ctypes.byref(child))
        return rx.value, ry.value, w.value, h.value

    def screenshot(self, path) -> Tuple[int, int]:
        """X11: the whole screen. XWayland: the active window (the game's), whose place is kept for clicks."""
        if self.xwayland:
            with self.lock:
                window = self._cardinal(self.root, "_NET_ACTIVE_WINDOW")
                if not window:
                    raise OSError("no X11 window is active (the game is not in front, or it runs as a Wayland "
                                  "program)")
                x, y, width, height = self._window_box(window)
            source, self.origin = window, (x, y)
        else:
            width, height = self.size()
            source, self.origin = self.root, (0, 0)
        with self.lock:
            ptr = self.x.XGetImage(self.d, source, 0, 0, width, height, ALL_PLANES, ZPIXMAP)
            if not ptr:
                raise OSError("XGetImage returned nothing")
            try:
                img = ctypes.cast(ptr, ctypes.POINTER(_XImage)).contents
                if img.bits_per_pixel != 32 or img.byte_order != 0:          # 0: LSBFirst
                    raise OSError(f"{img.bits_per_pixel}-bit screen (byte order {img.byte_order}) not supported; "
                                  "a 32-bit little-endian screen is expected")
                stride = img.bytes_per_line
                raw = ctypes.string_at(img.data, stride * height)
            finally:
                img_data = ctypes.cast(ptr, ctypes.POINTER(_XImage)).contents.data
                if img_data:
                    self.x.XFree(ctypes.c_void_p(img_data))
                self.x.XFree(ptr)
        rows = raw if stride == width * 4 else b"".join(raw[y * stride:y * stride + width * 4]
                                                        for y in range(height))
        write_png(path, width, height, rows)            # 32-bit ZPixmap on little-endian X: B, G, R, unused
        return width, height

    # the active window
    def _atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.x.XInternAtom(self.d, name.encode("ascii"), True)
        return self._atoms[name]

    def _cardinal(self, window: int, name: str) -> Optional[int]:
        atom = self._atom(name)
        if not atom:
            return None
        actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
        nitems, after, prop = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = self.x.XGetWindowProperty(self.d, window, atom, 0, 1, False, 0, ctypes.byref(actual_type),
                                           ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(after),
                                           ctypes.byref(prop))
        try:
            if status != 0 or not prop.value or nitems.value < 1 or actual_format.value != 32:
                return None
            return int(ctypes.cast(prop, ctypes.POINTER(ctypes.c_ulong))[0])   # format 32 is stored as long
        finally:
            if prop.value:
                self.x.XFree(prop)

    def _focus_inside(self, window: int) -> bool:
        """The X input focus is on the window or one of its children (Wine may focus a child window)."""
        focus, revert = ctypes.c_ulong(), ctypes.c_int()
        self.x.XGetInputFocus(self.d, ctypes.byref(focus), ctypes.byref(revert))
        current = focus.value
        for _ in range(32):
            if current == window:
                return True
            if current in (0, 1, self.root):            # None, PointerRoot, or the root: not the game
                return False
            root, parent, children, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
            if not self.x.XQueryTree(self.d, current, ctypes.byref(root), ctypes.byref(parent),
                                     ctypes.byref(children), ctypes.byref(n)):
                return False
            if children.value:
                self.x.XFree(children)
            current = parent.value
        return False

    def foreground_pid(self) -> Optional[int]:
        with self.lock:
            window = self._cardinal(self.root, "_NET_ACTIVE_WINDOW")
            if not window:
                return None
            if self.xwayland and not self._focus_inside(window):
                return None             # a Wayland window is in front: XWayland's focus is elsewhere
            return self._cardinal(window, "_NET_WM_PID")

    # input
    def _keycode(self, code: int) -> int:
        name = keysym_name(code)
        sym = self.x.XStringToKeysym(name.encode("ascii")) if name else 0
        keycode = self.x.XKeysymToKeycode(self.d, sym) if sym else 0
        if not keycode:
            raise OSError(f"key {name or code} is not on this keyboard layout")
        return keycode

    def _keys(self, codes: List[int], down: bool) -> None:
        for code in (codes if down else list(reversed(codes))):
            self.xtst.XTestFakeKeyEvent(self.d, self._keycode(code), down, 0)
        self.x.XFlush(self.d)

    def press(self, codes: List[int]) -> None:
        with self.lock:
            keycodes = [self._keycode(c) for c in codes]       # all known before any key goes down
            for k in keycodes:
                self.xtst.XTestFakeKeyEvent(self.d, k, True, 0)
            for k in reversed(keycodes):
                self.xtst.XTestFakeKeyEvent(self.d, k, False, 0)
            self.x.XFlush(self.d)

    def key_down(self, codes: List[int]) -> None:
        with self.lock:
            self._keys(codes, True)

    def key_up(self, codes: List[int]) -> None:
        with self.lock:
            self._keys(codes, False)

    def mouse_to(self, x: int, y: int) -> None:
        """A point of the last screenshot (XWayland: of the game's window, moved by its place)."""
        with self.lock:
            self.xtst.XTestFakeMotionEvent(self.d, -1, int(x) + self.origin[0], int(y) + self.origin[1], 0)
            self.x.XFlush(self.d)

    def mouse_move(self, dx: int, dy: int) -> None:
        with self.lock:
            self.xtst.XTestFakeRelativeMotionEvent(self.d, int(dx), int(dy), 0)
            self.x.XFlush(self.d)

    def mouse_button(self, down: bool) -> None:
        with self.lock:
            self.xtst.XTestFakeButtonEvent(self.d, 1, bool(down), 0)
            self.x.XFlush(self.d)


def _types(x, t) -> None:
    vp, ul, c_int, c_uint, c_long = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint, ctypes.c_long
    x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
    x.XCloseDisplay.argtypes = [vp]
    x.XDefaultRootWindow.restype, x.XDefaultRootWindow.argtypes = ul, [vp]
    x.XDefaultScreen.argtypes = [vp]
    x.XDisplayWidth.argtypes = x.XDisplayHeight.argtypes = [vp, c_int]
    x.XGetImage.restype = vp
    x.XGetImage.argtypes = [vp, ul, c_int, c_int, c_uint, c_uint, ul, c_int]
    x.XFree.argtypes = [vp]
    x.XInternAtom.restype, x.XInternAtom.argtypes = ul, [vp, ctypes.c_char_p, ctypes.c_bool]
    x.XGetWindowProperty.argtypes = [vp, ul, ul, c_long, c_long, ctypes.c_bool, ul, ctypes.POINTER(ul),
                                     ctypes.POINTER(c_int), ctypes.POINTER(ul), ctypes.POINTER(ul),
                                     ctypes.POINTER(vp)]
    x.XStringToKeysym.restype, x.XStringToKeysym.argtypes = ul, [ctypes.c_char_p]
    x.XKeysymToKeycode.restype, x.XKeysymToKeycode.argtypes = ctypes.c_ubyte, [vp, ul]
    x.XFlush.argtypes = [vp]
    ptr = ctypes.POINTER
    x.XGetGeometry.argtypes = [vp, ul, ptr(ul), ptr(c_int), ptr(c_int), ptr(c_uint), ptr(c_uint), ptr(c_uint),
                               ptr(c_uint)]
    x.XTranslateCoordinates.argtypes = [vp, ul, ul, c_int, c_int, ptr(c_int), ptr(c_int), ptr(ul)]
    x.XGetInputFocus.argtypes = [vp, ptr(ul), ptr(c_int)]
    x.XQueryTree.argtypes = [vp, ul, ptr(ul), ptr(ul), ptr(vp), ptr(c_uint)]
    t.XTestQueryExtension.argtypes = [vp] + [ctypes.POINTER(c_int)] * 4
    t.XTestFakeKeyEvent.argtypes = [vp, c_uint, ctypes.c_bool, ul]
    t.XTestFakeButtonEvent.argtypes = [vp, c_uint, ctypes.c_bool, ul]
    t.XTestFakeMotionEvent.argtypes = [vp, c_int, c_int, c_int, ul]
    t.XTestFakeRelativeMotionEvent.argtypes = [vp, c_int, c_int, ul]

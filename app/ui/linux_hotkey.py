"""Ctrl+Shift+X while a game is in front, on Linux (owner, 2026-11-19: it stopped Windows game runs; "the same on Linux").

Wayland gives no app the keys of another window; the desktop's GlobalShortcuts portal does (KDE Plasma 6, GNOME 48+):
the app asks for a shortcut, the desktop asks the owner once to allow it, then tells the app when it is pressed,
whatever window is in front. Only while a test runs, like the Windows hotkey: the portal session opens at the start
and closes at the end, so the keys are free the rest of the time.

PySide6's QtDBus cannot write the portal's a(sa{sv}) argument (probe on Fedora 45, 2026-11-19), and nothing may be
added to the venv, so this speaks the D-Bus wire protocol itself: only what these few calls need.
Portal API: flatpak.github.io/xdg-desktop-portal, org.freedesktop.portal.GlobalShortcuts (version 2 on Fedora 45).
"""

import logging
import os
import socket
import struct
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

APP_ID = "detailsbench"                      # ~/.local/share/applications/detailsbench.desktop
SHORTCUT_ID = "emergency-stop"
TRIGGER = "CTRL+SHIFT+x"                     # the portal's shortcut format (XDG shortcuts spec)
PORTAL = ("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
TIMEOUT = 10.0

# ---------------------------------------------------------------- D-Bus wire format (little endian)

ALIGN = {"y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8, "d": 8, "s": 4, "o": 4, "g": 1,
         "a": 4, "(": 8, "{": 8, "v": 1, "h": 4}
FIXED = {"y": "B", "b": "I", "n": "h", "q": "H", "i": "i", "u": "I", "x": "q", "t": "Q", "d": "d", "h": "I"}


def _one(sig: str, i: int = 0) -> int:
    """The end of the single complete type starting at sig[i]."""
    c = sig[i]
    if c == "a":
        return _one(sig, i + 1)
    if c in "({":
        close, depth = {"(": ")", "{": "}"}[c], 0
        for j in range(i, len(sig)):
            depth += sig[j] == c
            depth -= sig[j] == close
            if depth == 0:
                return j + 1
    return i + 1


def split(sig: str) -> list:
    out, i = [], 0
    while i < len(sig):
        j = _one(sig, i)
        out.append(sig[i:j])
        i = j
    return out


class Variant:
    """A D-Bus variant: a value with its own signature."""

    def __init__(self, sig: str, value):
        self.sig, self.value = sig, value

    def __eq__(self, other):
        return isinstance(other, Variant) and (self.sig, self.value) == (other.sig, other.value)

    def __repr__(self):
        return f"Variant({self.sig!r}, {self.value!r})"


def _pad(buf: bytearray, n: int):
    buf.extend(b"\0" * (-len(buf) % n))


def write(buf: bytearray, sig: str, value):
    c = sig[0]
    _pad(buf, ALIGN[c])
    if c in FIXED:
        buf.extend(struct.pack("<" + FIXED[c], value))
    elif c in "so":
        data = value.encode()
        buf.extend(struct.pack("<I", len(data)) + data + b"\0")
    elif c == "g":
        data = value.encode()
        buf.extend(bytes([len(data)]) + data + b"\0")
    elif c == "v":
        write(buf, "g", value.sig)
        write(buf, value.sig, value.value)
    elif c == "(":
        for part, v in zip(split(sig[1:-1]), value):
            write(buf, part, v)
    elif c == "a":
        inner = sig[1:]
        at = len(buf)
        buf.extend(b"\0\0\0\0")
        _pad(buf, ALIGN[inner[0]])
        start = len(buf)
        items = value.items() if inner[0] == "{" else value
        for item in items:
            if inner[0] == "{":
                _pad(buf, 8)
                k, v = split(inner[1:-1])
                write(buf, k, item[0])
                write(buf, v, item[1])
            else:
                write(buf, inner, item)
        struct.pack_into("<I", buf, at, len(buf) - start)
    else:
        raise ValueError(f"D-Bus type {c!r} not handled")


def read(data: bytes, pos: int, sig: str):
    """(value, next position)."""
    c = sig[0]
    pos += -pos % ALIGN[c]
    if c in FIXED:
        f = "<" + FIXED[c]
        return struct.unpack_from(f, data, pos)[0], pos + struct.calcsize(f)
    if c in "so":
        n = struct.unpack_from("<I", data, pos)[0]
        return data[pos + 4:pos + 4 + n].decode("utf-8", "replace"), pos + 5 + n
    if c == "g":
        n = data[pos]
        return data[pos + 1:pos + 1 + n].decode(), pos + 2 + n
    if c == "v":
        inner, pos = read(data, pos, "g")
        value, pos = read(data, pos, inner)
        return Variant(inner, value), pos
    if c == "(":
        out = []
        for part in split(sig[1:-1]):
            v, pos = read(data, pos, part)
            out.append(v)
        return tuple(out), pos
    if c == "a":
        n = struct.unpack_from("<I", data, pos)[0]
        inner = sig[1:]
        pos += 4
        pos += -pos % ALIGN[inner[0]]
        end = pos + n
        if inner[0] == "{":
            k, v = split(inner[1:-1])
            out = {}
            while pos < end:
                pos += -pos % 8
                key, pos = read(data, pos, k)
                out[key], pos = read(data, pos, v)
            return out, pos
        items = []
        while pos < end:
            item, pos = read(data, pos, inner)
            items.append(item)
        return items, pos
    raise ValueError(f"D-Bus type {c!r} not handled")


def message(kind: int, serial: int, fields: dict, sig: str = "", args=()) -> bytes:
    """kind 1 = method call. fields: {code: (signature, value)}."""
    body = bytearray()
    for part, value in zip(split(sig), args):
        write(body, part, value)
    if sig:
        fields = {**fields, 8: ("g", sig)}
    head = bytearray(b"l" + bytes([kind, 0, 1]))
    head.extend(struct.pack("<II", len(body), serial))
    write(head, "a(yv)", [(code, Variant(*fields[code])) for code in sorted(fields)])
    _pad(head, 8)
    return bytes(head + body)


def parse(data: bytes):
    """(kind, header fields {code: value}, body values, total length) of one message, or None when incomplete."""
    if len(data) < 16:
        return None
    if data[:1] != b"l":
        raise ValueError("big-endian D-Bus messages are not handled")
    body_len, _ = struct.unpack_from("<II", data, 4)
    fields_len = struct.unpack_from("<I", data, 12)[0]
    head_end = 16 + fields_len
    head_end += -head_end % 8
    if len(data) < head_end + body_len:
        return None
    fields, _ = read(data, 12, "a(yv)")
    fields = {code: v.value for code, v in fields}
    sig, pos, body = fields.get(8, ""), head_end, []
    for part in split(sig):
        value, pos = read(data, pos, part)
        body.append(value)
    return data[1], fields, body, head_end + body_len


class Bus:
    """One connection to the session bus: calls wait for their reply; signals go to on_signal (reader thread)."""

    def __init__(self, on_signal: Callable[[dict, list], None], address: Optional[str] = None,
                 connect: Callable[[str], socket.socket] = None):
        address = address or os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path=/run/user/{os.getuid()}/bus"
        path = next((p[5:] for p in address.split(":", 1)[1].split(",") if p.startswith("path=")), None)
        if path is None:
            raise OSError(f"session bus address not handled: {address}")
        self.sock = (connect or _connect)(path)
        self.on_signal = on_signal
        self.serial, self.replies, self.cond = 0, {}, threading.Condition()
        self.sock.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n")
        if not self._line().startswith(b"OK"):
            raise OSError("the session bus refused the connection")
        self.sock.sendall(b"BEGIN\r\n")
        self.buffer = b""
        threading.Thread(target=self._read, daemon=True, name="dbus-reader").start()
        self.name = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello")[0]

    def _line(self) -> bytes:
        out = b""
        while not out.endswith(b"\r\n"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise OSError("the session bus closed the connection")
            out += chunk
        return out

    def _read(self):
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                self.buffer += chunk
                while True:
                    got = parse(self.buffer)
                    if got is None:
                        break
                    kind, fields, body, size = got
                    self.buffer = self.buffer[size:]
                    if kind in (2, 3):
                        with self.cond:
                            self.replies[fields.get(5)] = (kind, fields, body)
                            self.cond.notify_all()
                    elif kind == 4:
                        try:
                            self.on_signal(fields, body)
                        except Exception:
                            logger.exception("D-Bus signal handler")
        except OSError:
            pass
        with self.cond:
            self.replies[None] = (3, {4: "disconnected"}, [])
            self.cond.notify_all()

    def call(self, dest, path, iface, member, sig="", args=(), timeout=TIMEOUT) -> list:
        with self.cond:
            self.serial += 1
            serial = self.serial
        self.sock.sendall(message(1, serial, {1: ("o", path), 2: ("s", iface), 3: ("s", member), 6: ("s", dest)},
                                  sig, args))
        with self.cond:
            if not self.cond.wait_for(lambda: serial in self.replies or None in self.replies, timeout):
                raise TimeoutError(f"{member}: no answer from {dest}")
            kind, fields, body = self.replies.pop(serial, None) or self.replies[None]
        if kind == 3:
            raise OSError(f"{member}: {fields.get(4)}: {body[0] if body else ''}")
        return body

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _connect(path: str) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(None)
    s.connect(path)
    return s


# ---------------------------------------------------------------- the shortcut


class PortalShortcut:
    """open(): asks the desktop for Ctrl+Shift+X (it asks the owner the first time); on_press() runs, on the reader
    thread, at each press while open; close() gives the keys back. Never raises: why() says what did not work."""

    def __init__(self, on_press: Callable[[], None], bus_factory=None, timeout: float = 60.0):
        self.on_press = on_press
        self.bus_factory = bus_factory or (lambda on_signal: Bus(on_signal))
        self.timeout = timeout
        self.bus = self.session = None
        self.problem: Optional[str] = None
        self.responses, self.cond, self.count = {}, threading.Condition(), 0

    def _signal(self, fields: dict, body: list):
        member, iface = fields.get(3), fields.get(2)
        if iface == "org.freedesktop.portal.Request" and member == "Response":
            with self.cond:
                self.responses[fields.get(1)] = body
                self.cond.notify_all()
        elif iface == SHORTCUTS and member == "Activated" and body[:2] == [self.session, SHORTCUT_ID]:
            self.on_press()

    def _request(self, method: str, sig: str, args: list, token: str, timeout: float) -> dict:
        """A portal call answered by a Response signal on its Request object: its results, or raises."""
        path = self.bus.call(*PORTAL, SHORTCUTS, method, sig, args)[0]
        with self.cond:
            if not self.cond.wait_for(lambda: path in self.responses, timeout):
                raise TimeoutError(f"{method}: the desktop did not answer within {int(timeout)} s")
            code, results = self.responses.pop(path)
        if code != 0:
            raise PermissionError(f"{method}: {'refused' if code == 1 else 'failed'} on the desktop")
        return results

    def open(self) -> bool:
        if self.session is not None:
            return True
        try:
            self.bus = self.bus_factory(self._signal)
            for rule in ("type='signal',interface='org.freedesktop.portal.Request',member='Response'",
                         f"type='signal',interface='{SHORTCUTS}',member='Activated'"):
                self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch",
                              "s", [rule])
            try:            # the desktop then names the app in its dialog (portal 1.19+; harmless when missing)
                self.bus.call(*PORTAL, "org.freedesktop.host.portal.Registry", "Register", "sa{sv}", [APP_ID, {}])
            except OSError:
                pass
            self.count += 1
            token = f"detailsbench{os.getpid()}_{self.count}"
            got = self._request("CreateSession", "a{sv}", [{"handle_token": Variant("s", token),
                                                            "session_handle_token": Variant("s", token)}],
                                token, TIMEOUT)
            self.session = got["session_handle"].value
            bound = self._request("BindShortcuts", "oa(sa{sv})sa{sv}", [
                self.session,
                [(SHORTCUT_ID, {"description": Variant("s", "DetailsBench: emergency stop of the running test"),
                                "preferred_trigger": Variant("s", TRIGGER)})],
                "", {"handle_token": Variant("s", token + "b")}], token + "b", self.timeout)
            shortcuts = (bound.get("shortcuts") or Variant("a(sa{sv})", [])).value
            if not any(s[0] == SHORTCUT_ID for s in shortcuts):
                raise PermissionError("the desktop did not keep the shortcut")
            self.problem = None
            return True
        except Exception as e:
            self.problem = str(e) or type(e).__name__
            logger.info("Linux emergency-stop shortcut not available: %s", self.problem)
            self.close()
            return False

    def close(self):
        if self.bus is not None and self.session is not None:
            try:
                self.bus.call(PORTAL[0], self.session, "org.freedesktop.portal.Session", "Close")
            except Exception:
                pass
        if self.bus is not None:
            self.bus.close()
        self.bus = self.session = None


class X11Shortcut:
    """The same on an X11 session (no portal there): XGrabKey of Ctrl+Shift+X on the root window, so the X server
    sends it to this app whatever window has the keyboard. Same open / close / problem as PortalShortcut."""

    MASKS = (0, 2, 16, 18)                  # also with Caps Lock (LockMask 2) and Num Lock (Mod2Mask 16) on
    CONTROL_SHIFT, KEY_PRESS = 4 | 1, 2

    def __init__(self, on_press: Callable[[], None], x11=None):
        self.on_press, self.x11 = on_press, x11
        self.display, self.problem, self.stop = None, None, threading.Event()

    def open(self) -> bool:
        if self.display is not None:
            return True
        try:
            import ctypes
            import ctypes.util
            x = self.x11 or ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            vp, ul = ctypes.c_void_p, ctypes.c_ulong
            x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
            x.XDefaultRootWindow.restype, x.XDefaultRootWindow.argtypes = ul, [vp]
            x.XStringToKeysym.restype, x.XStringToKeysym.argtypes = ul, [ctypes.c_char_p]
            x.XKeysymToKeycode.restype, x.XKeysymToKeycode.argtypes = ctypes.c_ubyte, [vp, ul]
            x.XGrabKey.argtypes = [vp, ctypes.c_int, ctypes.c_uint, ul, ctypes.c_bool, ctypes.c_int, ctypes.c_int]
            x.XUngrabKey.argtypes = [vp, ctypes.c_int, ctypes.c_uint, ul]
            x.XPending.argtypes = x.XFlush.argtypes = x.XCloseDisplay.argtypes = [vp]
            x.XNextEvent.argtypes = [vp, vp]
            display = x.XOpenDisplay(None)
            if not display:
                raise OSError("the X11 display could not be opened")
            self.x11, self.display = x, display
            self.root = x.XDefaultRootWindow(display)
            self.code = x.XKeysymToKeycode(display, x.XStringToKeysym(b"x"))
            for extra in self.MASKS:
                x.XGrabKey(display, self.code, self.CONTROL_SHIFT | extra, self.root, True, 1, 1)   # GrabModeAsync
            x.XFlush(display)
            self.stop.clear()
            threading.Thread(target=self._events, daemon=True, name="x11-hotkey").start()
            self.problem = None
            return True
        except Exception as e:
            self.problem = str(e) or type(e).__name__
            self.display = None
            return False

    def _events(self):
        import ctypes
        import time
        event = ctypes.create_string_buffer(192)            # sizeof(XEvent); its first int is the type
        x, display = self.x11, self.display
        while not self.stop.is_set():
            while x.XPending(display):
                x.XNextEvent(display, event)
                if ctypes.c_int.from_buffer(event).value == self.KEY_PRESS:
                    self.on_press()
            time.sleep(0.1)
        for extra in self.MASKS:
            x.XUngrabKey(display, self.code, self.CONTROL_SHIFT | extra, self.root)
        x.XCloseDisplay(display)

    def close(self):
        if self.display is not None:
            self.stop.set()                 # the event thread gives the keys back and closes its display
            self.display = None


def shortcut_for(on_press: Callable[[], None], env=None):
    """The X11 grab on an X11 session, the desktop's portal otherwise (Wayland)."""
    env = os.environ if env is None else env
    return X11Shortcut(on_press) if env.get("XDG_SESSION_TYPE") == "x11" else PortalShortcut(on_press)

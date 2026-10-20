"""Drive a Chromium browser (Chrome, Edge, Chromium) through its DevTools protocol, for the web benchmarks.

Owner, 2026-10-20: run Speedometer and JetStream by themselves. The app starts the browser with a NEW, empty profile
of its own (a folder it creates and deletes afterwards: never the owner's profile, history, extensions or sign-ins)
and --remote-debugging-port=0: the browser picks a free port on 127.0.0.1 and writes it to DevToolsActivePort in
that profile (Chromium's documented behaviour). The app then only reads the page: Runtime.evaluate of a fixed
expression (the score element's text). Nothing is typed into a page and nothing is uploaded.

The WebSocket client is the small part of RFC 6455 the protocol needs (text frames, masked from the client,
ping/pong, close), so the app needs no extra library.
"""

import base64
import json
import ntpath
import os
import posixpath
import shutil
import socket
import struct
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

WINDOW = "1400,1000"            # Speedometer wants its 800 x 600 area + 50 px
BROWSER_FLAGS = ("--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-extensions",
                 f"--window-size={WINDOW}", "--new-window")


def browser_candidates(platform: Optional[str] = None, env=None) -> List[str]:
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform == "win32":
        out = []
        for base in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("LOCALAPPDATA")):
            if base:
                out.append(ntpath.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        for base in (env.get("ProgramFiles(x86)"), env.get("ProgramFiles")):
            if base:
                out.append(ntpath.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
        return out
    if platform.startswith("linux"):
        return ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"]
    return []


def find_browser(platform: Optional[str] = None, env=None, isfile=os.path.isfile,
                 which=shutil.which) -> Optional[str]:
    for c in browser_candidates(platform, env):
        if ntpath.isabs(c) or posixpath.isabs(c):     # a full path (Windows ones too), else a name on the PATH
            if isfile(c):
                return c
        else:
            found = which(c)
            if found:
                return found
    return None


def browser_command(exe: str, profile: Path, url: str) -> List[str]:
    return [exe, f"--user-data-dir={profile}", "--remote-debugging-port=0", *BROWSER_FLAGS, url]


def read_active_port(profile: Path) -> Optional[int]:
    try:
        first = (Path(profile) / "DevToolsActivePort").read_text(encoding="ascii").splitlines()[0].strip()
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    return int(first) if first.isdigit() and 0 < int(first) < 65536 else None


def http_json(port: int, path: str, opener=urllib.request.urlopen):
    with opener(f"http://127.0.0.1:{int(port)}{path}", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def page_socket_url(targets) -> Optional[str]:
    for t in targets or []:
        if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            url = t["webSocketDebuggerUrl"]
            host = urlparse(url).hostname
            if host in ("127.0.0.1", "localhost"):          # only the browser on this PC
                return url
    return None


# ---- WebSocket (RFC 6455, client side) --------------------------------------------------------------------------
def encode_frame(text: str, mask: bytes) -> bytes:
    data = text.encode("utf-8")
    head = bytes([0x81])                                       # FIN + text
    n = len(data)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))


class WebSocket:
    def __init__(self, url: str, connect=socket.create_connection, timeout: float = 10.0):
        u = urlparse(url)
        if u.scheme != "ws" or u.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError(f"not a local DevTools address: {url}")
        self.sock = connect((u.hostname, u.port or 80), timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode("ascii"))
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("the browser closed the connection")
            reply += chunk
        head, _, self._buf = reply.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise ConnectionError(f"no WebSocket: {status[:80]!r}")

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("the browser closed the connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str) -> None:
        self.sock.sendall(encode_frame(text, os.urandom(4)))

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            op = b0 & 0x0F
            if op == 0x8:
                raise ConnectionError("the browser closed the connection")
            if op == 0x9:                                       # ping: answer, keep reading
                self.sock.sendall(bytes([0x8A, 0x80 | len(data)]) + b"\0\0\0\0" + data)
                continue
            if op in (0x1, 0x0):
                parts.append(data)
                if b0 & 0x80:
                    return b"".join(parts).decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Page:
    """One page of the browser: evaluate() a fixed expression and get its value."""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    def evaluate(self, expression: str):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": "Runtime.evaluate",
                                 "params": {"expression": expression, "returnByValue": True}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") != self._id:
                continue                                        # an event, or an older answer
            if "error" in msg:
                raise RuntimeError(str(msg["error"].get("message") if isinstance(msg["error"], dict) else msg))
            result = (msg.get("result") or {}).get("result") or {}
            return result.get("value")


def wait_for(check: Callable[[], Optional[object]], timeout: float, stop=None, every: float = 0.5,
             clock=time.monotonic):
    """Call check() until it returns something not None (its value) or the time is up (None)."""
    end = clock() + timeout
    while clock() < end:
        value = check()
        if value is not None:
            return value
        if stop is not None and stop.wait(every):
            return None
        if stop is None:
            time.sleep(every)
    return None


def end_browser(proc) -> None:
    """Close the browser this run started (its own processes only)."""
    try:
        import psutil
        root = psutil.Process(proc.pid)
        family = root.children(recursive=True) + [root]
        for p in family:
            try:
                p.terminate()
            except psutil.Error:
                pass
        psutil.wait_procs(family, timeout=10)
    except Exception:
        try:
            proc.kill()
        except OSError:
            pass


def remove_profile(profile: Path, tries: int = 10) -> None:
    """Delete the profile folder this run created (the browser may hold files for a moment after it ends)."""
    for _ in range(tries):
        shutil.rmtree(profile, ignore_errors=True)
        if not Path(profile).exists():
            return
        time.sleep(0.5)

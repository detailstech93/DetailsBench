"""Remote start (plan Phase D): a small web server inside the app, so a phone or another computer on the same local
network can start tests and follow them.

- Off by default. The owner switches it on in the app ("Remote: OFF..." on the Tests screen, a switch); it never
  starts by itself and is off again when the app closes.
- Local network only: a client whose address is not private (10/8, 172.16/12, 192.168/16, 169.254/16, IPv6 ULA or
  link-local) or loopback is refused. "This PC only" binds to 127.0.0.1. No internet is needed or used, and the app
  never changes firewall rules (Windows may ask the owner to allow the app; the owner answers that prompt).
- One-time code: a new random code every time the server is switched on; every API call needs it (header X-Code).
  After MAX_WRONG wrong codes within a minute every call is refused for LOCK_SECONDS. A call with the right code
  also gets a cookie (HttpOnly, SameSite=Strict, random, new with every switch-on), so the reports open as normal
  pages (/report, /report/<name>) on the phone and inside the page.
- What the page can do: read the PC's specs and its tests (the ones that can start remotely can be ticked), start
  some of them with repetitions, warm-ups and the pause, follow the progress, press stop, read the reports of the
  reports folder. Nothing else: no file is written from a request, a report is picked only by a name the app lists,
  no command or program name comes from the page (a test is picked by its id, and only ids the app offers are
  accepted).

`api` is any object with info(), state(), start(ids, repetitions, warmup_runs, cooldown_seconds) -> (ok, text),
stop() -> text, reports() -> [{"name", "time"}] and report(name=None) -> Optional[str] (HTML). The app's one
(app/ui/remote_control.py) runs them on the Qt thread.
"""

import hmac
import http.cookies
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

from app.remote.page import PAGE

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8737
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O or 1/I: read off the screen, typed on a phone
CODE_LENGTH = 8
MAX_WRONG, LOCK_SECONDS = 5, 60
MAX_BODY = 16 * 1024
LIMITS = {"repetitions": (1, 50), "warmup_runs": (0, 10), "cooldown_seconds": (0, 3600)}
MAX_TESTS = 100
COOKIE = "pcbm"
PAGE_POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
               "img-src data:; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
REPORT_POLICY = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; "
                 "base-uri 'none'; form-action 'none'")      # a report has no script; the page may show it inside


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def allowed_client(address: str) -> bool:
    """Only this PC and the local network."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    return ip in ipaddress.ip_network("fc00::/7")


def local_addresses() -> List[str]:
    """This PC's IPv4 addresses on a local network (for the address the owner types on the phone)."""
    try:
        import psutil
        found = []
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and allowed_client(a.address) and \
                        not ipaddress.ip_address(a.address).is_loopback and \
                        not ipaddress.ip_address(a.address).is_link_local:
                    found.append(a.address)
        return sorted(set(found))
    except Exception:
        logger.exception("Could not list the network addresses")
        return []


def check_start(body: object, offered: List[str]) -> Tuple[Optional[dict], str]:
    """(the start request, "") or (None, why). Only test ids the app offers, numbers within LIMITS."""
    if not isinstance(body, dict):
        return None, "not a start request"
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_TESTS or not all(isinstance(i, str) for i in ids):
        return None, "pick at least one test"
    unknown = [i for i in ids if i not in offered]
    if unknown:
        return None, "these tests cannot start from here: " + ", ".join(unknown[:5])
    out = {"ids": list(dict.fromkeys(ids))}
    for key, (low, high) in LIMITS.items():
        value = body.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return None, f"{key} must be a whole number from {low} to {high}"
        out[key] = value
    return out, ""


class Guard:
    """The one-time code and the lock after wrong codes (one lock for every client: a guesser on another
    address must not get more tries)."""

    def __init__(self, code: str, clock=time.monotonic):
        self.code, self.clock = code, clock
        self.wrong: List[float] = []
        self.locked_until = 0.0
        self.lock = threading.Lock()

    def check(self, given: Optional[str]) -> Tuple[bool, str]:
        with self.lock:
            now = self.clock()
            if now < self.locked_until:
                return False, f"too many wrong codes; try again in {int(self.locked_until - now) + 1} s"
            if given and hmac.compare_digest(given.strip().upper().encode(), self.code.encode()):
                return True, ""
            self.wrong = [t for t in self.wrong if now - t < 60] + [now]
            if len(self.wrong) >= MAX_WRONG:
                self.locked_until, self.wrong = now + LOCK_SECONDS, []
                return False, f"too many wrong codes; locked for {LOCK_SECONDS} s"
            return False, "wrong code"


def _handler(server: "RemoteServer"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PCBenchmarkManager"
        sys_version = ""

        def log_message(self, fmt, *args):      # requests go to the app's log, never to a console
            logger.info("remote %s: " + fmt, self.client_address[0], *args)

        def _send(self, code: int, body: bytes, kind: str = "application/json; charset=utf-8",
                  policy: str = PAGE_POLICY):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN" if policy is REPORT_POLICY else "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", policy)
            if getattr(self, "set_cookie", False):
                self.send_header("Set-Cookie", f"{COOKIE}={server.token}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict):
            self._send(code, json.dumps(data).encode("utf-8"))

        def _allowed(self) -> bool:
            if not allowed_client(self.client_address[0]):
                self._json(403, {"error": "only this PC and the local network"})
                return False
            return True

        def _authorised(self) -> bool:
            ok, why = server.guard.check(self.headers.get("X-Code"))
            if not ok:
                self._json(401, {"error": why})
            self.set_cookie = ok
            return ok

        def _has_cookie(self) -> bool:
            try:
                jar = http.cookies.SimpleCookie(self.headers.get("Cookie") or "")
            except http.cookies.CookieError:
                return False
            given = jar[COOKIE].value if COOKIE in jar else ""
            return bool(given) and hmac.compare_digest(given.encode(), server.token.encode())

        def _report(self, path: str):
            """/report (the last one) or /report/<name> (one the app lists), as a normal page."""
            if not self._has_cookie():
                self._send(401, b"<!doctype html><meta charset=utf-8><p>Open the remote page and type the code "
                                b"first.</p>", "text/html; charset=utf-8", REPORT_POLICY)
                return
            name = urllib.parse.unquote(path[len("/report/"):]) if path.startswith("/report/") else None
            try:
                html = server.api.report(name)
            except Exception as e:
                logger.exception("Remote report failed")
                html, name = None, f"the app could not answer ({e})"
            if html is None:
                self._send(404, b"<!doctype html><meta charset=utf-8><p>No report yet.</p>",
                           "text/html; charset=utf-8", REPORT_POLICY)
            else:
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", REPORT_POLICY)

        def do_GET(self):
            if not self._allowed():
                return
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/report" or path.startswith("/report/"):
                self._report(path)
                return
            if not path.startswith("/api/"):
                self._json(404, {"error": "not found"})
                return
            if not self._authorised():
                return
            try:
                if path == "/api/info":
                    self._json(200, server.api.info())
                elif path == "/api/state":
                    self._json(200, server.api.state())
                elif path == "/api/reports":
                    self._json(200, {"reports": server.api.reports()})
                elif path == "/api/report":
                    html = server.api.report()
                    if html is None:
                        self._json(404, {"error": "no report yet"})
                    else:
                        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self._json(404, {"error": "not found"})
            except Exception as e:
                logger.exception("Remote request failed")
                self._json(500, {"error": f"the app could not answer ({e})"})

        def do_POST(self):
            if not self._allowed():
                return
            path = self.path.split("?")[0]
            if path not in ("/api/start", "/api/stop"):
                self._json(404, {"error": "not found"})
                return
            if not self._authorised():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                if MAX_BODY < length <= 64 * MAX_BODY:
                    self.rfile.read(length)     # read (and drop) it, else the client may see a reset, not the answer
                self.close_connection = True
                self._json(413, {"error": "request too large"})
                return
            raw = self.rfile.read(length) if length else b""
            try:
                if path == "/api/stop":
                    self._json(200, {"ok": True, "text": server.api.stop()})
                    return
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._json(400, {"error": "not JSON"})
                    return
                request, why = check_start(body, [t["id"] for t in server.api.info().get("tests", [])
                                                  if t.get("can_start", True)])
                if request is None:
                    self._json(400, {"error": why})
                    return
                ok, text = server.api.start(**request)
                self._json(200 if ok else 409, {"ok": ok, "text": text})
            except Exception as e:
                logger.exception("Remote request failed")
                self._json(500, {"error": f"the app could not answer ({e})"})

        def do_PUT(self):
            self._json(405, {"error": "not allowed"})

        do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    return Handler


class RemoteServer:
    def __init__(self, api, local_only: bool = False, port: int = DEFAULT_PORT, code: Optional[str] = None):
        self.api, self.local_only, self.port = api, local_only, port
        self.code = code or new_code()
        self.token = secrets.token_urlsafe(32)          # the cookie for the report pages
        self.guard = Guard(self.code)
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """Raises OSError when the port cannot be used (taken, or not allowed)."""
        host = "127.0.0.1" if self.local_only else "0.0.0.0"
        self.httpd = ThreadingHTTPServer((host, self.port), _handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="remote-start")
        self.thread.start()

    def running(self) -> bool:
        return self.httpd is not None

    def addresses(self) -> List[str]:
        hosts = ["127.0.0.1"] if self.local_only else local_addresses()
        return [f"http://{h}:{self.port}/" for h in hosts]

    def stop(self):
        httpd, self.httpd = self.httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

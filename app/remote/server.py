"""Remote start (plan Phase D): a small web server inside the app, so a phone or another computer on the same local
network can start tests and follow them.

- Off by default. The owner switches it on in the app ("Remote: OFF..." on the Tests screen, a switch); it never
  starts by itself and is off again when the app closes.
- Local network only: a client whose address is not private (10/8, 172.16/12, 192.168/16, 169.254/16, IPv6 ULA or
  link-local) or loopback is refused. Outside the home network only through Tailscale (owner, 2026-10-20): a client
  in Tailscale's range (100.64.0.0/10; its IPv6 fd7a:115c:a1e0::/48 is ULA) is let in only when it reached this
  PC's own Tailscale address, so the same range from an internet provider's shared addresses (CGNAT) is refused. "This PC only" binds to 127.0.0.1. No internet is needed or used, and the app
  never changes firewall rules (Windows may ask the owner to allow the app; the owner answers that prompt).
- One-time code: a new random code every time the server is switched on; every API call needs it (header X-Code).
  After MAX_WRONG wrong codes within a minute every call is refused for LOCK_SECONDS. A call with the right code
  also gets a cookie (HttpOnly, SameSite=Strict, random, new with every switch-on), so the reports open as normal
  pages (/report, /report/<name>) on the phone and inside the page.
- What the page can do: read the PC's specs and its tests (the ones that can start remotely can be ticked), start
  some of them with repetitions, warm-ups and the pause, follow the progress, press stop, read the reports of the
  reports folder, install a missing test or accept a test's license after reading the app's own question (only
  where the app offers it). Nothing else: no file is written from a request, a report is picked only by a name the
  app lists,
  no command or program name comes from the page (a test is picked by its id, and only ids the app offers are
  accepted).

`api` is any object with info(), state(), start(ids, repetitions, warmup_runs, cooldown_seconds) -> (ok, text),
stop() -> text, reports() -> [{"name", "time"}], report(name=None) -> Optional[str] (HTML), and question(id, what)
/ act(id, what) -> (ok, text) for "install" or "license" (only for tests whose info says so). The app's one
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
REQUEST_TIMEOUT = 20         # seconds a connection may stay silent (security review, 2026-10-21)
LIMITS = {"repetitions": (1, 50), "warmup_runs": (0, 10), "cooldown_seconds": (0, 3600)}
MAX_TESTS = 100
COOKIE = "pcbm"
PAGE_POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
               "img-src data:; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
REPORT_POLICY = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; "
                 "base-uri 'none'; form-action 'none'")      # a report has no script; the page may show it inside


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


TAILSCALE = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def _ip(address: Optional[str]):
    try:
        ip = ipaddress.ip_address(str(address).split("%")[0])
    except ValueError:
        return None
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def is_tailscale(address: Optional[str]) -> bool:
    ip = _ip(address)
    return ip is not None and any(ip.version == n.version and ip in n for n in TAILSCALE)


def allowed_client(address: str, local: Optional[str] = None) -> bool:
    """Only this PC, the local network and, through Tailscale, the owner's own devices. local: the address of this
    PC the request came to (a Tailscale client must have come to this PC's Tailscale address)."""
    ip = _ip(address)
    if ip is None:
        return False
    if is_tailscale(address) and isinstance(ip, ipaddress.IPv4Address):
        return is_tailscale(local)
    if ip.is_loopback or ip.is_link_local:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    return ip in ipaddress.ip_network("fc00::/7")


def local_addresses() -> List[str]:
    """This PC's IPv4 addresses on a local network and on Tailscale (for the address the owner types on the phone)."""
    try:
        import psutil
        found = []
        for name, addrs in psutil.net_if_addrs().items():
            if name == "lo":                # Linux loopback; under WSL it also holds 10.255.255.254 (its DNS)
                continue
            for a in addrs:
                if a.family == socket.AF_INET and allowed_client(a.address, a.address) and \
                        not ipaddress.ip_address(a.address).is_loopback and \
                        not ipaddress.ip_address(a.address).is_link_local:
                    found.append(a.address)
        return sorted(set(found))
    except Exception:
        logger.exception("Could not list the network addresses")
        return []


def wsl_note(addresses: List[str], wsl: bool) -> str:
    """Under WSL (its default NAT network) the app sees only WSL's own virtual network, not Windows' Tailscale or
    home network address, so the phone cannot reach what is listed (owner, 2026-10-20)."""
    if not wsl or any(is_tailscale(a.split("//")[-1].split(":")[0]) for a in addresses):
        return ""
    return ("Running inside WSL: these are WSL's own internal addresses, the phone cannot reach them (Windows' "
            "Tailscale address is not seen here). For Remote, run the app on Windows, or install Tailscale inside "
            "WSL, or turn on WSL's mirrored networking yourself (networkingMode=mirrored in .wslconfig).")


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
    minutes = body.get("stress_minutes")        # optional: the stress tests' length (none: the app's default)
    if minutes is not None:
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 240:
            return None, "stress_minutes must be a whole number from 1 to 240"
        out["stress_minutes"] = minutes
    chosen = body.get("game_presets")           # optional: {test id: preset} for the games (Games section)
    if chosen is not None:
        from app.games.presets import from_page
        if not isinstance(chosen, dict) or not set(chosen) <= set(out["ids"]):
            return None, "game_presets must name the games picked"
        presets = {test_id: from_page(p) for test_id, p in chosen.items()}
        if any(p is None for p in presets.values()):
            return None, "a game preset is not valid"
        out["game_presets"] = {test_id: p.to_dict() for test_id, p in presets.items()}
    shutdown = body.get("shutdown_after")       # optional: shut the PC down when the run ends (app/ui/shutdown_after.py)
    if shutdown is not None:
        if not isinstance(shutdown, bool):
            return None, "shutdown_after must be true or false"
        out["shutdown_after"] = shutdown
    return out, ""


def check_action(body: object, tests: List[dict]) -> Tuple[Optional[str], str]:
    """(test id, "install" or "license") for a test the app offers that action for, or (None, why)."""
    if not isinstance(body, dict) or body.get("what") not in ("install", "license", "install_many"):
        return None, "not an install or license request"
    test_id, what = body.get("id"), body["what"]
    if what == "install_many":          # several missing tests at once (owner, 2026-10-21): (list of ids, what)
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_TESTS or not all(isinstance(i, str) for i in ids):
            return None, "pick at least one test to install"
        can = {t.get("id") for t in tests if t.get("install")}
        if not set(ids) <= can:
            return None, "these tests have no install to do from here: " + ", ".join(sorted(set(ids) - can)[:5])
        return list(dict.fromkeys(ids)), what
    if not isinstance(test_id, str) or not any(t.get("id") == test_id and t.get(what) for t in tests):
        return None, f"this test has no {what} to do from here"
    return test_id, what


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
        timeout = REQUEST_TIMEOUT       # a client that opens a connection and sends nothing must not hold a thread

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
            try:
                local = self.connection.getsockname()[0]
            except OSError:
                local = None
            if not allowed_client(self.client_address[0], local):
                self._json(403, {"error": "only this PC, the local network and Tailscale"})
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
            if path not in ("/api/start", "/api/stop", "/api/skip", "/api/question", "/api/act"):
                self._json(404, {"error": "not found"})
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
            # the body is read before a 401 too: an unread body made Windows reset the connection (WinError 10053)
            raw = self.rfile.read(length) if length else b""
            if not self._authorised():
                return
            try:
                if path == "/api/stop":
                    self._json(200, {"ok": True, "text": server.api.stop()})
                    return
                if path == "/api/skip":
                    self._json(200, {"ok": True, "text": server.api.skip()})
                    return
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._json(400, {"error": "not JSON"})
                    return
                if path in ("/api/question", "/api/act"):
                    test_id, what = check_action(body, server.api.info().get("tests", []))
                    if test_id is None:
                        self._json(400, {"error": what})
                        return
                    ok, text = (server.api.question if path == "/api/question" else server.api.act)(test_id, what)
                    self._json(200 if ok else 409, {"ok": ok, "text": text})
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

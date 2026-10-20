"""Speedometer 3.1 and JetStream 3.0 through the browser's DevTools protocol, with a fake browser (no network)."""

import io
import json
import struct
import threading

import pytest

from app.automation.workload import RunContext
from app.web import benchmarks as wb
from app.web import cdp


def test_scores_from_the_page():
    assert wb.parse_score("31.4") == (31.4, None)
    assert wb.parse_score("1,234.5") == (1234.5, None)
    assert wb.parse_score("ERR:Errors were encountered") == (None, "Errors were encountered")
    assert wb.parse_score("Error")[0] is None
    assert wb.parse_score("")[0] is None and wb.parse_score(None)[0] is None
    assert wb.parse_score("0")[0] is None


def test_the_pages_start_by_themselves():
    from data.benchmark_definitions import DEFINITIONS_BY_ID, get_default_page_version
    for spec, name in ((wb.SPEEDOMETER, "Speedometer 3.1"), (wb.JETSTREAM, "JetStream 3.0")):
        page = get_default_page_version(DEFINITIONS_BY_ID[spec["id"]])
        assert spec["url"] == page["url"] + "?startAutomatically" and spec["name"] == name
    assert "_hasResults" in wb.SPEEDOMETER["read"]          # #summary is "valid" before the run (Chrome 150)
    assert "'done'" in wb.JETSTREAM["read"]


def test_browser_command_uses_an_own_profile_and_a_free_port(tmp_path):
    cmd = cdp.browser_command("chrome.exe", tmp_path, "https://x/")
    assert cmd[0] == "chrome.exe" and f"--user-data-dir={tmp_path}" in cmd and "--remote-debugging-port=0" in cmd
    assert cmd[-1] == "https://x/"


def test_find_browser_prefers_chrome_then_edge():
    env = {"ProgramFiles": r"C:\PF", "ProgramFiles(x86)": r"C:\PF86", "LOCALAPPDATA": r"C:\L"}
    edge = r"C:\PF86\Microsoft\Edge\Application\msedge.exe"
    assert cdp.find_browser("win32", env, isfile=lambda p: p == edge) == edge
    assert cdp.find_browser("linux", {}, which=lambda n: "/usr/bin/chromium" if n == "chromium" else None) \
        == "/usr/bin/chromium"
    assert cdp.find_browser("darwin", {}) is None


def test_only_a_local_page_socket():
    assert cdp.page_socket_url([{"type": "page", "webSocketDebuggerUrl": "ws://evil.example/x"}]) is None
    assert cdp.page_socket_url([{"type": "service_worker", "webSocketDebuggerUrl": "ws://127.0.0.1:1/a"},
                                {"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1:1/b"}]) == "ws://127.0.0.1:1/b"
    with pytest.raises(ValueError):
        cdp.WebSocket("ws://example.com:80/x", connect=lambda *a: pytest.fail("no connection"))


def test_active_port_file(tmp_path):
    assert cdp.read_active_port(tmp_path) is None
    (tmp_path / "DevToolsActivePort").write_text("57752\n/devtools/browser/abc\n", encoding="ascii")
    assert cdp.read_active_port(tmp_path) == 57752


def test_client_frames_are_masked():
    frame = cdp.encode_frame("hi", b"\x01\x02\x03\x04")
    assert frame[0] == 0x81 and frame[1] == 0x80 | 2 and frame[2:6] == b"\x01\x02\x03\x04"
    assert bytes(b ^ m for b, m in zip(frame[6:], b"\x01\x02")) == b"hi"
    assert cdp.encode_frame("x" * 300, b"\0\0\0\0")[1:4] == bytes([0x80 | 126]) + struct.pack(">H", 300)


class FakeSock:
    def __init__(self, data: bytes):
        self.inp = io.BytesIO(data)
        self.sent = b""

    def sendall(self, b):
        self.sent += b

    def recv(self, n):
        return self.inp.read(min(n, 7))         # small pieces: the reader must put frames together

    def close(self):
        pass


def server_frame(text: str, fin=True, op=0x1) -> bytes:
    data = text.encode()
    return bytes([(0x80 if fin else 0) | op, len(data)]) + data


def test_websocket_reads_split_frames_and_answers_ping():
    data = (b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n" + server_frame("", op=0x9)
            + server_frame('{"id": 1, ', fin=False) + server_frame('"x": 2}', op=0x0))
    sock = FakeSock(data)
    ws = cdp.WebSocket("ws://127.0.0.1:9222/devtools/page/1", connect=lambda addr, t: sock)
    assert json.loads(ws.recv()) == {"id": 1, "x": 2}
    assert b"GET /devtools/page/1 HTTP/1.1" in sock.sent and b"\x8a\x80" in sock.sent      # pong sent


def test_no_upgrade_is_an_error():
    sock = FakeSock(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    with pytest.raises(ConnectionError):
        cdp.WebSocket("ws://127.0.0.1:9222/x", connect=lambda addr, t: sock)


class FakeWS:
    answers = []

    def __init__(self, url, **kw):
        self.url = url
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        value = FakeWS.answers.pop(0)
        return json.dumps({"id": self.sent[-1]["id"], "result": {"result": {"value": value}}})

    def close(self):
        pass


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(url, timeout):
    if url.endswith("/json/version"):
        return FakeResponse(json.dumps({"Browser": "Chrome/150.0.7871.116"}).encode())
    return FakeResponse(json.dumps([{"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1:57752/devtools/page/A"}])
                        .encode())


class FakeProc:
    pid = -1


def fake_browser(started):
    def popen(command, **kw):
        started.append(command)
        profile = next(c.split("=", 1)[1] for c in command if c.startswith("--user-data-dir="))
        with open(profile + "/DevToolsActivePort", "w", encoding="ascii") as f:
            f.write("57752\n/devtools/browser/x\n")
        return FakeProc()
    return popen


@pytest.fixture
def fakes(monkeypatch):
    ended = []
    monkeypatch.setattr(cdp, "WebSocket", FakeWS)
    monkeypatch.setattr(cdp, "end_browser", lambda proc: ended.append(proc))
    monkeypatch.setattr(cdp, "wait_for", lambda check, timeout, stop=None, every=0.5, clock=None: _until(check))
    return ended


def _until(check):
    for _ in range(10):
        value = check()
        if value is not None:
            return value
    return None


def test_a_run_reads_the_score_and_cleans_up(tmp_path, fakes):
    started = []
    FakeWS.answers = [None, None, "31.4"]                    # running, running, done
    wl = wb.WebBenchmarkWorkload(wb.SPEEDOMETER, browser="chrome.exe", popen=fake_browser(started), opener=opener)
    r = wl.run(RunContext(output_dir=tmp_path, timeout_seconds=60, stop_event=threading.Event()))
    assert r.status == "completed" and r.score == 31.4 and r.unit == "score"
    assert r.note.startswith("Browser: Chrome/150.0.7871.116; Page: " + wb.SPEEDOMETER["url"])
    profile = next(c.split("=", 1)[1] for c in started[0] if c.startswith("--user-data-dir="))
    assert not __import__("os").path.exists(profile)        # the run's own profile is gone
    assert len(fakes) == 1                                   # the browser it started was closed
    assert wl.needs_desktop and wl.version() == "3.1"


def test_page_error_is_invalid_and_no_score(tmp_path, fakes):
    FakeWS.answers = ["ERR:Errors were encountered during page load."]
    wl = wb.WebBenchmarkWorkload(wb.JETSTREAM, browser="chrome.exe", popen=fake_browser([]), opener=opener)
    r = wl.run(RunContext(output_dir=tmp_path, timeout_seconds=60, stop_event=threading.Event()))
    assert r.status == "invalid" and r.score is None and "Errors were encountered" in r.error


def test_no_score_in_time_is_timed_out(tmp_path, fakes):
    FakeWS.answers = [None] * 20
    wl = wb.WebBenchmarkWorkload(wb.JETSTREAM, browser="chrome.exe", popen=fake_browser([]), opener=opener)
    r = wl.run(RunContext(output_dir=tmp_path, timeout_seconds=60, stop_event=threading.Event()))
    assert r.status == "timed_out" and r.score is None


def test_no_browser_is_skipped(tmp_path):
    wl = wb.WebBenchmarkWorkload(wb.SPEEDOMETER, finder=lambda: None)
    assert wl.availability()[0] is False
    assert wl.run(RunContext(output_dir=tmp_path)).status == "skipped"


def test_the_bar_moves():
    now = [100.0]
    wl = wb.WebBenchmarkWorkload(wb.SPEEDOMETER, browser="c", clock=lambda: now[0])
    assert wl.progress is None
    wl._started = 100.0
    now[0] = 220.0
    assert wl.progress == (0.5, 120.0)

"""Remote start (plan Phase D): local-network check, one-time code and its lock, the allowed requests, a real server
on 127.0.0.1 with a fake app, which tests are offered and how a remote start runs (no dialogs)."""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from app.catalog import TestEntry
from app.remote import server as rs


def test_only_this_pc_and_the_local_network():
    for ok in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "172.16.3.4", "172.31.255.1", "169.254.1.1", "::1",
               "fe80::1%12", "fd12::5", "::ffff:192.168.0.9"):
        assert rs.allowed_client(ok), ok
    for bad in ("8.8.8.8", "172.32.0.1", "100.64.0.1", "2001:db8::1", "::ffff:8.8.8.8", "not an address"):
        assert not rs.allowed_client(bad), bad


def test_the_code_is_new_each_time_and_locks_after_wrong_tries():
    assert rs.new_code() != rs.new_code() and len(rs.new_code()) == rs.CODE_LENGTH
    now = [100.0]
    g = rs.Guard("ABCD2345", clock=lambda: now[0])
    assert g.check("abcd2345 ") == (True, "")
    for _ in range(rs.MAX_WRONG - 1):
        assert g.check("WRONG")[1] == "wrong code"
    ok, why = g.check(None)
    assert not ok and "locked" in why
    assert not g.check("ABCD2345")[0]                  # even the right code waits for the lock
    now[0] += rs.LOCK_SECONDS + 1
    assert g.check("ABCD2345")[0]


def test_only_offered_tests_and_sane_numbers():
    good = {"ids": ["a", "a", "b"], "repetitions": 3, "warmup_runs": 1, "cooldown_seconds": 30}
    assert rs.check_start(good, ["a", "b"]) == ({"ids": ["a", "b"], "repetitions": 3, "warmup_runs": 1,
                                                 "cooldown_seconds": 30}, "")
    for bad, why in (({**good, "ids": ["a", "cmd.exe"]}, "cannot start"), ({**good, "ids": []}, "at least one"),
                     ({**good, "repetitions": 0}, "repetitions"), ({**good, "cooldown_seconds": 99999}, "cooldown"),
                     ({**good, "warmup_runs": True}, "warmup"), ({**good, "repetitions": "3"}, "repetitions"),
                     ([1], "not a start")):
        request, text = rs.check_start(bad, ["a", "b"])
        assert request is None and why in text, (bad, text)


class FakeApi:
    def __init__(self):
        self.started, self.stopped = [], 0
        self.html = None

    def info(self):
        return {"specs": [["Processor", "X"]], "tests": [{"id": "sevenzip_benchmark", "name": "7-Zip"}],
                "defaults": {"repetitions": 3, "warmup_runs": 1, "cooldown_seconds": 30}}

    def state(self):
        return {"running": False, "text": "", "percent": None, "report": False}

    def start(self, **kw):
        self.started.append(kw)
        return True, "Started"

    def stop(self):
        self.stopped += 1
        return "Stopping"

    def report(self):
        return self.html


@pytest.fixture
def served():
    api = FakeApi()
    server = rs.RemoteServer(api, local_only=True, port=0, code="ABCD2345")
    server.start()
    yield api, server, f"http://127.0.0.1:{server.port}"
    server.stop()


def call(url, code=None, body=None, method=None):
    headers = {"X-Code": code} if code else {}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_the_server_answers_only_with_the_code(served):
    api, server, base = served
    assert server.addresses() == [f"http://127.0.0.1:{server.port}/"]
    status, headers, body = call(base + "/")
    assert status == 200 and b"remote start" in body and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert call(base + "/api/info")[0] == 401
    assert call(base + "/api/info", "WRONG123")[0] == 401
    status, _, body = call(base + "/api/info", "abcd2345")
    assert status == 200 and json.loads(body)["tests"][0]["id"] == "sevenzip_benchmark"
    assert call(base + "/api/report", "ABCD2345")[0] == 404
    api.html = "<h1>report</h1>"
    status, headers, body = call(base + "/api/report", "ABCD2345")
    assert status == 200 and body == b"<h1>report</h1>" and headers["Content-Type"].startswith("text/html")
    assert call(base + "/../settings.json", "ABCD2345")[0] == 404
    assert call(base + "/api/info", "ABCD2345", method="PUT")[0] == 405


def test_start_and_stop_through_the_server(served):
    api, server, base = served
    req = {"ids": ["sevenzip_benchmark"], "repetitions": 2, "warmup_runs": 0, "cooldown_seconds": 5}
    assert call(base + "/api/start", body=req)[0] == 401 and not api.started
    status, _, body = call(base + "/api/start", "ABCD2345", {**req, "ids": ["powershell"]})
    assert status == 400 and b"cannot start" in body and not api.started
    assert call(base + "/api/start", "ABCD2345", b"not json")[0] == 400
    assert call(base + "/api/start", "ABCD2345", b"x" * (rs.MAX_BODY + 1))[0] == 413
    status, _, body = call(base + "/api/start", "ABCD2345", req)
    assert status == 200 and json.loads(body)["ok"] and api.started == [req]
    assert call(base + "/api/stop", "ABCD2345", {})[0] == 200 and api.stopped == 1


def test_a_guesser_is_locked_out(served):
    _, _, base = served
    for _ in range(rs.MAX_WRONG):
        call(base + "/api/state", "NOPE")
    status, _, body = call(base + "/api/state", "ABCD2345")
    assert status == 401 and b"too many wrong codes" in body


def _entry(test_id, source="workload", ref=None, group="cpu", mode="automatic", state="installed"):
    ref = ref if ref is not None else SimpleNamespace()
    return TestEntry(id=test_id, name=test_id, group=group, mode=mode, state=state, state_reason="",
                     compatible=True, compatibility_reason="", verified=False, source=source, ref=ref)


def test_only_tests_that_need_nobody_are_offered():
    from app.ui.remote_control import remote_entries
    lic = SimpleNamespace(license=("vray", "V-Ray", str), license_ok=lambda: False)
    accepted = SimpleNamespace(license=("vray", "V-Ray", str), license_ok=lambda: True)
    entries = [_entry("sevenzip_benchmark"), _entry("vray_benchmark", ref=lic), _entry("vray_gpu", ref=accepted),
               _entry("llama_benchy", ref=SimpleNamespace(configure=lambda s: None), group="ai"),
               _entry("llama_benchy_gemma4_12b", ref=SimpleNamespace(configure=None, asks_settings=False),
                      group="ai"),
               _entry("geekbench_6", source="library"), _entry("steam_game", source="steam", group="game")]
    ids = [e.id for e in remote_entries(entries) if e.can_run()]
    assert ids == ["sevenzip_benchmark", "vray_gpu", "llama_benchy_gemma4_12b"]


def _window(tmp_path, entries, running=False):
    status = SimpleNamespace(text=lambda: "", setText=lambda t: None)
    lhm = SimpleNamespace(platform_name="linux", declined=False, running=lambda: False, busy=lambda: False)
    panel = SimpleNamespace(is_running=lambda: running, lhm=lhm, status_label=status,
                            telemetry_box=SimpleNamespace(isChecked=lambda: True))
    return SimpleNamespace(automation_panel=panel, frame_panel=SimpleNamespace(is_running=lambda: False,
                                                                               data_dir=tmp_path),
                           tests_page=SimpleNamespace(busy=False, entries=entries), status_label=status)


def test_a_remote_start_asks_nothing_at_the_pc(tmp_path):
    from app.ui.remote_control import remote_start
    seen = {}

    def launch(window, entries, choice, confirm=None, tools_ready=False):
        seen.update(ids=[e.id for e in entries], choice=choice, answer=confirm("Run automated benchmarks?"),
                    tools_ready=tools_ready)
        return True
    w = _window(tmp_path, [_entry("sevenzip_benchmark"), _entry("geekbench_6", source="library")])
    ok, text = remote_start(w, ["sevenzip_benchmark"], 2, 0, 10, launch=launch)
    assert ok and "Started 1 test(s), 2 measured run(s)" in text
    assert seen["ids"] == ["sevenzip_benchmark"] and seen["answer"] is True and not seen["tools_ready"]
    assert (seen["choice"].repetitions, seen["choice"].warmup_runs, seen["choice"].cooldown_seconds) == (2, 0, 10)
    ok, text = remote_start(w, ["geekbench_6"], 2, 0, 10, launch=launch)
    assert not ok and "cannot start from here" in text
    ok, text = remote_start(_window(tmp_path, [_entry("sevenzip_benchmark")], running=True), ["sevenzip_benchmark"],
                            1, 0, 0, launch=launch)
    assert not ok and "already going on" in text


def test_without_sensor_tool_running_the_run_goes_on_without_it(tmp_path):
    from app.ui.remote_control import remote_start
    seen = {}
    w = _window(tmp_path, [_entry("sevenzip_benchmark")])
    w.automation_panel.lhm.platform_name = "win32"
    ok, text = remote_start(w, ["sevenzip_benchmark"], 1, 0, 0,
                            launch=lambda *a, **k: seen.update(k) or True)
    assert ok and seen["tools_ready"] is True and "Without CPU sensors" in text


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_answers_are_made_on_the_qt_thread(qapp):
    from PySide6.QtCore import QCoreApplication, QThread
    from app.ui.remote_control import MainThread
    main = MainThread()
    out = {}

    def worker():
        out["same"] = main.run(lambda: QThread.currentThread() is main.thread())
        try:
            main.run(lambda: 1 / 0)
        except ZeroDivisionError:
            out["error"] = True
    t = threading.Thread(target=worker)
    t.start()
    end = time.monotonic() + 10
    while t.is_alive() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert out == {"same": True, "error": True}


def test_the_switch_window_shows_a_new_code(tmp_path, qapp):
    from app.ui.remote_control import RemoteControl, RemoteDialog
    window = SimpleNamespace(frame_panel=SimpleNamespace(data_dir=tmp_path))
    control = RemoteControl.__new__(RemoteControl)
    control.window, control.api, control.server = window, FakeApi(), None
    control.server_class = lambda api, local_only, port: rs.RemoteServer(api, local_only=True, port=0)
    d = RemoteDialog(control)
    assert not control.running() and d.code.text() == ""
    assert not d.switch.isChecked() and "OFF" in d.switch.text()
    assert d.toggle() and d.code.text().startswith("Code: ") and "127.0.0.1" in d.address.text()
    assert d.switch.isChecked() and "ON" in d.switch.text()
    first = control.server.code
    assert not d.toggle() and d.code.text() == ""
    assert d.toggle() and control.server.code != first
    control.stop()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["remote_start"]
    assert set(saved) == {"port", "local_only"}            # the server's "on" is never stored

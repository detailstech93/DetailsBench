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


def test_tailscale_only_through_this_pcs_tailscale_address():
    # owner, 2026-10-20: from outside the home network only through Tailscale
    assert rs.is_tailscale("100.84.145.56") and rs.is_tailscale("fd7a:115c:a1e0::8532:9139")
    assert not rs.is_tailscale("100.128.0.1") and not rs.is_tailscale(None)
    assert rs.allowed_client("100.101.7.3", local="100.84.145.56")             # a phone on the tailnet
    assert not rs.allowed_client("100.101.7.3", local="192.168.1.20")          # the same range from a provider (CGNAT)
    assert not rs.allowed_client("100.101.7.3")
    assert rs.allowed_client("fd7a:115c:a1e0::1", local="fd7a:115c:a1e0::2")
    assert not rs.allowed_client("8.8.8.8", local="100.84.145.56")             # the internet stays out
    assert rs.allowed_client("192.168.1.30", local="192.168.1.20")

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
        return {"specs": [["Processor", "X"]],
                "tests": [{"id": "sevenzip_benchmark", "name": "7-Zip", "can_start": True},
                          {"id": "geekbench_6", "name": "Geekbench 6", "can_start": False, "why": "typed"}],
                "defaults": {"repetitions": 3, "warmup_runs": 1, "cooldown_seconds": 30}}

    def state(self):
        return {"running": False, "text": "", "percent": None, "report": False}

    def start(self, **kw):
        self.started.append(kw)
        return True, "Started"

    def stop(self):
        self.stopped += 1
        return "Stopping"

    def skip(self):
        self.skipped = getattr(self, "skipped", 0) + 1
        return "Skipping"

    def reports(self):
        return [{"name": "session_1_x.html", "time": "2026-10-19 20:07"}] if self.html else []

    def report(self, name=None):
        return self.html if name in (None, "session_1_x.html") else None


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
    assert status == 200 and b"DetailsBench" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"] and "Set-Cookie" not in headers
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
    status, _, body = call(base + "/api/start", "ABCD2345", {**req, "ids": ["geekbench_6"]})
    assert status == 400 and b"cannot start" in body and not api.started     # listed, but not startable here
    assert call(base + "/api/start", "ABCD2345", b"not json")[0] == 400
    assert call(base + "/api/start", "ABCD2345", b"x" * (rs.MAX_BODY + 1))[0] == 413
    status, _, body = call(base + "/api/start", "ABCD2345", req)
    assert status == 200 and json.loads(body)["ok"] and api.started == [req]
    assert call(base + "/api/skip", "ABCD2345", {})[0] == 200 and api.skipped == 1 and api.stopped == 0
    assert call(base + "/api/skip", None, {})[0] == 401 and api.skipped == 1
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


def get(url, cookie=None):
    req = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_the_report_opens_as_a_page_after_the_code(served):
    api, server, base = served
    assert get(base + "/report")[0] == 401
    assert get(base + "/report", "pcbm=guess")[0] == 401
    status, headers, _ = call(base + "/api/state", "ABCD2345")
    cookie = headers["Set-Cookie"]
    assert status == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    cookie = cookie.split(";")[0]
    assert get(base + "/report", cookie)[0] == 404                       # no report yet
    api.html = "<h1>report</h1>"
    status, headers, body = get(base + "/report", cookie)
    assert status == 200 and body == b"<h1>report</h1>"
    assert "script-src" not in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "SAMEORIGIN"
    assert get(base + "/report/session_1_x.html", cookie)[0] == 200
    assert get(base + "/report/..%2Fsettings.json", cookie)[0] == 404
    status, _, body = call(base + "/api/reports", "ABCD2345")
    assert status == 200 and json.loads(body)["reports"][0]["name"] == "session_1_x.html"
    other = rs.RemoteServer(api, local_only=True, port=0, code="ABCD2345")
    other.start()
    try:                                                # a new switch-on: the old cookie is no good
        assert get(f"http://127.0.0.1:{other.port}/report", cookie)[0] == 401
    finally:
        other.stop()


def test_only_tests_that_need_nobody_are_offered():
    from app.ui.remote_control import remote_entries, remote_listing
    lic = SimpleNamespace(license=("vray", "V-Ray", str), license_ok=lambda: False)
    accepted = SimpleNamespace(license=("vray", "V-Ray", str), license_ok=lambda: True)
    entries = [_entry("sevenzip_benchmark"), _entry("vray_benchmark", ref=lic), _entry("vray_gpu", ref=accepted),
               _entry("llama_benchy", ref=SimpleNamespace(configure=lambda s: None), group="ai"),
               _entry("llama_benchy_gemma4_12b", ref=SimpleNamespace(configure=None, asks_settings=False),
                      group="ai"),
               _entry("geekbench_6", source="library"), _entry("steam_game", source="steam", group="game")]
    ids = [e.id for e in remote_entries(entries, "linux") if e.can_run()]
    assert ids == ["sevenzip_benchmark", "vray_gpu", "llama_benchy_gemma4_12b"]
    admin = [_entry("passmark_cpu", ref=SimpleNamespace(needs_admin=True)),
             _entry("cyberpunk", ref=SimpleNamespace(script={"steam_app": 1091500}), group="game")]
    assert [e.id for e in remote_entries(admin, "linux")] == ["passmark_cpu", "cyberpunk"]
    # on Windows they can start too (the owner asked for every test, games too), with a warning about the prompt
    listing = {e.id: (why, warning) for e, why, warning in remote_listing(admin, "win32")}
    assert listing["passmark_cpu"][0] == "" and "administrator" in listing["passmark_cpu"][1]
    assert listing["cyberpunk"][0] == "" and "game" in listing["cyberpunk"][1].lower()
    # every test for this PC is listed; the ones that need someone at the PC say why
    shown = {e.id: why for e, why, _ in remote_listing(entries + [_entry("blender", state="missing")], "linux")}
    assert set(shown) == {e.id for e in entries} | {"blender"}
    assert "typed" in shown["geekbench_6"] and "license" in shown["vray_benchmark"]
    assert "window" in shown["llama_benchy"] and "Not ready" in shown["blender"] and shown["steam_game"]


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
    window = SimpleNamespace(frame_panel=SimpleNamespace(data_dir=tmp_path),
                             automation_panel=SimpleNamespace(output_root=str(tmp_path / "runs")),
                             status_label=SimpleNamespace(setText=lambda t: None))
    control = RemoteControl.__new__(RemoteControl)
    control.window, control.api, control.server = window, FakeApi(), None
    control.server_class = lambda api, local_only, port: rs.RemoteServer(api, local_only=True, port=0)
    d = RemoteDialog(control)
    assert not control.running() and d.code.text() == "" and d.card.isHidden()
    assert not d.switch.isChecked() and "OFF" in d.switch.text() and d.pill.text() == "OFF"
    assert d.reports.text() == str(tmp_path / "reports")
    assert d.toggle() and d.code.text() == control.server.code and "127.0.0.1" in d.address.text()
    assert d.switch.isChecked() and "ON" in d.switch.text() and d.pill.text() == "ON" and not d.card.isHidden()
    first = control.server.code
    assert not d.toggle() and d.code.text() == ""
    assert d.toggle() and control.server.code != first
    control.stop()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["remote_start"]
    assert set(saved) == {"port", "local_only"}            # the server's "on" is never stored


def test_the_reports_folder_is_asked_once(tmp_path, monkeypatch):
    from app.ui import reports_folder
    texts = []
    window = SimpleNamespace(frame_panel=SimpleNamespace(data_dir=tmp_path),
                             automation_panel=SimpleNamespace(output_root=str(tmp_path / "runs")),
                             status_label=SimpleNamespace(setText=texts.append))
    asked = []
    answer = [""]
    monkeypatch.setattr(reports_folder, "pick", lambda parent, title, start: asked.append(start) or answer[0])
    assert reports_folder.folder(window) == tmp_path / "reports"           # nothing picked yet: the data folder
    assert reports_folder.choose(window) == tmp_path / "reports" and len(asked) == 1
    assert reports_folder.saved_folder(window) is None                    # cancelled: asked again next time
    answer[0] = str(tmp_path / "My reports")
    assert reports_folder.choose(window) == tmp_path / "My reports" and len(asked) == 2
    assert (tmp_path / "My reports").is_dir() and "My reports" in texts[-1]
    assert reports_folder.choose(window) == tmp_path / "My reports" and len(asked) == 2   # never asked again
    assert reports_folder.folder(window) == tmp_path / "My reports"
    answer[0] = ""
    assert reports_folder.choose(window, always=True) == tmp_path / "My reports" and len(asked) == 3


def test_the_page_follows_each_test_of_the_plan():
    from app.ui.remote_control import plan_tests

    def item(wid, name, runs, warmups):
        return SimpleNamespace(workload=SimpleNamespace(id=wid, display_name=name), repetitions=runs,
                               warmup_runs=warmups)
    plan = [item("sevenzip_benchmark", "7-Zip", 3, 1), item("blender_cpu", "Blender CPU", 2, 0),
            item("ycruncher", "y-cruncher", 1, 0)]
    rows = [{"benchmark_name": "sevenzip_benchmark", "is_warmup": 1, "score": 170000.0, "status": "completed"},
            {"benchmark_name": "sevenzip_benchmark", "is_warmup": 0, "score": 173304.0, "status": "completed",
             "unit": "MIPS"},
            {"benchmark_name": "sevenzip_benchmark", "is_warmup": 0, "score": 172726.0, "status": "completed",
             "unit": "MIPS"},
            {"benchmark_name": "sevenzip_benchmark", "is_warmup": 0, "score": None, "status": "failed"}]
    panel = SimpleNamespace(worker=SimpleNamespace(runner=SimpleNamespace(plan=plan, session_id=24)),
                            last_session_id=24, step=(4, 6, "Blender CPU: run 1"))
    db = SimpleNamespace(get_session=lambda sid: {"results": rows} if sid == 24 else None)
    tests = plan_tests(panel, db, True)
    assert [t["state"] for t in tests] == ["done", "running", "waiting"]
    first = tests[0]
    assert (first["runs"], first["planned"], first["failed"], first["unit"]) == (3, 3, 1, "MIPS")
    assert first["scores"] == ["173,304", "172,726"] and first["average"] == "173,015"
    assert [t["state"] for t in plan_tests(panel, db, False)] == ["done", "not run", "not run"]
    panel.last_session_id = 25                           # another session since: nothing to show
    assert plan_tests(panel, db, False) == []


def test_reports_are_listed_and_only_those_can_be_read(tmp_path, qapp):
    import os
    from app.ui.remote_control import MainThread, RemoteApi
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "session_1_a.html").write_text("<p>one</p>", encoding="utf-8")
    (folder / "session_2_b.html").write_text("<p>two</p>", encoding="utf-8")
    old = time.time() - 100
    os.utime(folder / "session_1_a.html", (old, old))
    (tmp_path / "secret.html").write_text("no", encoding="utf-8")
    window = SimpleNamespace(frame_panel=SimpleNamespace(data_dir=tmp_path), last_report_path=None,
                             automation_panel=SimpleNamespace(output_root=str(tmp_path / "runs")))
    api = RemoteApi(window, MainThread())
    assert [r["name"] for r in api.reports()] == ["session_2_b.html", "session_1_a.html"]
    assert api.report() == "<p>two</p>"                  # nothing written since the app started: the newest
    assert api.report("session_1_a.html") == "<p>one</p>"
    for bad in ("../secret.html", "..\\secret.html", "secret.html", "session_3.html", "x.txt"):
        assert api.report(bad) is None, bad
    window.last_report_path = folder / "session_1_a.html"
    assert api.report() == "<p>one</p>"


def test_install_and_license_only_where_the_app_offers_them():
    tests = [{"id": "vray_benchmark", "install": False, "license": True},
             {"id": "ycruncher_pi_1b", "install": True, "license": False}]
    assert rs.check_action({"id": "vray_benchmark", "what": "license"}, tests) == ("vray_benchmark", "license")
    assert rs.check_action({"id": "ycruncher_pi_1b", "what": "install"}, tests) == ("ycruncher_pi_1b", "install")
    for bad in ({"id": "vray_benchmark", "what": "install"}, {"id": "nope", "what": "license"},
                {"id": "ycruncher_pi_1b", "what": "run"}, {"id": 5, "what": "install"}, ["x"]):
        assert rs.check_action(bad, tests)[0] is None, bad


def test_the_page_asks_and_installs_through_the_server(served):
    api, server, base = served
    seen = []
    api.info = lambda: {"tests": [{"id": "ycruncher_pi_1b", "install": True, "license": False}]}
    api.question = lambda tid, what: seen.append(("q", tid, what)) or (True, "Download 47 MB from numberworld.org?")
    api.act = lambda tid, what: seen.append(("a", tid, what)) or (True, "Installing")
    req = {"id": "ycruncher_pi_1b", "what": "install"}
    assert call(base + "/api/question", body=req)[0] == 401 and not seen
    status, _, body = call(base + "/api/question", "ABCD2345", req)
    assert status == 200 and b"numberworld" in body
    assert call(base + "/api/act", "ABCD2345", {**req, "what": "license"})[0] == 400
    assert call(base + "/api/act", "ABCD2345", req)[0] == 200
    assert seen == [("q", "ycruncher_pi_1b", "install"), ("a", "ycruncher_pi_1b", "install")]


def test_a_license_is_read_and_accepted_from_the_page(qapp):
    from app.ui.remote_control import MainThread, RemoteApi
    accepted = []
    ref = SimpleNamespace(license=("vray", "V-Ray Benchmark", lambda: "Read https://www.chaos.com/eula first."),
                          license_ok=lambda: bool(accepted), accept_license=lambda: accepted.append(1))
    e = _entry("vray_benchmark", ref=ref)
    window = SimpleNamespace(tests_page=SimpleNamespace(entries=[e], host=None))
    api = RemoteApi(window, MainThread())
    assert api.question("vray_benchmark", "license") == (True, "Read https://www.chaos.com/eula first.")
    assert api.act("vray_benchmark", "license") == (True, "V-Ray Benchmark: license accepted.") and accepted
    assert api.question("vray_benchmark", "license")[0] is False         # nothing left to accept
    assert api.question("nope", "license")[0] is False


def test_the_list_says_what_each_report_is_about_and_the_newest_is_known(tmp_path, qapp):
    from app.ui.remote_control import MainThread, RemoteApi, report_title
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "session_12_a.html").write_text("<html><head><title>Benchmark report: PassMark CPU Mark - Automated "
                                              "2026-10-19 21&#58;27</title></head></html>", encoding="utf-8")
    (folder / "session_3_b.html").write_text("<p>no title</p>", encoding="utf-8")
    assert report_title(folder / "session_12_a.html") == "PassMark CPU Mark - Automated 2026-10-19 21:27"
    assert report_title(folder / "missing.html") == ""
    window = SimpleNamespace(frame_panel=SimpleNamespace(data_dir=tmp_path), last_report_path=None,
                             automation_panel=SimpleNamespace(output_root=str(tmp_path / "runs")))
    titles = {r["name"]: r["title"] for r in RemoteApi(window, MainThread()).reports()}
    assert titles == {"session_12_a.html": "PassMark CPU Mark - Automated 2026-10-19 21:27", "session_3_b.html": ""}


def test_the_tests_key_changes_when_a_test_is_installed_or_its_license_accepted():
    from app.ui.remote_control import tests_key
    accepted = []
    ref = SimpleNamespace(license=("vray", "V-Ray", str), license_ok=lambda: bool(accepted))
    before = tests_key([_entry("vray_benchmark", ref=ref), _entry("blender", state="missing")])
    assert before == tests_key([_entry("vray_benchmark", ref=ref), _entry("blender", state="missing")])
    assert before != tests_key([_entry("vray_benchmark", ref=ref), _entry("blender")])
    accepted.append(1)
    assert before != tests_key([_entry("vray_benchmark", ref=ref), _entry("blender", state="missing")])


def test_the_page_keeps_install_buttons_out_of_the_grey_and_follows_the_newest_report():
    from app.remote.page import PAGE
    assert "item.append(actButton(" in PAGE and "txt.append(actButton(" not in PAGE
    assert "s.newest_report" in PAGE and "s.tests_key" in PAGE and '"?t=" + Date.now()' in PAGE
    assert 'id="reportReload"' in PAGE
    assert "reportToggle" in PAGE and "pcbm-report" in PAGE        # the report opens and closes on the phone


def test_a_download_moves_the_bar():
    from app.ui.tests_page import RunState, progress_percent
    assert progress_percent(RunState(True, 0, None, "Downloading V-Ray: 300 of 1,200 MB", share=0.25)) == 25
    assert progress_percent(RunState(True, 0, None, "Installing...")) is None          # no known part: moving bar
    assert progress_percent(RunState(True, 1, 4, "", fraction=0.5)) == 38                # runs keep their own count


def test_the_remote_button_says_admin_is_needed_for_every_test():
    from app.ui import remote_control as rc
    assert rc.admin_notice("linux") is None and rc.admin_notice("win32", elevated=True) is None
    text = rc.admin_notice("win32", elevated=False)
    assert "administrator" in text and "every test" in text and "PassMark" in text
    shown, restarted = [], []
    assert rc.admin_first(None, choose=lambda t: shown.append(t) or False, restart=lambda: restarted.append(1),
                          notice=lambda: text) is False                 # Continue: the remote window opens
    assert shown == [text] and restarted == []
    assert rc.admin_first(None, choose=lambda t: True, restart=lambda: True, notice=lambda: text) is True
    assert rc.admin_first(None, choose=lambda t: 1 / 0, notice=lambda: None) is False   # admin: no notice


def test_stress_length_from_the_page_or_ten_minutes_in_a_run_of_several(tmp_path):
    """Owner, 2026-10-20: a run of several tests whose stress length was not chosen runs 10 minutes (the real run
    used 1 minute, the last length chosen, too short to judge)."""
    from app.remote import server as rsrv
    from app.ui.remote_control import remote_start
    from app.stress.workload import DEFAULT_MINUTES
    good = {"ids": ["a"], "repetitions": 3, "warmup_runs": 1, "cooldown_seconds": 0}
    assert "stress_minutes" not in rsrv.check_start(good, ["a"])[0]
    assert rsrv.check_start(dict(good, stress_minutes=20), ["a"])[0]["stress_minutes"] == 20
    assert rsrv.check_start(dict(good, stress_minutes=0), ["a"])[0] is None
    seen = []
    launch = lambda window, entries, choice, **k: seen.append(choice.stress_minutes) or True
    (tmp_path / "settings.json").write_text('{"stress_minutes": 1}', encoding="utf-8")
    both = [_entry("stress_cpu"), _entry("sevenzip_benchmark")]
    remote_start(_window(tmp_path, both), ["stress_cpu", "sevenzip_benchmark"], 1, 0, 0, launch=launch)
    remote_start(_window(tmp_path, both), ["stress_cpu", "sevenzip_benchmark"], 1, 0, 0, launch=launch,
                 stress_minutes=30)
    assert seen == [DEFAULT_MINUTES, 30] and DEFAULT_MINUTES == 10


def test_the_games_section_sends_a_preset_per_game():
    """Owner, 2026-10-20: games have their own section on the page with the preset chosen there."""
    from app.games import presets
    req = {"ids": ["cp", "rdr2"], "repetitions": 3, "warmup_runs": 0, "cooldown_seconds": 30,
           "game_presets": {"cp": {"mode": "upscaling_fg", "upscaler": "dlss", "frame_gen": "x2", "quality": "high",
                                   "resolution": "2560x1440", "ray_tracing": "on"},
                            "rdr2": {"mode": "raw", "upscaler": "dlss", "frame_gen": "x2", "quality": "ultra",
                                     "resolution": "1920x1080", "ray_tracing": "off"}}}
    out, why = rs.check_start(req, ["cp", "rdr2"])
    assert why == "" and out["game_presets"]["cp"] == presets.GamePreset(
        "2560x1440", "dlss", "on", "high", "x2").to_dict()
    assert out["game_presets"]["rdr2"]["upscaler"] == "off" and out["game_presets"]["rdr2"]["frame_gen"] == "off"
    assert rs.check_start(dict(req, game_presets={"other": {}}), ["cp", "rdr2"])[0] is None
    bad = dict(req, game_presets={"cp": {"mode": "upscaling", "upscaler": "nope"}})
    assert rs.check_start(bad, ["cp", "rdr2"]) == (None, "a game preset is not valid")
    choices = presets.page_choices("AMD Radeon RX 7900 XTX")
    assert [u[0] for u in choices["upscalers"]] == ["fsr", "xess"] and choices["frame_gens"]["xess"] == []
    from app.remote.page import PAGE
    assert 'id="secGames"' in PAGE and "game_presets" in PAGE and 'id="gMode"' in PAGE


def test_games_and_tests_are_not_started_together(monkeypatch):
    from types import SimpleNamespace
    from app.ui import remote_control as rc
    game = _entry("steam_1", group="game")
    test = _entry("sevenzip_benchmark")
    page = SimpleNamespace(entries=[game, test], busy=False)
    panel = SimpleNamespace(is_running=lambda: False, lhm=SimpleNamespace(busy=lambda: False))
    window = SimpleNamespace(automation_panel=panel, tests_page=page,
                             frame_panel=SimpleNamespace(is_running=lambda: False, data_dir=None))
    monkeypatch.setattr(rc, "remote_entries", lambda entries, platform=None: entries)
    ok, text = rc.remote_start(window, ["steam_1", "sevenzip_benchmark"], 1, 0, 0)
    assert not ok and "do not run together" in text


def test_nothing_starts_from_the_phone_while_the_pc_scans(tmp_path):
    """Owner, 2026-10-20: no test starts while the app scans the PC for tests."""
    from app.ui.remote_control import remote_start
    w = _window(tmp_path, [_entry("sevenzip_benchmark")])
    w.tests_page.scanning = True
    ok, text = remote_start(w, ["sevenzip_benchmark"], 1, 0, 0, launch=lambda *a, **k: True)
    assert not ok and "scanning" in text
    from app.remote.page import PAGE as html
    assert "s.running || !!s.scanning" in html


def test_wsl_loopback_addresses_are_not_offered_and_wsl_explains_why_the_phone_cannot_reach_it(monkeypatch):
    import socket
    from types import SimpleNamespace

    import psutil

    from app.remote import server
    a = lambda ip: SimpleNamespace(family=socket.AF_INET, address=ip)
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {"lo": [a("127.0.0.1"), a("10.255.255.254")],
                                                          "eth0": [a("172.24.101.72")]})
    assert server.local_addresses() == ["172.24.101.72"]
    note = server.wsl_note(["http://172.24.101.72:8765/"], wsl=True)
    assert "WSL" in note and "Tailscale" in note and "mirrored" in note
    assert server.wsl_note(["http://172.24.101.72:8765/", "http://100.84.145.56:8765/"], wsl=True) == ""
    assert server.wsl_note(["http://192.168.1.5:8765/"], wsl=False) == ""


def test_shut_down_after_the_run_from_the_page(tmp_path, monkeypatch):
    """Owner, 2026-10-21: the shutdown box on the remote page too; refused where the PC does not offer it (WSL)."""
    from app.remote import server as rs
    from app.ui import shutdown_after
    from app.ui.remote_control import remote_start
    good = {"ids": ["a"], "repetitions": 1, "warmup_runs": 0, "cooldown_seconds": 0}
    assert "shutdown_after" not in rs.check_start(good, ["a"])[0]
    assert rs.check_start(dict(good, shutdown_after=True), ["a"])[0]["shutdown_after"] is True
    assert rs.check_start(dict(good, shutdown_after="yes"), ["a"])[0] is None
    seen = []

    def launch(window, entries, choice, confirm=None, tools_ready=False):
        seen.append(choice.shutdown_after)
        return True
    monkeypatch.setattr(shutdown_after, "why_not_offered", lambda *a, **k: None)
    w = _window(tmp_path, [_entry("sevenzip_benchmark")])
    ok, text = remote_start(w, ["sevenzip_benchmark"], 1, 0, 0, launch=launch, shutdown_after=True)
    assert ok and "shuts down when the run ends" in text
    ok, text = remote_start(w, ["sevenzip_benchmark"], 1, 0, 0, launch=launch)
    assert ok and "shuts down" not in text
    monkeypatch.setattr(shutdown_after, "why_not_offered", lambda *a, **k: "not offered in WSL")
    ok, text = remote_start(w, ["sevenzip_benchmark"], 1, 0, 0, launch=launch, shutdown_after=True)
    assert seen == [True, False, False] and "shuts down" not in text


def test_install_several_only_tests_the_page_offers_to_install():
    tests = [{"id": "a", "install": True}, {"id": "b", "install": True}, {"id": "c", "install": False}]
    assert rs.check_action({"what": "install_many", "ids": ["a", "b", "a"]}, tests) == (["a", "b"], "install_many")
    for bad in ({"what": "install_many", "ids": ["a", "c"]}, {"what": "install_many", "ids": []},
                {"what": "install_many", "ids": "a"}, {"what": "install_many", "ids": [5]}):
        assert rs.check_action(bad, tests)[0] is None, bad


def test_install_several_from_the_page_shows_the_list_then_installs_with_no_window_at_the_pc(qapp, monkeypatch):
    from app.ui import run_all
    from app.ui.remote_control import MainThread, RemoteApi
    started = []
    a, b = _entry("fio", state="missing"), _entry("sevenzip_benchmark", state="missing")
    flow = SimpleNamespace(busy=lambda: False, start=lambda entries, **kw: started.append(([e.id for e in entries],
                                                                                          kw)) or True)
    window = SimpleNamespace(tests_page=SimpleNamespace(entries=[a, b], host=None, busy=False),
                             automation_panel=SimpleNamespace(is_running=lambda: False),
                             install_flow=SimpleNamespace(busy=lambda: False), run_all_flow=flow)
    api = RemoteApi(window, MainThread())
    monkeypatch.setattr(RemoteApi, "_can_install", lambda self, e: True)
    item = run_all.Item(a, {"kind": "linux", "family": "debian", "package": "fio"})
    item.commands = [["pkexec", "apt-get", "install", "-y", "fio"]]
    monkeypatch.setattr(run_all, "collect", lambda entries, host: ([item], []))
    monkeypatch.setattr(run_all, "check_items", lambda items: items)
    ok, text = api.question(["fio", "sevenzip_benchmark"], "install_many")
    assert ok and "apt-get install -y fio" in text and "someone must type it at the PC" in text
    ok, text = api.act(["fio", "sevenzip_benchmark"], "install_many")
    assert ok and started == [(["fio", "sevenzip_benchmark"], {"install_only": True, "confirmed": True})]
    window.run_all_flow = SimpleNamespace(busy=lambda: True)
    assert api.act(["fio"], "install_many") == (False, "Something is going on at the PC; wait for it first.")


def test_a_silent_connection_is_closed(served, monkeypatch):
    """Security review (2026-10-21): a client that connects and sends nothing must not hold a thread forever."""
    import socket
    _, server, _ = served
    assert server.httpd.RequestHandlerClass.timeout == rs.REQUEST_TIMEOUT
    monkeypatch.setattr(server.httpd.RequestHandlerClass, "timeout", 0.5)
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as s:
        start = time.monotonic()
        assert s.recv(100) == b"" and time.monotonic() - start < 9     # the server hung up

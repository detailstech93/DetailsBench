"""Frame capture panel: fake process list and a fake elevated runner (no UAC, no PresentMon),
REAL CSV parsing, statistics, runner and temporary database."""

import sys
import threading
from functools import partial
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.database import Database
from app.frames.elevated import ElevatedOutcome
from app.frames.provision import Found
from app.frames.release_select import AssetChoice
from app.frames.presentmon import FrameCaptureWorkload
from app.paths import load_settings
from app.profiles import RunProfile
from app.ui.frame_capture_panel import FrameCapturePanel, list_processes, read_os_release, run_commands
from app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def writes_csv(times, outcome=None, calls=None):
    def run(executable, args, cwd, timeout, stop_event):
        if calls is not None:
            calls.append(args)
        out = args[args.index("--output_file") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write("Application,ProcessID,FrameTime\n" + "".join(f"game.exe,42,{t}\n" for t in times))
        return outcome or ElevatedOutcome("exited", exit_code=0)
    return run


@pytest.fixture
def panel(qapp, tmp_path):
    exe = tmp_path / "PresentMon.exe"
    exe.write_bytes(b"MZ")
    db = Database(str(tmp_path / "f.db"))
    p = FrameCapturePanel(db, lambda: RunProfile(name="p", repetitions=2, warmup_runs=0),
                          lambda: None, data_dir=tmp_path / "data",
                          lister=lambda: [("game.exe", 42), ("other.exe", 7)], output_root=tmp_path / "runs",
                          detector=lambda: Found(error="none"), arch="x64", platform_name="win32",
                          remote_check=lambda: False)
    p.asked = []
    p.confirm = lambda text: (p.asked.append(text), True)[1]
    p.ask_presentmon_file = lambda: str(exe)
    return p, db, tmp_path


def load_list(qapp, p):
    p.refresh_processes()
    assert p._list_worker.wait(5000)
    qapp.processEvents()


def test_select_presentmon_saves_setting(panel):
    p, _, tmp = panel
    assert "not on this PC yet; Capture frames... offers to download it" in p.path_label.text()
    p.select_presentmon()
    assert load_settings(tmp / "data")[0] == {"presentmon_path": str(tmp / "PresentMon.exe")}
    assert str(tmp / "PresentMon.exe") in p.path_label.text() and "not yet verified" not in p.path_label.text()


def test_frameview_copy_refused(panel):
    p, _, tmp = panel
    bundled = tmp / "FrameView" / "PresentMon_x64.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"MZ")
    p.ask_presentmon_file = lambda: str(bundled)
    p.select_presentmon()
    assert "Not saved" in p.status_label.text() and load_settings(tmp / "data")[0] == {}


def test_needs_process_then_offers_presentmon(qapp, panel):
    p, db, _ = panel
    assert p.start_capture() is False and "Pick the game process" in p.status_label.text()
    load_list(qapp, p)
    p.confirm_download = lambda text: (p.asked.append(text), False)[1]
    p.planner = lambda: CHOICE
    assert p.start_capture() is False                      # PresentMon missing: the download is offered first
    finish(qapp, p)
    assert len(p.asked) == 1 and "Frame capture needs PresentMon" in p.asked[0]
    assert p.status_label.text() == "Download cancelled; nothing was captured."
    assert db.get_sessions(include_test=True) == []


def test_missing_chosen_file_is_not_replaced_by_a_download(qapp, panel):
    p, _, tmp = panel
    p.select_presentmon()
    (tmp / "PresentMon.exe").unlink()
    load_list(qapp, p)
    p.planner = lambda: pytest.fail("no download for a file the user chose")
    assert p.start_capture() is False and "Select PresentMon..." in p.status_label.text()


def test_linux_capture_is_not_implemented(qapp, tmp_path):
    p = make_panel(tmp_path, platform_name="linux")
    finish(qapp, p)
    assert p.start_capture() is False and "not implemented yet" in p.status_label.text()


def test_capture_stores_fps_and_low(qapp, panel):
    p, db, _ = panel
    p.select_presentmon()
    load_list(qapp, p)
    assert p.process_combo.count() == 2 and p.process_combo.itemText(0) == "game.exe (pid 42)"
    calls = []
    times = [10.0] * 198 + [50.0, 30.0]
    p.workload_factory = partial(FrameCaptureWorkload, elevated_runner=writes_csv(times, calls=calls),
                                 version_reader=lambda path: "2.3.0")
    assert p.start_capture() is True
    assert "UAC prompt for EACH of the 2 captures" in p.asked[0] and "--process_id 42" in p.asked[0]
    assert p.worker.wait(30000)
    qapp.processEvents()
    runs = db.get_runs(p.last_session_id)
    assert [(r["status"], r["unit"]) for r in runs] == [("completed", "FPS")] * 2
    assert runs[0]["score"] == pytest.approx(1000 * 200 / sum(times))
    metrics = db.get_result_metrics(runs[0]["id"])
    assert metrics["low_1pct_fps"] == (pytest.approx(25.0), "FPS") and metrics["frames"] == (200.0, "frames")
    assert len(calls) == 2 and "1% low 25.0" in p.status_label.text()
    manifest = db.get_session_manifest(p.last_session_id)
    assert manifest["benchmarks"][0]["version"] == "2.3.0"


def test_declined_uac_cancels_the_rest(qapp, panel):
    p, db, _ = panel
    p.select_presentmon()
    load_list(qapp, p)
    p.workload_factory = partial(FrameCaptureWorkload, version_reader=lambda path: None,
                                 elevated_runner=lambda *a: ElevatedOutcome("uac_declined"))
    assert p.start_capture()
    assert p.worker.wait(30000)
    qapp.processEvents()
    runs = db.get_runs(p.last_session_id)
    assert [(r["status"], r["score"]) for r in runs] == [("cancelled", None)]
    assert "1 capture(s) not started" in p.status_label.text()


def test_list_processes_excludes_self():
    import os
    procs = list_processes()
    assert procs and os.getpid() not in [pid for _, pid in procs]


def test_main_window_shortcut_stops_both_panels(qapp, tmp_path):
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {})
    with patch.object(w.automation_panel, "emergency_stop") as a, patch.object(w.frame_panel, "emergency_stop") as f:
        w.stop_shortcut.activated.emit()
    a.assert_called_once()
    f.assert_called_once()
    with patch.object(w.frame_panel, "shutdown") as shutdown:
        w.close()
    shutdown.assert_called_once()


# ---- automatic detection, download and Linux install (fakes only: no network, no PowerShell, no pkexec) ----
CHOICE = AssetChoice(version="2.6.0", tag="v2.6.0", name="PresentMon-2.6.0-x64.exe",
                     url="https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                     size=980320, sha256="b2" * 32, asset_arch="x64")


def make_panel(tmp_path, **kw):
    kw.setdefault("detector", lambda: Found(error="none"))
    kw.setdefault("arch", "x64")
    kw.setdefault("platform_name", "win32")
    kw.setdefault("remote_check", lambda: False)
    p = FrameCapturePanel(Database(str(tmp_path / "g.db")), lambda: RunProfile(name="p"), lambda: None,
                          data_dir=tmp_path / "data", lister=lambda: [("game.exe", 42)],
                          output_root=tmp_path / "runs", **kw)
    p.asked, p.capture_asked = [], []
    p.confirm_download = lambda text: (p.asked.append(text), p.answer)[1]
    p.confirm = lambda text: (p.capture_asked.append(text), False)[1]   # the capture itself is never run here
    p.answer = True
    return p


def start_with_game(qapp, p):
    """Pick the game and press Capture frames... (PresentMon is missing, so the download is offered)."""
    finish(qapp, p)
    load_list(qapp, p)
    p.start_capture()


def finish(qapp, p):
    if p._task is not None:
        assert p._task.wait(10000)
    qapp.processEvents()


def test_auto_detect_finds_intel_install(qapp, tmp_path):
    exe = tmp_path / "PresentMon-2.6.0-x64.exe"
    exe.write_bytes(b"MZ")
    p = make_panel(tmp_path, detector=lambda: Found(str(exe), "intel install", "2.6.0"))
    finish(qapp, p)
    assert p.presentmon_path() == str(exe)
    assert str(exe) in p.path_label.text() and "[intel install]" in p.path_label.text()


def test_chosen_file_wins_over_detected(qapp, tmp_path):
    found, mine = tmp_path / "PresentMon-2.6.0-x64.exe", tmp_path / "PresentMon.exe"
    found.write_bytes(b"MZ")
    mine.write_bytes(b"MZ")
    p = make_panel(tmp_path, detector=lambda: Found(str(found), "intel install", "2.6.0"))
    finish(qapp, p)
    p.ask_presentmon_file = lambda: str(mine)
    p.select_presentmon()
    assert p.presentmon_path() == str(mine) and "[intel install]" not in p.path_label.text()


def test_download_after_confirmation(qapp, tmp_path):
    got = tmp_path / "data" / "PresentMon-2.6.0-x64.exe"
    calls = []

    def downloader(choice):
        calls.append(choice.name)
        got.parent.mkdir(parents=True, exist_ok=True)
        got.write_bytes(b"MZ")
        return Found(str(got), "download", "2.6.0", notes=["size, SHA-256 and Intel signature verified"])
    p = make_panel(tmp_path, planner=lambda: CHOICE, downloader=downloader)
    start_with_game(qapp, p)
    finish(qapp, p)
    assert len(p.asked) == 1 and CHOICE.url in p.asked[0] and CHOICE.sha256 in p.asked[0]
    assert "0.9 MB" in p.asked[0] and "emulation" not in p.asked[0]
    finish(qapp, p)
    assert calls == ["PresentMon-2.6.0-x64.exe"] and p.presentmon_path() == str(got)
    assert "[download]" in p.path_label.text()
    assert len(p.capture_asked) == 1 and "game.exe (pid 42)" in p.capture_asked[0]   # the capture continued


def test_presentmon_found_on_second_look_is_used_without_download(qapp, tmp_path):
    exe = tmp_path / "PresentMon-2.6.0-x64.exe"
    exe.write_bytes(b"MZ")
    looks = []

    def detector():
        looks.append(1)
        return Found(str(exe), "intel install", "2.6.0") if len(looks) > 1 else Found(error="none")
    p = make_panel(tmp_path, detector=detector, planner=lambda: pytest.fail("no download needed"))
    start_with_game(qapp, p)
    finish(qapp, p)
    assert p.asked == [] and p.presentmon_path() == str(exe) and len(p.capture_asked) == 1


def test_download_cancelled_downloads_nothing(qapp, tmp_path):
    calls = []
    p = make_panel(tmp_path, planner=lambda: CHOICE, downloader=lambda c: calls.append(c))
    p.answer = False
    start_with_game(qapp, p)
    finish(qapp, p)
    assert calls == [] and p.status_label.text() == "Download cancelled; nothing was captured."
    assert p.capture_asked == []


def test_arm64_download_explains_emulation(qapp, tmp_path):
    from dataclasses import replace
    p = make_panel(tmp_path, arch="arm64", planner=lambda: replace(CHOICE, emulated=True))
    p.answer = False
    start_with_game(qapp, p)
    finish(qapp, p)
    assert "no native ARM64 build" in p.asked[0] and "not verified on ARM hardware" in p.asked[0]


@pytest.mark.parametrize("planner,downloader,expected", [
    (lambda: AssetChoice(error="no published checksum for X"), None, "Download not possible: no published checksum"),
    (lambda: CHOICE, lambda c: Found(error="SHA-256 differs"), "Download failed, nothing kept: SHA-256 differs"),
    (lambda: CHOICE, lambda c: 1 / 0, "Download failed, nothing kept: division by zero"),
])
def test_download_problems_are_reported(qapp, tmp_path, planner, downloader, expected):
    p = make_panel(tmp_path, planner=planner, downloader=downloader or (lambda c: None))
    start_with_game(qapp, p)
    finish(qapp, p)
    finish(qapp, p)
    assert p.status_label.text().startswith(expected) and p.presentmon_path() is None
    assert p.capture_asked == []


def test_linux_offers_mangohud_install(qapp, tmp_path):
    detected = []
    p = make_panel(tmp_path, platform_name="linux", detector=lambda: detected.append(1))
    finish(qapp, p)
    assert detected == []
    assert "Windows-only" in p.path_label.text() and "MangoHud" in p.path_label.text()
    paths = {"pkexec": "/usr/bin/pkexec", "apt-get": "/usr/bin/apt-get"}
    ran = []
    p.command_runner = lambda commands: (ran.append(commands), "done")[1]
    p.install_mangohud({"ID": "ubuntu", "ID_LIKE": "debian"}, paths.get)
    finish(qapp, p)
    assert "/usr/bin/pkexec /usr/bin/apt-get update" in p.asked[0]
    assert ran == [[["/usr/bin/pkexec", "/usr/bin/apt-get", "update"],
                    ["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", "mangohud"]]]
    assert p.status_label.text() == "MangoHud install: done"


def test_linux_install_cancel_and_errors(qapp, tmp_path):
    p = make_panel(tmp_path, platform_name="linux", arch="arm64")
    ran = []
    p.command_runner = lambda commands: ran.append(commands)
    p.answer = False
    p.install_mangohud({"ID": "fedora"}, {"pkexec": "/usr/bin/pkexec", "dnf": "/usr/bin/dnf"}.get)
    assert "not verified" in p.asked[0] and p.status_label.text() == "Install cancelled."
    p.install_mangohud({"ID": "ubuntu"}, {"mangohud": "/usr/bin/mangohud"}.get)
    assert "already installed" in p.status_label.text()
    p.install_mangohud({"ID": "plan9"}, {}.get)
    assert "unsupported distribution" in p.status_label.text() and ran == []


class Res:
    def __init__(self, code, err=""):
        self.returncode, self.stderr, self.stdout = code, err, ""


def test_run_commands():
    seen = []

    def runner_for(*codes):
        it = iter(codes)
        return lambda argv, **k: (seen.append(argv), Res(next(it), "E: lock held\n"))[1]
    cmds = [["/usr/bin/pkexec", "apt-get", "update"], ["/usr/bin/pkexec", "apt-get", "install"]]
    assert run_commands(cmds, runner_for(0, 0)) == "done" and len(seen) == 2
    assert run_commands(cmds, runner_for(126)) == "cancelled (password prompt declined)"
    assert run_commands(cmds, runner_for(0, 100)) == "failed (exit code 100): E: lock held"

    def boom(argv, **k):
        raise OSError("no such file")
    assert run_commands(cmds, boom) == "failed: no such file"


def test_read_os_release(tmp_path):
    f = tmp_path / "os-release"
    f.write_text('# comment\nID=ubuntu\nID_LIKE="debian"\nPRETTY_NAME="Ubuntu 24.04"\n', encoding="utf-8")
    assert read_os_release(str(f)) == {"ID": "ubuntu", "ID_LIKE": "debian", "PRETTY_NAME": "Ubuntu 24.04"}
    assert read_os_release(str(tmp_path / "missing")) == {}


def test_remote_desktop_warning(qapp, tmp_path):
    exe = tmp_path / "PresentMon.exe"
    exe.write_bytes(b"MZ")
    p = make_panel(tmp_path, remote_check=lambda: True)
    finish(qapp, p)
    p.ask_presentmon_file = lambda: str(exe)
    p.select_presentmon()
    assert "Remote Desktop session" in p.path_label.text()
    p.process_combo.addItem("game.exe (pid 42)", ("game.exe", 42))
    asked = []
    p.confirm = lambda text: (asked.append(text), False)[1]
    assert p.start_capture() is False and "Remote Desktop session" in asked[0]
    (tmp_path / "b").mkdir()
    local = make_panel(tmp_path / "b")
    finish(qapp, local)
    local.ask_presentmon_file = lambda: str(exe)
    local.select_presentmon()
    assert "Remote Desktop" not in local.path_label.text()


def test_is_remote_session_never_raises():
    from app.ui.frame_capture_panel import is_remote_session
    assert is_remote_session() in (True, False)


def test_telemetry_option_reaches_the_runner(qapp, panel):
    from app.telemetry import sources
    p, db, _ = panel
    p.select_presentmon()
    load_list(qapp, p)
    p.workload_factory = partial(FrameCaptureWorkload, elevated_runner=writes_csv([10.0] * 200),
                                 version_reader=lambda path: None)
    assert p.telemetry_box.isChecked()
    assert p.start_capture()
    assert p.worker.runner.telemetry is sources.new_sampler
    assert p.worker.wait(30000)
    qapp.processEvents()
    run_dirs = [d for d in (p.output_root).rglob("frames_game_r*") if d.is_dir()]
    assert run_dirs and all((d / "telemetry.json").is_file() for d in run_dirs)
    p.telemetry_box.setChecked(False)
    assert p.start_capture()
    assert p.worker.runner.telemetry is None
    assert p.worker.wait(30000)
    qapp.processEvents()


GAMES = [{"store": "steam", "app_id": "123", "name": "Game", "install_path": "Z:/common/Game", "installed": True},
         {"store": "steam", "app_id": "9", "name": "Partial", "install_path": "Z:/common/P", "installed": False},
         {"store": "steam", "app_id": "8", "name": "Proton", "install_path": "Z:/common/X", "installed": True,
          "kind": "tool"}]


def fake_benchmark(result_status="recorded", seen=None):
    from app.automation.workload import WorkloadResult
    from app.frames.game_benchmark import GameCapture, WindowOutcome

    class Bench:
        def __init__(self, spec, run_dir, stop_event, progress, sampler_factory=None):
            if seen is not None:
                seen.append((spec, sampler_factory))
            self.spec = spec

        def run(self):
            if result_status != "recorded":
                return GameCapture(result_status, "UAC prompt declined; the game was not started")
            windows = [WindowOutcome(k < self.spec.warmup_runs,
                                     k if k < self.spec.warmup_runs else k - self.spec.warmup_runs + 1,
                                     WorkloadResult(status="completed", score=60.0 + k, unit="fps",
                                                    metrics={"low_1pct_fps": (40.0, "fps")}))
                       for k in range(self.spec.count)]
            return GameCapture("recorded", "70 s of Game.exe recorded", process=("Game.exe", 7), windows=windows)
    return Bench


def test_benchmark_game_lists_installed_games_and_stores_every_window(qapp, panel):
    p, db, _ = panel
    p.select_presentmon()
    p.games = lambda: GAMES
    p.refresh_games()
    assert [p.game_combo.itemData(i)["app_id"] for i in range(p.game_combo.count())] == ["123"]
    seen = []
    p.benchmark_factory = fake_benchmark(seen=seen)
    assert p.benchmark_game() is True
    p.worker.wait(10000)
    qapp.processEvents()
    assert "ONE UAC prompt" in p.asked[0] and "steam://rungameid/123" in p.asked[0]
    assert "closes the game" in p.asked[0] and "never Steam" in p.asked[0]
    spec, _ = seen[0]
    assert (spec.app_id, spec.repetitions, spec.warmup_runs, spec.settle_seconds) == ("123", 2, 0, 30)
    sessions = db.get_sessions(include_test=True)
    assert len(sessions) == 1 and sessions[0]["name"].startswith("Game ")
    assert p.last_session_id == sessions[0]["id"]
    assert "run 1: 60.0 fps, 1% low 40.0" in p.status_label.text()


def test_linux_game_benchmark_asks_once_then_needs_steam_closed(qapp, tmp_path):
    from app.frames import mangohud_run as mh
    p = make_panel(tmp_path, platform_name="linux")
    finish(qapp, p)
    p.games = lambda: GAMES
    p.refresh_games()
    installs = []
    p.install_mangohud = lambda **kw: installs.append(kw)
    p.linux_which = lambda name: None
    assert p.benchmark_game() is False and len(installs) == 1 and p.capture_asked == []
    p.linux_which = lambda name: "/usr/bin/" + name
    answers = [False]
    p.confirm = lambda text: (p.capture_asked.append(text), answers.pop(0))[1]
    assert p.benchmark_game() is False and "Not allowed" in p.status_label.text()
    assert "asked only this once" in p.capture_asked[0] and not load_settings(p.data_dir)[0].get(mh.CONSENT_KEY)
    seen = []
    p.benchmark_factory = fake_benchmark(seen=seen)
    answers[:] = [True, True]                   # the permission, then the run's own question
    assert p.benchmark_game() is True
    assert load_settings(p.data_dir)[0][mh.CONSENT_KEY] is True
    p.worker.wait(10000)
    qapp.processEvents()
    answers[:] = [True]                         # the next game: only the run's own question
    assert p.benchmark_game() is True
    p.worker.wait(10000)
    qapp.processEvents()
    text = p.capture_asked[-1]
    assert "steam -applaunch 123" in text and "nothing in Steam's settings is changed" in text
    assert "UAC" not in text and "steam -shutdown" in text and len(p.capture_asked) == 4
    spec, _ = seen[0]
    assert spec.presentmon_path == "/usr/bin/mangohud"
    session = p.db.get_sessions(include_test=True)[-1]
    assert "MangoHud capture of Game.exe" in session["description"]


def test_benchmark_game_that_records_nothing_creates_no_session(qapp, panel):
    p, db, _ = panel
    p.select_presentmon()
    p.games = lambda: GAMES
    p.refresh_games()
    p.benchmark_factory = fake_benchmark("cancelled")
    p.benchmark_game()
    p.worker.wait(10000)
    qapp.processEvents()
    assert db.get_sessions(include_test=True) == []
    assert p.status_label.text().startswith("cancelled: UAC prompt declined") and p.game_btn.isEnabled()


def test_benchmark_game_needs_a_game_and_offers_presentmon_first(qapp, tmp_path):
    p = make_panel(tmp_path, planner=lambda: CHOICE)
    p.answer = False
    finish(qapp, p)
    assert p.benchmark_game() is False and "No installed Steam game" in p.status_label.text()
    p.games = lambda: GAMES
    p.refresh_games()
    p.benchmark_game()
    finish(qapp, p)
    assert "Frame capture needs PresentMon" in p.asked[0] and p.capture_asked == []
    assert p.status_label.text() == "Download cancelled; nothing was captured."


class FakeForced:
    """Stands in for app.games.settings_file.ForcedSettings (no game file is touched)."""

    def __init__(self, refused=None, after=None, log=None):
        from app.games.settings_file import SettingsRecord
        self.refused, self.after, self.log = refused or {}, after, log if log is not None else []
        self.SettingsRecord = SettingsRecord

    def __call__(self, app_id, values, run_dir, data_dir):
        self.record = self.SettingsRecord("UserSettings.json", dict(values))
        self.values = values
        return self

    def apply(self):
        self.log.append("apply")
        self.record.refused = dict(self.refused)
        self.record.changed = [k for k in self.values if k not in self.refused]
        return self.record

    def finish(self, restore=True):
        self.log.append("finish")
        self.record.mismatch_after_run = {} if self.after is None else self.after
        self.record.restored = True
        return self.record


def forced_panel(qapp, panel, values):
    from app.paths import save_setting
    p, db, tmp = panel
    p.select_presentmon()
    p.games = lambda: [{"store": "steam", "app_id": "1091500", "name": "Cyberpunk 2077",
                        "install_path": "Z:/common/CP", "installed": True}]
    p.refresh_games()
    save_setting(p.data_dir, "forced_game_settings", {"1091500": values})
    p.settings_reader = lambda app_id, data_dir: ({"ray_tracing": [True, False]}, {"ray_tracing": True})
    p.dialogs = []
    p.settings_dialog_factory = lambda *a: (p.dialogs.append(a), KeepDialog(a[3]))[1]
    return p, db


class KeepDialog:
    """Accepts the window as it opened (the saved choice)."""

    def __init__(self, chosen, accept=True):
        self.chosen, self.accept = chosen, accept

    def exec(self):
        return self.accept

    def selection(self):
        return dict(self.chosen)


def run_bench(qapp, p):
    assert p.benchmark_game() is True
    p.worker.wait(10000)
    qapp.processEvents()


def test_forced_settings_are_applied_checked_and_recorded(qapp, panel):
    p, db = forced_panel(qapp, panel, {"ray_tracing": False, "dlss_mode": "Quality"})
    assert p.game_settings_btn.isEnabled()
    fake = FakeForced()
    p.forced_factory, p.benchmark_factory = fake, fake_benchmark()
    run_bench(qapp, p)
    assert p.dialogs[0][0] == "Cyberpunk 2077" and p.dialogs[0][3] == {"ray_tracing": False, "dlss_mode": "Quality"}
    assert "Forced game settings: Ray tracing Off, DLSS mode Quality" in p.asked[0]
    assert "steam://run/1091500//--launcher-skip -skipStartScreen/ is opened" in p.asked[0]
    assert fake.log == ["apply", "finish"]
    sid = db.get_sessions(include_test=True)[0]["id"]
    gs = db.get_session_manifest(sid)["run_settings"]["game_settings"]
    assert gs["requested"] == {"ray_tracing": False, "dlss_mode": "Quality"} and gs["verified"] is True
    assert all(r["status"] == "completed" for r in db.get_runs(session_id=sid, include_test=True))


def test_refused_setting_stops_before_the_game(qapp, panel):
    p, db = forced_panel(qapp, panel, {"dlss_mode": "Quality"})
    fake = FakeForced(refused={"dlss_mode": "position unknown"})
    seen = []
    p.forced_factory, p.benchmark_factory = fake, fake_benchmark(seen=seen)
    run_bench(qapp, p)
    assert seen == [] and fake.log == ["apply", "finish"] and db.get_sessions(include_test=True) == []
    assert "dlss_mode: position unknown" in p.status_label.text() and "not started" in p.status_label.text()


def test_settings_changed_by_the_game_make_runs_invalid(qapp, panel):
    p, db = forced_panel(qapp, panel, {"ray_tracing": False})
    p.forced_factory = FakeForced(after={"ray_tracing": [False, True]})
    p.benchmark_factory = fake_benchmark()
    run_bench(qapp, p)
    sid = db.get_sessions(include_test=True)[0]["id"]
    runs = db.get_runs(session_id=sid, include_test=True)
    assert runs and all(r["status"] == "invalid" for r in runs)
    assert db.get_session_manifest(sid)["run_settings"]["game_settings"]["verified"] is False


def test_no_forced_settings_says_so_and_game_without_adapter_only_declares(qapp, panel):
    p, db, _ = panel
    p.select_presentmon()
    p.games = lambda: GAMES
    p.refresh_games()
    assert p.game_settings_btn.isEnabled() and "declared, not checked" in p.game_settings_btn.toolTip()
    p.benchmark_factory = fake_benchmark()
    run_bench(qapp, p)
    assert "No game settings are forced" in p.asked[0]


def test_game_settings_dialog_choice_is_saved(qapp, panel, monkeypatch):
    p, db = forced_panel(qapp, panel, {})
    import app.games.settings_file as sf
    monkeypatch.setattr(sf, "read_for_dialog", lambda app_id, data_dir: (
        {"ray_tracing": [True, False], "rt_lighting": ["Off", "Ultra"]}, {"ray_tracing": True}))

    class Dialog:
        def __init__(self, name, choices, current, chosen):
            self.args = (name, choices, current, chosen)

        def exec(self):
            return True

        def selection(self):
            return {"rt_lighting": "Ultra"}
    p.settings_reader = None
    p.settings_dialog_factory = Dialog
    assert p.edit_game_settings() is True
    assert p.forced_settings_for("1091500") == {"rt_lighting": "Ultra"}
    assert "RT lighting Ultra" in p.status_label.text()


def test_benchmark_asks_the_settings_first_and_cancel_starts_nothing(qapp, panel):
    p, db = forced_panel(qapp, panel, {"ray_tracing": False})
    p.settings_dialog_factory = lambda *a: KeepDialog(a[3], accept=False)
    seen = []
    p.benchmark_factory = fake_benchmark(seen=seen)
    assert p.benchmark_game() is False
    assert p.asked == [] and seen == [] and p.status_label.text() == "Cancelled; nothing was started."


def test_settings_picked_at_benchmark_time_are_used(qapp, panel):
    p, db = forced_panel(qapp, panel, {})
    p.settings_dialog_factory = lambda *a: KeepDialog({"ray_tracing": False})
    fake = FakeForced()
    p.forced_factory, p.benchmark_factory = fake, fake_benchmark()
    run_bench(qapp, p)
    assert fake.values == {"ray_tracing": False} and "Ray tracing Off" in p.asked[0]
    assert p.forced_settings_for("1091500") == {"ray_tracing": False}


def test_no_settings_file_benchmarks_without_forcing(qapp, panel):
    p, db = forced_panel(qapp, panel, {})

    def missing(app_id, data_dir):
        raise OSError("the game's settings file was not found")
    p.settings_reader = missing
    p.benchmark_factory = fake_benchmark()
    run_bench(qapp, p)
    assert "No game settings are forced" in p.asked[0]


def test_declared_settings_for_a_game_the_app_cannot_force(qapp, panel):
    p, db, tmp = panel
    p.select_presentmon()
    p.games = lambda: [{"store": "steam", "app_id": "1593500", "name": "God of War",
                        "install_path": "Z:/common/GoW", "installed": True}]
    p.refresh_games()
    assert p.game_settings_btn.isEnabled()
    opened = []
    p.declared_dialog_factory = lambda *a: (opened.append(a), KeepDialog({"preset": "Ultra", "ray_tracing": False}))[1]
    assert p.edit_game_settings() is True
    assert opened[0][0] == "God of War" and "preset" in opened[0][1] and opened[0][3] == {}
    assert p.declared_settings_for("1593500") == {"preset": "Ultra", "ray_tracing": False}
    p.benchmark_factory = fake_benchmark()
    run_bench(qapp, p)
    assert "declared by you (not checked" in p.asked[0] and "Quality preset Ultra, Ray tracing Off" in p.asked[0]
    sid = db.get_sessions(include_test=True)[0]["id"]
    gs = db.get_session_manifest(sid)["run_settings"]["game_settings"]
    assert gs["declared"] == {"preset": "Ultra", "ray_tracing": False} and gs["verified"] is False
    assert all(r["status"] == "completed" for r in db.get_runs(session_id=sid, include_test=True))
    assert p.declared_settings_for("1091500") == {}                  # forced games never use declared values

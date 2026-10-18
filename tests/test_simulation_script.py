"""Simulation scripts against a fake desktop (no real window, key or process is touched)."""

import json
import threading
from pathlib import Path

from app.automation.workload import RunContext
from app.simulation import script as sim
from app.simulation.desktop import Window, parse_chord

import pytest


@pytest.fixture(autouse=True)
def no_user_settings(tmp_path, monkeypatch):
    """Steam scripts read the settings chosen for the game: never the real ones of this PC."""
    import app.paths
    monkeypatch.setattr(app.paths, "get_data_dir", lambda env=None: tmp_path / "pcbm_data")

DATA = Path(__file__).parent / "data"
SCRIPT = json.loads((Path(sim.SCRIPTS_DIR) / "sevenzip_fm_benchmark.json").read_text(encoding="utf-8"))
# control texts of 7-Zip 26.04's Benchmark window after 10 passes, development computer 2026-10-18
CONTROLS = {int(k): v for k, v in json.loads(
    (DATA / "7zfm_26.04_benchmark_controls.json").read_text(encoding="utf-8")).items()}


class FakeProc:
    def __init__(self, pid):
        self.pid, self.alive = pid, True

    def is_running(self):
        return self.alive


class FakeDesktop:
    """Plays 7zFM: its window appears on start; Alt+T then B opens the Benchmark window in a child process."""

    def __init__(self, focus_works=True, foreground=None, controls=CONTROLS, closes=True):
        self.main, self.child = FakeProc(100), FakeProc(101)
        self.started = None
        self.pressed = []
        self.closed = []
        self.terminated = []
        self.focus_works, self.fg, self.controls_text, self.closes = focus_works, foreground, controls, closes

    def start(self, command, cwd):
        self.started = (command, cwd)
        return self.main

    def tree(self, root):
        return [self.main] + ([self.child] if self.child_started() else [])

    def child_started(self):
        return "b" in self.pressed

    def windows(self, pids):
        out = []
        if self.main.alive and 100 in pids:
            out.append(Window(1, 100, "7-Zip::FM", "7-Zip"))
        if self.child_started() and self.child.alive and 101 in pids:
            out.append(Window(2, 101, "#32770", "Benchmark"))
        return out

    def controls(self, hwnd):
        return self.controls_text if hwnd == 2 else {}

    def foreground_pid(self):
        return self.fg

    def focus(self, hwnd):
        if self.focus_works:
            self.fg = 100

    def press(self, codes):
        names = {(0x12, ord("T")): "alt+t", (ord("B"),): "b"}
        self.pressed.append(names.get(tuple(codes), str(codes)))

    def close(self, hwnd):
        self.closed.append(hwnd)
        if self.closes:
            (self.main if hwnd == 1 else self.child).alive = False

    def terminate(self, procs):
        for p in procs:
            p.alive = False
            self.terminated.append(p.pid)
        return self.terminated


def workload(desktop):
    return sim.SimulationWorkload(SCRIPT, env={"PCBM_7ZIPFM": "C:/7z/7zFM.exe"},
                                  exists=lambda p: p == "C:/7z/7zFM.exe", which=lambda n: None, desktop=desktop)


def test_shipped_scripts_are_valid_and_chords_parse():
    workloads, errors = sim.load_scripts()
    assert errors == [] and [w.id for w in workloads] == ["cyberpunk2077_builtin_benchmark", "sevenzip_fm_benchmark"]
    assert parse_chord("alt+t") == [0x12, ord("T")] and parse_chord("enter") == [0x0D]
    assert parse_chord("ctrl+shift+f5") == [0x11, 0x10, 0x74] and parse_chord("win+r") is None
    bad = dict(SCRIPT, steps=[{"do": "keys", "window": "x", "keys": ["hyper+q"]}])
    problems = sim.validate_script(bad)
    assert "the first step must be launch" in problems and any("not waited for" in p for p in problems)
    assert any("key name" in p for p in problems) and "the score value is not read by any step" in problems


def test_runs_the_script_reads_the_result_and_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(sim.ScriptRun, "pause", lambda self, s: None)
    desk = FakeDesktop(foreground=None)
    result = workload(desk).run(RunContext(output_dir=tmp_path, timeout_seconds=60))
    assert result.status == "completed" and result.score == 170.013 and result.unit == "GIPS"
    assert result.metrics["compress_rating"] == (162.923, "GIPS") and result.metrics["passes"] == (10.0, None)
    assert desk.started == (["C:/7z/7zFM.exe"], str(Path("C:/7z")))
    assert desk.pressed == ["alt+t", "b"] and sorted(desk.closed) == [1, 2] and desk.terminated == []
    log = (tmp_path / sim.LOG_FILE).read_text(encoding="utf-8")
    assert "pressed alt+t" in log and "program closed" in log
    assert json.loads((tmp_path / sim.CONTROLS_FILE).read_text(encoding="utf-8"))["130"] == "170.013 GIPS"


def test_no_key_is_sent_when_another_program_has_the_focus(tmp_path, monkeypatch):
    monkeypatch.setattr(sim.ScriptRun, "pause", lambda self, s: None)
    desk = FakeDesktop(focus_works=False, foreground=999)
    result = workload(desk).run(RunContext(output_dir=tmp_path))
    assert result.status == "failed" and result.score is None and "no key was sent" in result.error
    assert desk.pressed == [] and desk.closed == [1]


def test_missing_result_is_invalid_and_stubborn_program_is_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(sim.ScriptRun, "pause", lambda self, s: None)
    monkeypatch.setattr(sim, "CLOSE_GRACE_SECONDS", 0.05)
    controls = {**CONTROLS, 130: ""}
    desk = FakeDesktop(foreground=100, controls=controls, closes=False)
    result = workload(desk).run(RunContext(output_dir=tmp_path))
    assert result.status == "invalid" and result.score is None and "total_rating" in result.error
    assert sorted(desk.terminated) == [100, 101]


def test_program_that_ends_early_fails_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "ENDED_GRACE_SECONDS", 0)
    monkeypatch.setattr(sim.ScriptRun, "pause", lambda self, s: None)
    desk = FakeDesktop(foreground=100)
    original = desk.press

    def crash_on_b(codes):
        original(codes)
        if desk.pressed[-1] == "b":
            desk.main.alive = False                   # 7-Zip crashes instead of opening the Benchmark window
            desk.pressed.append("crashed")
    desk.press = crash_on_b
    desk.child_started = lambda: False
    result = workload(desk).run(RunContext(output_dir=tmp_path, timeout_seconds=600))
    assert result.status == "failed" and result.score is None
    assert result.error == "the program ended before window 'bench' happened (closed or crashed)"


def test_game_closed_before_its_result_fails_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "ENDED_GRACE_SECONDS", 0)
    steam = FakeSteam(None)
    original = steam.launch

    def launch_then_quit(*args):
        res = original(*args)
        steam.procs.pop(50)
        steam.procs[51] = "Cyberpunk2077.exe"            # e.g. Steam restarts the exe once ...
        return res
    steam.launch = launch_then_quit
    wl = sim.SimulationWorkload(GAME, env={"USERPROFILE": str(tmp_path)}, desktop=GameDesktop(),
                                find_game=lambda appid: "Z:/g", steam_hooks=steam)
    monkeypatch.setattr(sim.ScriptRun, "pause", lambda self, s: steam.procs.pop(51, None) and None)
    result = wl.run(RunContext(output_dir=tmp_path / "run", timeout_seconds=600))
    assert result.status == "failed" and "the program ended before" in result.error   # ... then the user quits


def test_cancel_timeout_and_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(sim, "CLOSE_GRACE_SECONDS", 0.05)
    stop = threading.Event()
    stop.set()
    assert workload(FakeDesktop(foreground=100)).run(RunContext(output_dir=tmp_path, stop_event=stop)).status == "cancelled"
    never = FakeDesktop(foreground=100)
    never.windows = lambda pids: []
    timed = workload(never).run(RunContext(output_dir=tmp_path, timeout_seconds=0.05))
    assert timed.status == "timed_out" and timed.score is None and never.terminated == [100]   # no window: ended
    missing = sim.SimulationWorkload(SCRIPT, env={}, exists=lambda p: False, which=lambda n: None)
    assert missing.availability()[0] is False and missing.run(RunContext(output_dir=tmp_path)).status == "skipped"


GAME = json.loads((Path(sim.SCRIPTS_DIR) / "cyberpunk2077_benchmark.json").read_text(encoding="utf-8"))


class FakeSteam:
    """Plays Steam and the game: launching adds a process in the folder that writes a result file."""

    def __init__(self, result_text, status="launched", frames_text=None):
        self.result_text, self.status, self.frames_text = result_text, status, frames_text
        self.procs, self.closed, self.launched = {7: "steam_helper"}, None, None
        self.write_to = None

    def launch(self, appid, folder, args, timeout, clock):
        self.launched = (appid, folder, args)
        if self.status == "launched":
            self.procs[50] = "Cyberpunk2077.exe"
            if self.result_text is not None:
                self.write_to.parent.mkdir(parents=True)
                self.write_to.write_text(self.result_text, encoding="utf-8")
                if self.frames_text is not None:
                    (self.write_to.parent / "frames.csv").write_text(self.frames_text, encoding="utf-8")
        return {"status": self.status, "message": "new process in the game folder: Cyberpunk2077.exe"}

    def folder_procs(self, folder):
        return dict(self.procs)

    def close(self, started):
        self.closed = sorted(started)
        return "closed politely"

    def alive(self, info):
        return info in self.procs.values()


class GameDesktop(FakeDesktop):
    def windows(self, pids):
        return [Window(9, 50, "W2ViewportClass", "Cyberpunk 2077")] if 50 in pids else []

    def screenshot(self, path):
        Path(path).write_bytes(b"png")


def run_game(tmp_path, monkeypatch, steam, timeout=60, **kw):
    monkeypatch.setattr(sim.ScriptRun, "pause", lambda self, s: None)
    steam.write_to = tmp_path / "home" / "Documents" / "CD Projekt Red" / "Cyberpunk 2077" / "benchmarkResults" / "b1" / "summary.json"
    wl = sim.SimulationWorkload(GAME, env={"USERPROFILE": str(tmp_path / "home")}, desktop=GameDesktop(),
                                find_game=lambda appid: "Z:/Steam/common/Cyberpunk 2077" if appid == 1091500 else None,
                                steam_hooks=steam, **kw)
    out = tmp_path / "run"
    return wl, wl.run(RunContext(output_dir=out, timeout_seconds=timeout)), out


def test_steam_game_script_reads_the_result_file_and_closes_only_new_processes(tmp_path, monkeypatch):
    # the JSON layout (Data.averageFps ...) matches the summary.json of the real run on 2026-10-18
    steam = FakeSteam(json.dumps({"Data": {"averageFps": 88.5, "minFps": 70.25, "maxFps": 120}}))
    wl, result, out = run_game(tmp_path, monkeypatch, steam)
    assert wl.availability() == (True, "found (Steam library)") and wl.verified is True
    assert result.status == "completed" and result.score == 88.5 and result.unit == "FPS"
    assert result.metrics == {"min_fps": (70.25, "FPS"), "max_fps": (120.0, "FPS")}
    assert steam.launched == (1091500, "Z:/Steam/common/Cyberpunk 2077", ["--launcher-skip", "-skipStartScreen"])
    assert steam.closed == [50]                                   # never the helper that ran before
    assert (out / "result_b1_summary.json").exists() and (out / "screenshot_menu.png").exists()


def test_steam_game_failures_never_score(tmp_path, monkeypatch):
    _, wrong_keys, out = run_game(tmp_path / "a", monkeypatch, FakeSteam(json.dumps({"fps": 90})))
    assert wrong_keys.status == "invalid" and wrong_keys.score is None and "average_fps" in wrong_keys.error
    assert (out / "result_b1_summary.json").exists()                 # kept to fix the script
    _, not_json, _ = run_game(tmp_path / "b", monkeypatch, FakeSteam("<html>"))
    assert not_json.status == "invalid" and not_json.score is None
    steam = FakeSteam(None, status="unconfirmed")
    _, no_start, _ = run_game(tmp_path / "c", monkeypatch, steam)
    assert no_start.status == "failed" and steam.closed is None
    monkeypatch.setattr(sim, "POLL_SECONDS", 0.01)
    _, no_file, _ = run_game(tmp_path / "d", monkeypatch, FakeSteam(None), timeout=0.2)
    assert no_file.status == "timed_out" and no_file.score is None
    missing = sim.SimulationWorkload(GAME, find_game=lambda appid: None)
    assert missing.availability()[0] is False


SUMMARY = {"Data": {"averageFps": 76.43, "minFps": 65.5, "maxFps": 104.4, "renderWidth": 2560,
                    "renderHeight": 1440, "presetName": "Ultra", "DLSSEnabled": True, "FSR3Enabled": False,
                    "rayTracingEnabled": False, "rayTracedPathTracingEnabled": False, "verticalSync": False,
                    "gameVersion": "2.31"}}
FRAMES = "Frame index, Frame time (ms), DLSS Frame Generation (ms)\n" + "".join(
    f"{i}, {40.0 if i < 10 else 10.0}, 0\n" for i in range(1000))


def test_game_frames_and_settings_used(tmp_path, monkeypatch):
    _, result, out = run_game(tmp_path, monkeypatch, FakeSteam(json.dumps(SUMMARY), frames_text=FRAMES))
    assert result.status == "completed" and result.score == 76.43
    assert result.metrics["low_1pct_fps"] == (25.0, "FPS")          # the 10 slowest of 1000 frames: 40 ms
    assert result.metrics["low_01pct_fps"] == (25.0, "FPS")
    assert result.note == ("Settings used: Resolution 2560x1440, Preset Ultra, Upscaler DLSS, Frame generation Off, "
                           "Ray tracing Off, Path tracing Off, VSync Off, Game version 2.31")
    assert (out / "frames_b1_frames.csv").exists()


def test_game_without_frames_file_still_scores(tmp_path, monkeypatch):
    _, result, out = run_game(tmp_path, monkeypatch, FakeSteam(json.dumps(SUMMARY)))
    assert result.status == "completed" and "low_1pct_fps" not in result.metrics
    assert "1% low not measured" in (out / sim.LOG_FILE).read_text(encoding="utf-8")


def test_settings_text_and_frame_times():
    data = {"D": {"w": 1920, "h": 1080.0, "a": True, "b": False, "rt": True}}
    assert sim.settings_text(data, {"paths": [["D", "w"], ["D", "h"]], "join": "x"}) == "1920x1080"
    assert sim.settings_text(data, {"any_true": {"A": ["D", "a"], "B": ["D", "b"]}, "none": "Off"}) == "A"
    assert sim.settings_text(data, {"any_true": {"B": ["D", "b"]}, "none": "Off"}) == "Off"
    assert sim.settings_text(data, {"path": ["D", "rt"]}) == "On"
    assert sim.settings_text(data, {"path": ["D", "missing"]}) is None
    assert sim.frame_times("a, Frame time (ms)\n0, 10\n1, x\n2, -1\n3, 12.5\n", "frame time (ms)") == [10.0, 12.5]
    assert sim.frame_times("a,b\n1,2\n", "Frame time (ms)") == []


class FakeForced:
    def __init__(self, refused=None, mismatch_after=None, fail=None):
        from app.games.settings_file import SettingsRecord
        self.record = SettingsRecord("UserSettings.json", {"ray_tracing": True})
        self.refused, self.mismatch_after, self.fail = refused or {}, mismatch_after, fail
        self.calls = []

    def __call__(self, app_id, values, run_dir, data_dir):
        self.calls.append(("new", str(app_id), dict(values)))
        return self

    def apply(self):
        self.calls.append("apply")
        if self.fail:
            raise OSError(self.fail)
        self.record.refused = dict(self.refused)
        return self.record

    def finish(self):
        self.calls.append("finish")
        self.record.mismatch_after_run = self.mismatch_after or {}
        self.record.restored = True
        return self.record


def save_choice(data_dir, values):
    from app.games.settings_file import SAVED_KEY
    from app.paths import save_setting
    save_setting(data_dir, SAVED_KEY, {"1091500": values})


def test_chosen_settings_are_forced_then_put_back(tmp_path, monkeypatch):
    save_choice(tmp_path / "data", {"ray_tracing": True})
    forced = FakeForced()
    steam = FakeSteam(json.dumps(SUMMARY))
    _, result, out = run_game(tmp_path, monkeypatch, steam, data_dir=tmp_path / "data", forced_factory=forced)
    assert forced.calls == [("new", "1091500", {"ray_tracing": True}), "apply", "finish"]
    assert result.status == "completed" and "Forced: ray_tracing True" in result.note
    assert json.loads((out / "game_settings.json").read_text(encoding="utf-8"))["restored"] is True


def test_settings_problems_never_score(tmp_path, monkeypatch):
    save_choice(tmp_path / "data", {"ray_tracing": True})
    steam = FakeSteam(json.dumps(SUMMARY))
    refused = FakeForced(refused={"ray_tracing": "unknown"})
    _, r1, _ = run_game(tmp_path / "a", monkeypatch, steam, data_dir=tmp_path / "data", forced_factory=refused)
    assert r1.status == "failed" and "settings not set" in r1.error and refused.calls[-1] == "finish"
    assert steam.launched is None                                   # the game was never started
    broken = FakeForced(fail="file locked")
    _, r2, _ = run_game(tmp_path / "b", monkeypatch, steam, data_dir=tmp_path / "data", forced_factory=broken)
    assert r2.status == "failed" and "file locked" in r2.error and broken.calls[-1] == "finish"
    changed = FakeForced(mismatch_after={"ray_tracing": [True, False]})
    _, r3, _ = run_game(tmp_path / "c", monkeypatch, steam, data_dir=tmp_path / "data", forced_factory=changed)
    assert r3.status == "invalid" and r3.score is None and "did not keep" in r3.error


def test_nothing_forced_without_a_choice(tmp_path, monkeypatch):
    forced = FakeForced()
    _, result, _ = run_game(tmp_path, monkeypatch, FakeSteam(json.dumps(SUMMARY)), data_dir=tmp_path / "data",
                            forced_factory=forced)
    assert forced.calls == [] and result.status == "completed" and "Forced" not in (result.note or "")

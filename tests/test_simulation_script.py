"""Simulation scripts against a fake desktop (no real window, key or process is touched)."""

import json
import threading
from pathlib import Path

from app.automation.workload import RunContext
from app.simulation import script as sim
from app.simulation.desktop import Window, parse_chord

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

    def __init__(self, result_text, status="launched"):
        self.result_text, self.status = result_text, status
        self.procs, self.closed, self.launched = {7: "steam_helper"}, None, None
        self.write_to = None

    def launch(self, appid, folder, args, timeout, clock):
        self.launched = (appid, folder, args)
        if self.status == "launched":
            self.procs[50] = "Cyberpunk2077.exe"
            if self.result_text is not None:
                self.write_to.parent.mkdir(parents=True)
                self.write_to.write_text(self.result_text, encoding="utf-8")
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


def run_game(tmp_path, monkeypatch, steam, timeout=60):
    monkeypatch.setattr(sim.ScriptRun, "pause", lambda self, s: None)
    steam.write_to = tmp_path / "home" / "Documents" / "CD Projekt Red" / "Cyberpunk 2077" / "benchmarkResults" / "b1" / "summary.json"
    wl = sim.SimulationWorkload(GAME, env={"USERPROFILE": str(tmp_path / "home")}, desktop=GameDesktop(),
                                find_game=lambda appid: "Z:/Steam/common/Cyberpunk 2077" if appid == 1091500 else None,
                                steam_hooks=steam)
    out = tmp_path / "run"
    return wl, wl.run(RunContext(output_dir=out, timeout_seconds=timeout)), out


def test_steam_game_script_reads_the_result_file_and_closes_only_new_processes(tmp_path, monkeypatch):
    # the JSON layout is this script's (unverified) assumption, not a recorded file
    steam = FakeSteam(json.dumps({"Data": {"averageFps": 88.5, "minFps": 70.25, "maxFps": 120}}))
    wl, result, out = run_game(tmp_path, monkeypatch, steam)
    assert wl.availability() == (True, "found (Steam library)") and wl.verified is False
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

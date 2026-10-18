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
    assert errors == [] and [w.id for w in workloads] == ["sevenzip_fm_benchmark"]
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

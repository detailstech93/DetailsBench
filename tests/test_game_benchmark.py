"""Whole game benchmark with fakes: no UAC prompt, no Steam, no game, no PresentMon."""

import threading
import time

from app.automation.runner import AutomationRunner
from app.database import Database
from app.frames.elevated import ElevatedOutcome
from app.frames.game_benchmark import GameBenchmark, GameBenchmarkSpec, plan_for
from app.telemetry.sampler import TelemetryResult

HEADER = "Application,ProcessID,MsBetweenPresents\n"


def csv_text(game_frames, frame_ms=50.0):
    rows = []
    for i in range(game_frames):
        rows.append(f"Game.exe,7,{frame_ms}")
        if i % 10 == 0:
            rows.append("dwm.exe,99,16.7")           # other processes are recorded too
    return HEADER + "\n".join(rows) + "\n"


class Fakes:
    def __init__(self, tmp_path, game_frames=2000, uac="accept", launch="launched"):
        self.install = tmp_path / "steamapps" / "common" / "Game"
        self.now = [0.0]
        self.calls = []
        self.game_frames = game_frames
        self.uac = uac
        self.launch = launch

    def clock(self):
        return self.now[0]

    def sleep(self, seconds):
        self.now[0] += seconds
        time.sleep(0.001)

    def elevated(self, exe, args, cwd, timeout, halt):
        self.calls.append(("presentmon", list(args)))
        if self.uac == "decline":
            return ElevatedOutcome("uac_declined", error="the UAC prompt was declined")
        out = args[args.index("--output_file") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write(csv_text(self.game_frames))
        halt.wait(10)
        return ElevatedOutcome("stopped", exit_code=1, terminated=True)

    def launcher(self, appid, install, appids, **kw):
        self.calls.append(("launch", appid))
        return {"status": self.launch, "message": "no new process" if self.launch != "launched" else "ok"}

    def snapshot(self):
        return [("Game.exe", str(self.install / "bin" / "Game.exe")), ("dwm.exe", r"C:\Windows\System32\dwm.exe")]


def spec(**kw):
    values = dict(game_name="Game", app_id="123", install_path="", discovered_appids=["123"],
                  presentmon_path="C:/pm/PresentMon.exe", seconds=20, warmup_runs=1, repetitions=2,
                  settle_seconds=10)
    values.update(kw)
    return GameBenchmarkSpec(**values)


def run(tmp_path, fakes, stop=None, sampler=None, **kw):
    s = spec(install_path=str(fakes.install), **kw)
    bench = GameBenchmark(s, tmp_path / "run", stop, lambda m: None, sampler_factory=sampler,
                          elevated_runner=fakes.elevated, launcher=fakes.launcher, snapshot=fakes.snapshot,
                          clock=fakes.clock, sleep=fakes.sleep)
    return s, bench.run()


def test_one_recording_becomes_warmup_and_measured_windows(tmp_path):
    fakes = Fakes(tmp_path)
    s, cap = run(tmp_path, fakes)
    assert cap.status == "recorded" and cap.process == ("Game.exe", 7)
    assert [c[0] for c in fakes.calls] == ["presentmon", "launch"]          # PresentMon (UAC) before the game
    args = fakes.calls[0][1]
    assert "--process_name" not in args and "--process_id" not in args and "--terminate_after_timed" in args
    assert [(w.is_warmup, w.run_index) for w in cap.windows] == [(True, 0), (False, 1), (False, 2)]
    for w in cap.windows:
        assert w.result.status == "completed" and abs(w.result.score - 20.0) < 1e-6 and w.result.unit == "fps"
    assert [w.result.metrics["window_start_s"][0] for w in cap.windows] == [10.0, 30.0, 50.0]


def test_window_the_recording_does_not_reach_is_invalid_without_score(tmp_path):
    fakes = Fakes(tmp_path, game_frames=1100)                              # 55 s of game time
    fakes_stall = fakes
    s, cap = run(tmp_path, fakes_stall)
    statuses = [w.result.status for w in cap.windows]
    assert statuses == ["completed", "completed", "invalid"]
    assert cap.windows[2].result.score is None and "ended at 55 s" in cap.windows[2].result.error


def test_declined_uac_starts_no_game(tmp_path):
    fakes = Fakes(tmp_path, uac="decline")
    _, cap = run(tmp_path, fakes)
    assert cap.status == "cancelled" and "UAC" in cap.message and cap.windows == []
    assert [c[0] for c in fakes.calls] == ["presentmon"]


def test_game_that_does_not_start_stops_presentmon_and_stores_nothing(tmp_path):
    fakes = Fakes(tmp_path, launch="unconfirmed")
    _, cap = run(tmp_path, fakes)
    assert cap.status == "failed" and "did not start" in cap.message and cap.windows == []


def test_stop_before_frames_is_cancelled(tmp_path):
    fakes = Fakes(tmp_path)
    stop = threading.Event()
    fakes.snapshot = lambda: (stop.set(), [])[1]                           # stop pressed during the first check
    _, cap = run(tmp_path, fakes, stop=stop)
    assert cap.status == "cancelled" and cap.windows == []


class FakeSampler:
    def __init__(self, samples):
        self.samples = samples

    def start(self):
        return self

    def stop(self):
        return TelemetryResult(samples=self.samples, unavailable={"LibreHardwareMonitor": "not running"})


def test_windows_are_stored_like_runs_with_their_telemetry(tmp_path):
    fakes = Fakes(tmp_path)
    # the live check sees 100 s of game time at sampler time 0, so the game's time 0 is at -100 s
    samples = [(t - 100.0, {"gpu_temp": (float(t), "C")}) for t in range(0, 100, 5)]
    s, cap = run(tmp_path, fakes, sampler=lambda: FakeSampler(samples))
    assert [len(w.samples) for w in cap.windows] == [4, 4, 4]
    db = Database(str(tmp_path / "g.db"))
    sid = db.create_session("g", is_manual=False)
    summary = AutomationRunner(db, sid, plan_for(cap, s), tmp_path / "runs").run()
    assert [(r.is_warmup, r.run_index, r.result.status) for r in summary.records] == \
        [(True, 0, "completed"), (False, 1, "completed"), (False, 2, "completed")]
    assert summary.records[0].workload_id == "frames_game"
    assert (summary.records[1].run_dir / "telemetry.csv").is_file()
    metrics = db.get_result_metrics(summary.records[1].result_id)
    assert metrics["tel_gpu_temp_avg"][0] == 37.5 and metrics["avg_fps"][0] == 20.0

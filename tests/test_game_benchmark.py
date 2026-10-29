"""Whole game benchmark with fakes: no UAC prompt, no Steam, no game, no PresentMon."""

import threading
import time

from app.automation.runner import AutomationRunner
from app.database import Database
from app.frames.elevated import ElevatedOutcome
from app.frames.game_close import ProcInfo, close_game, processes_in_folder
from app.frames.game_benchmark import GameBenchmark, GameBenchmarkSpec, game_exe_names, plan_for
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
        self.launched = False
        self.closed = []
        self.old = {3: ProcInfo(3, 1.0, "old.exe")}           # already running before: never closed
        self.game_closed = threading.Event()

    def folder_procs(self):
        game = {7: ProcInfo(7, 2.0, str(self.install / "bin" / "Game.exe"))}
        return {**self.old, **game} if self.launched and self.launch == "launched" else dict(self.old)

    def closer(self, started):
        self.closed.append(sorted(started))
        self.game_closed.set()
        return "game closed (Game.exe)"

    def clock(self):
        return self.now[0]

    def sleep(self, seconds):
        self.now[0] += seconds
        time.sleep(0.001)

    def elevated(self, exe, args, cwd, timeout, halt, on_started=None):
        self.calls.append(("presentmon", list(args)))
        if self.uac == "decline":
            return ElevatedOutcome("uac_declined", error="the UAC prompt was declined")
        on_started()
        while not (self.game_closed.is_set() or halt.is_set()):     # like --terminate_on_proc_exit
            time.sleep(0.001)
        if self.game_closed.is_set():                                # the CSV is complete only after exit
            out = args[args.index("--output_file") + 1]
            with open(out, "w", encoding="utf-8") as f:
                f.write(csv_text(self.game_frames))
            return ElevatedOutcome("exited", exit_code=0)
        return ElevatedOutcome("stopped", exit_code=1, terminated=True)

    def launcher(self, appid, install, appids, **kw):
        self.calls.append(("launch", appid))
        self.launch_kw = kw
        self.launched = True
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
    bench = GameBenchmark(s, tmp_path / "run", stop, lambda m: None, linux=False, sampler_factory=sampler,
                          elevated_runner=fakes.elevated, launcher=fakes.launcher, snapshot=fakes.snapshot,
                          clock=fakes.clock, sleep=fakes.sleep, folder_procs=fakes.folder_procs,
                          closer=fakes.closer, exe_lister=lambda folder: ["Game.exe", "Launcher.exe"])
    return s, bench.run()


def test_one_recording_becomes_warmup_and_measured_windows(tmp_path):
    fakes = Fakes(tmp_path)
    s, cap = run(tmp_path, fakes)
    assert cap.status == "recorded" and cap.process == ("Game.exe", 7)
    assert [c[0] for c in fakes.calls] == ["presentmon", "launch"]          # PresentMon (UAC) before the game
    args = fakes.calls[0][1]
    assert args[:4] == ["--process_name", "Game.exe", "--process_name", "Launcher.exe"]
    assert "--terminate_on_proc_exit" in args and "--process_id" not in args
    assert [(w.is_warmup, w.run_index) for w in cap.windows] == [(True, 0), (False, 1), (False, 2)]
    for w in cap.windows:
        assert w.result.status == "completed" and abs(w.result.score - 20.0) < 1e-6 and w.result.unit == "FPS"
    assert [w.result.metrics["window_start_s"][0] for w in cap.windows] == [10.0, 30.0, 50.0]
    assert fakes.closed == [[7]] and cap.message.endswith("; game closed (Game.exe)")


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
    assert [c[0] for c in fakes.calls] == ["presentmon"] and fakes.closed == []


def test_game_that_does_not_start_stops_presentmon_and_stores_nothing(tmp_path):
    fakes = Fakes(tmp_path, launch="unconfirmed")
    _, cap = run(tmp_path, fakes)
    assert cap.status == "failed" and "did not start" in cap.message and cap.windows == []
    assert fakes.closed == [] and cap.message.endswith("; no game process to close")
    assert not (tmp_path / "run" / "presentmon.csv").exists()               # PresentMon was stopped


def test_stop_closes_the_game_and_unreached_windows_are_cancelled(tmp_path):
    fakes = Fakes(tmp_path, game_frames=500)                                 # 25 s of game time
    stop = threading.Event()
    procs = fakes.folder_procs
    fakes.folder_procs = lambda: (stop.set() if fakes.launched else None, procs())[1]
    _, cap = run(tmp_path, fakes, stop=stop)
    assert fakes.closed == [[7]]                                             # stopped: the game is closed too
    assert cap.status == "recorded" and [w.result.status for w in cap.windows] == ["cancelled"] * 3


def test_no_frames_from_the_game_is_a_failure(tmp_path):
    fakes = Fakes(tmp_path, game_frames=0)
    _, cap = run(tmp_path, fakes)
    assert cap.status == "failed" and "no frames from the game (started: Game.exe" in cap.message


def test_wait_is_settle_plus_windows_with_room_for_loading(tmp_path):
    fakes = Fakes(tmp_path)
    s, cap = run(tmp_path, fakes)
    assert s.wait_seconds() == (10 + 20 * 3) * 1.15 + 20
    assert fakes.now[0] >= s.wait_seconds()


def test_game_exe_names_walks_the_folder(tmp_path):
    walk = lambda folder: [(folder, [], ["Game.EXE", "readme.txt"]), (folder + "/bin", [], ["game.exe", "Crash.exe"])]
    assert game_exe_names("G", walk=walk) == ["Crash.exe", "Game.EXE"]


class FakeSampler:
    def __init__(self, samples):
        self.samples = samples

    def start(self):
        return self

    def stop(self):
        return TelemetryResult(samples=self.samples, unavailable={"LibreHardwareMonitor": "not running"})


def test_windows_are_stored_like_runs_with_their_telemetry(tmp_path):
    fakes = Fakes(tmp_path)
    samples = [(float(t), {"gpu_temp": (float(t), "C")}) for t in range(0, 110)]   # one per second
    s, cap = run(tmp_path, fakes, sampler=lambda: FakeSampler(samples))
    assert [len(w.samples) for w in cap.windows] == [20, 20, 20]
    db = Database(str(tmp_path / "g.db"))
    sid = db.create_session("g", is_manual=False)
    summary = AutomationRunner(db, sid, plan_for(cap, s), tmp_path / "runs").run()
    assert [(r.is_warmup, r.run_index, r.result.status) for r in summary.records] == \
        [(True, 0, "completed"), (False, 1, "completed"), (False, 2, "completed")]
    assert summary.records[0].workload_id == "frames_game"
    assert (summary.records[1].run_dir / "telemetry.csv").is_file()
    metrics = db.get_result_metrics(summary.records[1].result_id)
    # game time 0 = close request - 100 s recorded; the window 30-50 s holds 20 consecutive samples
    assert metrics["tel_gpu_temp_avg"][1] == "C" and metrics["avg_fps"][0] == 20.0
    assert metrics["tel_gpu_temp_max"][0] - metrics["tel_gpu_temp_avg"][0] == 9.5


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_processes_in_folder_only_inside_the_install_folder(tmp_path):
    folder = str(tmp_path / "Game")
    procs = [{"pid": 7, "exe": str(tmp_path / "Game" / "bin" / "game.exe"), "create_time": 2.0},
             {"pid": 8, "exe": str(tmp_path / "GameOther" / "x.exe"), "create_time": 2.0},
             {"pid": 9, "exe": None, "create_time": 2.0},
             {"pid": 1, "exe": r"C:\Steam\steam.exe", "create_time": 1.0}]
    assert list(processes_in_folder(folder, lambda: procs)) == [7]


def test_polite_close_first():
    clock, gone = Clock(), set()
    game = ProcInfo(7, 2.0, r"Z:\Game\Game.exe")
    polite = lambda pids: gone.update(pids)
    forced = []
    text = close_game({7: game}, alive=lambda i: i.pid not in gone, polite=polite,
                      force=lambda i: forced.append(i), sleep=clock.sleep, clock=clock)
    assert text == "game closed (Game.exe)" and forced == []


def test_force_after_the_grace_time_and_problems_reported():
    clock, forced = Clock(), []
    game = ProcInfo(7, 2.0, r"Z:\Game\Game.exe")
    text = close_game({7: game}, grace_seconds=30, alive=lambda i: True, polite=lambda pids: 0,
                      force=lambda i: forced.append(i.pid), sleep=clock.sleep, clock=clock)
    assert forced == [7] and clock.now >= 30 and text == "game closed by force after 30 s (Game.exe)"
    text = close_game({7: game}, alive=lambda i: True, polite=lambda pids: 0, force=lambda i: "access denied",
                      sleep=clock.sleep, clock=clock)
    assert "could not be closed (Game.exe: access denied); close it yourself" in text
    assert close_game({7: game}, alive=lambda i: False) == "the game was already closed"


def test_launch_arguments_reach_the_launcher_only_when_set(tmp_path):
    fakes = Fakes(tmp_path)
    run(tmp_path, fakes)
    assert "args" not in fakes.launch_kw
    fakes = Fakes(tmp_path)
    run(tmp_path, fakes, launch_args=["--launcher-skip"])
    assert fakes.launch_kw["args"] == ["--launcher-skip"]


class FakeDesktop:
    """The foreground window's pid follows a list of (from second, pid); presses are recorded."""

    def __init__(self, fakes, front):
        self.fakes, self.front, self.pressed = fakes, front, []

    def foreground_pid(self):
        pid = None
        for at, who in self.front:
            if self.fakes.now[0] >= at:
                pid = who
        return pid

    def press(self, codes):
        self.pressed.append((self.fakes.now[0], codes))


def test_a_game_behind_this_app_is_brought_to_the_front(tmp_path):
    from types import SimpleNamespace
    from app.simulation.desktop import parse_chord
    fakes = Fakes(tmp_path)
    desk = FakeDesktop(fakes, [(0, 999)])                           # this app stays in front by itself
    desk.windows = lambda pids: [SimpleNamespace(hwnd=70, pid=p) for p in sorted(pids)]
    desk.focus = lambda hwnd: desk.front.append((fakes.now[0], 7))
    s = spec(install_path=str(fakes.install), start_keys=[(5.0, "enter")])
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                          launcher=fakes.launcher, snapshot=fakes.snapshot, clock=fakes.clock, sleep=fakes.sleep,
                          folder_procs=fakes.folder_procs, closer=fakes.closer,
                          exe_lister=lambda folder: ["Game.exe"], desktop=desk)
    cap = bench.run()
    assert cap.status == "recorded" and [codes for _, codes in desk.pressed] == [parse_chord("enter")]


def test_start_keys_go_only_into_the_game_when_it_is_in_front(tmp_path):
    from app.simulation.desktop import parse_chord
    fakes = Fakes(tmp_path)
    desk = FakeDesktop(fakes, [(0, 7), (30, 999), (50, 7)])       # another program is in front at 30-50 s
    s = spec(install_path=str(fakes.install),
             start_keys=[(40.0, "esc"), (5.0, "enter"), (60.0, "nokey"), (61.0, "down")])
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                          launcher=fakes.launcher, snapshot=fakes.snapshot, clock=fakes.clock, sleep=fakes.sleep,
                          folder_procs=fakes.folder_procs, closer=fakes.closer,
                          exe_lister=lambda folder: ["Game.exe"], desktop=desk)
    cap = bench.run()
    assert cap.status == "recorded"
    assert [codes for _, codes in desk.pressed] == [parse_chord("enter"), parse_chord("down")]
    assert all(at >= 5 for at, _ in desk.pressed)
    assert "esc at 40 s not sent: the game was not in front" in cap.message
    assert "'nokey' at 60 s: unknown key, not sent" in cap.message


def test_no_start_keys_never_touches_the_desktop(tmp_path):
    fakes = Fakes(tmp_path)
    s, cap = run(tmp_path, fakes)
    assert cap.status == "recorded" and "start keys" not in cap.message


def test_start_keys_data_is_well_formed():
    from app.simulation.desktop import parse_chord
    from data.game_start_keys import GAME_START_KEYS, start_keys_for
    assert start_keys_for("not a number") == [] and start_keys_for(1) == []
    for app_id, entry in GAME_START_KEYS.items():
        assert isinstance(app_id, int) and isinstance(entry.get("verified"), bool) and entry.get("note")
        for at, chord in start_keys_for(app_id):
            assert at >= 0 and parse_chord(chord) is not None


def test_no_capture_file_says_how_presentmon_ended():
    from app.frames.elevated import ElevatedOutcome
    from app.frames.game_benchmark import presentmon_text
    from app.frames.presentmon import capture_game_args
    text = presentmon_text(ElevatedOutcome("exited", exit_code=1, duration_seconds=3.2), 2.6)
    assert text == (" (PresentMon ended by itself 3 s after the game was launched, before the app closed the game; "
                    "PresentMon: exit code 1 after 3 s)")
    assert presentmon_text(None, None) == ""
    assert "stopped by the app after 9 s" in presentmon_text(ElevatedOutcome("stopped", duration_seconds=9), None)
    args = capture_game_args("x.csv", ["Game.exe"], 60)
    assert args[-3:] == ["--session_name", "PCBenchmarkManager", "--stop_existing_session"]


def test_the_capture_message_is_kept_in_the_run_folder(tmp_path):
    fakes = Fakes(tmp_path)
    s, cap = run(tmp_path, fakes)
    log = (tmp_path / "run" / "capture_log.txt").read_text(encoding="utf-8")
    assert f"status: {cap.status}" in log and cap.message in log and "PresentMon" in log


def test_keys_wait_for_the_game_window_of_a_slow_loading_game(tmp_path):
    # The Witcher 3 from a network share showed its window 3.7 min after the launch (2026-10-18)
    from app.simulation.desktop import parse_chord
    fakes = Fakes(tmp_path)
    desk = FakeDesktop(fakes, [(0, 999), (200, 7)])                # loading: another window in front
    s = spec(install_path=str(fakes.install), start_keys=[(5.0, "enter")])
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                          launcher=fakes.launcher, snapshot=fakes.snapshot, clock=fakes.clock, sleep=fakes.sleep,
                          folder_procs=fakes.folder_procs, closer=fakes.closer,
                          exe_lister=lambda folder: ["Game.exe"], desktop=desk)
    cap = bench.run()
    assert cap.status == "recorded"
    assert [codes for _, codes in desk.pressed] == [parse_chord("enter")]
    assert desk.pressed[0][0] >= 205
    assert "the game came to the front 200 s after the launch" in cap.message
    assert s.max_record_seconds() > spec().max_record_seconds()


def test_a_game_that_restarts_itself_does_not_end_the_capture():
    """Far Cry 6 (2026-10-23): FarCry6.exe ends after about 5 s and Ubisoft Connect starts it again."""
    from app.frames.presentmon import capture_game_args
    from data.game_start_keys import relaunches_for
    assert "--terminate_on_proc_exit" in capture_game_args("x.csv", ["Game.exe"], 60)
    assert "--terminate_on_proc_exit" not in capture_game_args("x.csv", ["Game.exe"], 60, until_exit=False)
    assert relaunches_for(2369390) and not relaunches_for(1174180) and not relaunches_for("x")

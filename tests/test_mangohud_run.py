"""Linux game benchmark with MangoHud, all faked: no Steam, no game, no MangoHud (runs on any host)."""

import os
import re
import threading
import time
from pathlib import Path

from app.frames import mangohud_run as mh
from app.frames.game_benchmark import GameBenchmark, GameBenchmarkSpec
from app.frames.game_close import ProcInfo, processes_in_folder, unix_path

HEAD = ("os,cpu,gpu,ram,kernel,driver,cpuscheduler\nUbuntu,CPU,GPU,32GB,6.8,Mesa,none\n"
        "fps,frametime,cpu_load,gpu_load,elapsed\n")


def log_text(frames, frame_ms=50.0):
    return HEAD + "".join(f"20,{frame_ms},10,90,{int((i + 1) * frame_ms * 1e6)}\n" for i in range(frames))


def test_config_environment_and_steam_command(tmp_path):
    text = mh.config_text(tmp_path / "logs", 123)
    assert f"output_folder={tmp_path / 'logs'}" in text and "log_interval=0" in text
    assert "log_duration=123" in text and "autostart_log=1" in text
    assert "no_display" not in text and "alpha=0" in text and "legacy_layout=0" in text   # no_display: no autostart
    assert "toggle_logging=Shift_L+F2" in text          # the key that ends the log before the game is closed
    env = mh.environment(tmp_path / "MangoHud.conf", base={"PATH": "/usr/bin", "MANGOHUD_CONFIG": "fps_limit=30"})
    assert env == {"PATH": "/usr/bin", "MANGOHUD": "1", "MANGOHUD_CONFIGFILE": str(tmp_path / "MangoHud.conf")}
    assert mh.steam_command("1091500", ["-benchmark"], which=lambda n: "/usr/games/steam") == \
        ["/usr/games/steam", "-applaunch", "1091500", "-benchmark"]
    assert mh.steam_command("1", which=lambda n: None) is None
    assert mh.steam_running(lambda: ["bash", "Steam"]) and not mh.steam_running(lambda: ["steamwebhelper", "x"])


def test_a_finished_steam_shutdown_left_as_a_zombie_is_not_steam(monkeypatch):
    import psutil

    class P:
        def __init__(self, status):
            self.info = {"name": "steam", "status": status}
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [P(psutil.STATUS_ZOMBIE)])
    assert not mh.steam_running() and mh.steam_state(Path("x")) == "closed"
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [P(psutil.STATUS_ZOMBIE), P(psutil.STATUS_SLEEPING)])
    assert mh.steam_running()


def test_proton_processes_are_found_by_their_command_line(tmp_path):
    assert unix_path("Z:\\home\\me\\Game\\game.exe") == "/home/me/Game/game.exe"
    procs = [{"pid": 1, "create_time": 1.0, "exe": "/opt/proton/files/bin/wine64-preloader",
              "cmdline": ["Z:\\home\\me\\steamapps\\common\\Game\\bin\\Game.exe", "-dx12"]},
             {"pid": 2, "create_time": 1.0, "exe": "/home/me/steamapps/common/Game/game.x86_64", "cmdline": []},
             {"pid": 3, "create_time": 1.0, "exe": "/usr/bin/steam", "cmdline": ["/usr/bin/steam"]},
             {"pid": 4, "create_time": 1.0, "exe": "/usr/bin/wineserver", "cmdline": ["C:\\windows\\x.exe"]}]
    found = processes_in_folder("/home/me/steamapps/common/Game", lambda: procs, linux=True)
    assert sorted(found) == [1, 2] and found[1].exe.endswith("Game.exe")
    assert sorted(processes_in_folder("/home/me/steamapps/common/Game", lambda: procs, linux=False)) == [2]
    # a library on another drive is its own Wine drive (Fedora 2026-11-19: the NTFS library was S:)
    lib = "/run/media/me/System/SteamLibrary/steamapps/common/Borderlands 3"
    bl3 = [{"pid": 5, "create_time": 1.0, "exe": "/opt/proton/files/bin/wine64-preloader",
            "cmdline": ["S:\\steamapps\\common\\Borderlands 3\\OakGame\\Binaries\\Win64\\Borderlands3.exe"]},
           {"pid": 6, "create_time": 1.0, "exe": "/x/wine", "cmdline": ["S:\\steamapps\\common\\Other\\o.exe"]},
           {"pid": 7, "create_time": 1.0, "exe": "/x/wine", "cmdline": ["c:\\windows\\system32\\steam.exe"]}]
    found = processes_in_folder(lib, lambda: bl3, linux=True)
    assert sorted(found) == [5] and found[5].exe == lib + "/OakGame/Binaries/Win64/Borderlands3.exe"


class Fakes:
    def __init__(self, tmp_path, frames=2000, state="closed", mangohud=True, writes_log=True, exits=True):
        self.install = tmp_path / "steamapps" / "common" / "Game"
        self.data = tmp_path / "data"
        self.now = [0.0]
        self.started = None
        self.frames, self.state, self.mangohud, self.writes_log = frames, state, mangohud, writes_log
        self.exits, self.shutdowns = exits, 0
        self.closed = []
        self.log_at = 30.0              # fake seconds when MangoHud's log (and summary) appear

    # Hooks
    def mangohud_installed(self):
        return self.mangohud

    def folder(self):
        return self.data / "mangohud"

    def steam_state(self, config):
        assert config == self.folder() / "MangoHud.conf"
        return self.state

    def shutdown(self):
        self.shutdowns += 1
        if self.exits:
            self.state = "closed"
            return None
        return "Steam was still open 120 s after steam -shutdown"

    def command(self, app_id, args):
        return ["/usr/games/steam", "-applaunch", str(app_id)] + list(args)

    def start(self, command, env):
        self.started = (command, env)
        config = Path(env["MANGOHUD_CONFIGFILE"]).read_text(encoding="utf-8")
        self.logs = Path(re.search(r"^output_folder=(.+)$", config, re.M).group(1))

    def folder_procs(self):
        game = {7: ProcInfo(7, 2.0, str(self.install / "Game.exe"))}
        return game if self.started else {}

    def closer(self, started):
        self.closed.append(sorted(started))
        return "game closed (Game.exe)"

    def clock(self):
        return self.now[0]

    def sleep(self, seconds):
        self.now[0] += seconds
        if self.started and self.writes_log and self.now[0] >= self.log_at and not (self.logs / "done").exists():
            (self.logs / "fossilize_replay_2026-10-19_10-00-00.csv").write_text(log_text(5000), encoding="utf-8")
            (self.logs / "Game.exe_2026-10-19_10-00-01.csv").write_text(log_text(self.frames), encoding="utf-8")
            (self.logs / "Game.exe_2026-10-19_10-00-01_summary.csv").write_text("x\n", encoding="utf-8")
            (self.logs / "done").write_text("", encoding="utf-8")
        time.sleep(0.001)


def run(tmp_path, fakes, stop=None, **kw):
    values = dict(game_name="Game", app_id="123", install_path=str(fakes.install), discovered_appids=["123"],
                  presentmon_path="/usr/bin/mangohud", seconds=20, warmup_runs=1, repetitions=2, settle_seconds=10)
    values.update(kw)
    s = GameBenchmarkSpec(**values)
    bench = GameBenchmark(s, tmp_path / "run", stop, lambda m: None, linux=True, mangohud_hooks=fakes,
                          clock=fakes.clock, sleep=fakes.sleep, folder_procs=fakes.folder_procs,
                          closer=fakes.closer, remote_session=lambda: False)
    return s, bench.run()


def test_a_linux_run_starts_steam_with_mangohud_and_reads_the_game_log(tmp_path):
    fakes = Fakes(tmp_path)
    s, cap = run(tmp_path, fakes, launch_args=["-benchmark"])
    assert cap.status == "recorded", cap.message
    command, env = fakes.started
    assert command == ["/usr/games/steam", "-applaunch", "123", "-benchmark"] and env["MANGOHUD"] == "1"
    assert f"log_duration={int(s.wait_seconds())}" in (tmp_path / "run" / "MangoHud.conf").read_text()
    assert cap.process == ("Game.exe", None) and "recorded by MangoHud" in cap.message   # not fossilize's log
    assert fakes.closed == [[7]]
    assert [w.result.status for w in cap.windows] == ["completed"] * 3
    assert all(abs(w.result.score - 20.0) < 0.01 for w in cap.windows)


def test_linux_runs_that_cannot_start_start_nothing(tmp_path):
    for fakes, words in ((Fakes(tmp_path, state="other", exits=False), "did not exit"),
                         (Fakes(tmp_path, mangohud=False), "MangoHud is not installed")):
        _, cap = run(tmp_path, fakes)
        assert cap.status == "failed" and words in cap.message and fakes.started is None


def test_no_log_says_vulkan_and_short_logs_leave_windows_invalid(tmp_path):
    fakes = Fakes(tmp_path, writes_log=False)
    _, cap = run(tmp_path, fakes)
    assert cap.status == "failed" and "Vulkan" in cap.message and fakes.closed == [[7]]
    fakes = Fakes(tmp_path / "b", frames=1100)                 # 55 s of game time: the 50-70 s window is not reached
    _, cap = run(tmp_path / "b", fakes)
    assert [w.result.status for w in cap.windows] == ["completed", "completed", "invalid"]
    assert cap.windows[2].result.score is None


def test_stop_while_steam_starts(tmp_path):
    fakes = Fakes(tmp_path)
    fakes.folder_procs = lambda: {}
    stop = threading.Event()
    stop.set()
    _, cap = run(tmp_path, fakes, stop=stop)
    assert cap.status == "cancelled" and "no game process to close" in cap.message and fakes.now[0] >= 120
    # Fedora 2026-11-20: Steam started the game after the stop and it ran on; it is waited for and closed
    late = Fakes(tmp_path / "b")
    late.folder_procs = lambda: {7: ProcInfo(7, 2.0, str(late.install / "Game.exe"))} if late.now[0] >= 40 else {}
    _, cap = run(tmp_path / "b", late, stop=stop)
    assert cap.status == "cancelled" and late.closed == [[7]] and late.now[0] < 120


def test_several_games_reuse_the_apps_steam_and_a_users_steam_is_asked_to_exit(tmp_path):
    ours = Fakes(tmp_path / "a", state="ours")
    _, cap = run(tmp_path / "a", ours)
    assert cap.status == "recorded" and ours.shutdowns == 0 and ours.started is not None
    logs = tmp_path / "a" / "run" / "mangohud_logs"
    assert (logs / "Game.exe_2026-10-19_10-00-01.csv").is_file()           # moved into the run folder
    assert not (ours.folder() / "mangohud_logs" / "Game.exe_2026-10-19_10-00-01.csv").exists()
    users = Fakes(tmp_path / "b", state="other")
    _, cap = run(tmp_path / "b", users)
    assert cap.status == "recorded" and users.shutdowns == 1


def test_which_steam_is_the_apps_and_the_clean_exit(tmp_path):
    conf = tmp_path / "MangoHud.conf"
    mine = {"MANGOHUD": "1", "MANGOHUD_CONFIGFILE": str(conf)}
    assert mh.steam_state(conf, lambda: []) == "closed"
    assert mh.steam_state(conf, lambda: [mine, mine]) == "ours"
    assert mh.steam_state(conf, lambda: [mine, None]) == "other"
    assert mh.steam_state(conf, lambda: [{"MANGOHUD": "1"}]) == "other"
    started, now, alive = [], [0.0], [True, True, False]
    why = mh.shutdown_steam(lambda n: "/usr/games/steam", running=lambda: alive.pop(0),
                            start=started.append, sleep=lambda s: now.__setitem__(0, now[0] + s),
                            clock=lambda: now[0])
    assert why is None and started == [["/usr/games/steam", "-shutdown"]]
    why = mh.shutdown_steam(lambda n: "/usr/games/steam", running=lambda: True, start=lambda c: None,
                            sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0], wait=10)
    assert "still open" in why


def test_linux_menu_steps_measure_from_the_benchmark_start(tmp_path):
    from types import SimpleNamespace
    from app.frames.game_benchmark import steps_bound_seconds
    from tests.test_menu_steps import FakeSteps
    fakes = Fakes(tmp_path, frames=3000)            # 150 s of frames: menus first, then the benchmark
    fakes.log_at = 10000.0                          # MangoHud logs until its key ends it (log_duration is longer)
    steps = [{"see": "options", "within": 30, "click": "options"}, {"see": "stop", "within": 60, "mark": True}]
    s = GameBenchmarkSpec(game_name="Game", app_id="123", install_path=str(fakes.install), discovered_appids=["123"],
                          presentmon_path="/usr/bin/mangohud", seconds=20, warmup_runs=1, repetitions=2,
                          settle_seconds=10, steps=steps)
    player = FakeSteps(fakes, mark_after=40)
    plain_sleep, stamped = fakes.sleep, []

    def sleep(seconds):                             # the log's last frame is written when the game closes: its
        plain_sleep(seconds)                        # time says when its first frame was (150 s of frames earlier)
        log = fakes.logs / "Game.exe_2026-10-19_10-00-01.csv" if fakes.started else None
        if log is not None and log.exists() and not stamped:
            stamped.append(begin[0])         # the run's start: slow sleeps on Windows do not move it
            os.utime(log, (stamped[0] + 150, stamped[0] + 150))
    keys = []                                       # MangoHud writes the log only when its key ends logging
    desk = SimpleNamespace(foreground_pid=lambda: 7, key_down=lambda c: keys.append(("down", c)),
                           key_up=lambda c: keys.append(("up", c)) or fakes.now.__setitem__(0, fakes.log_at))
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=True, mangohud_hooks=fakes,
                          clock=fakes.clock, sleep=sleep, folder_procs=fakes.folder_procs,
                          closer=fakes.closer, remote_session=lambda: False,
                          desktop=desk, step_player=lambda st: player)
    begin = [time.time()]
    cap = bench.run()
    assert cap.status == "recorded", cap.message
    assert keys == [("down", [0x10, 0x71]), ("up", [0x10, 0x71])]     # Shift+F2: MangoHud ends and writes its log
    assert f"log_duration={int(s.wait_seconds()) + steps_bound_seconds(steps)}" in \
        (tmp_path / "run" / "MangoHud.conf").read_text()
    starts = [w.result.metrics["window_start_s"][0] for w in cap.windows]
    # 10-30, 30-50, 50-70 s moved past the 40 s in the menus (less the moment between the launch and the stamp)
    assert all(abs(a - b) < 2 for a, b in zip(starts, [50.0, 70.0, 90.0])), starts
    assert "menu steps: test" in cap.message and fakes.closed == [[7]]
    assert steps_bound_seconds(steps) == 30 + 60 + 60


def test_linux_menu_steps_that_fail_or_cannot_run(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests.test_menu_steps import FakeSteps
    fakes = Fakes(tmp_path)
    fakes.log_at = 1e9
    s = GameBenchmarkSpec(game_name="Game", app_id="123", install_path=str(fakes.install), discovered_appids=["123"],
                          presentmon_path="/usr/bin/mangohud", seconds=20, steps=[{"see": "stop", "within": 9,
                                                                                    "mark": True}])
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=True, mangohud_hooks=fakes,
                          clock=fakes.clock, sleep=fakes.sleep, folder_procs=fakes.folder_procs,
                          closer=fakes.closer, desktop=SimpleNamespace(foreground_pid=lambda: 7),
                          step_player=lambda st: FakeSteps(fakes, problem="step 1: 'stop' did not appear"))
    cap = bench.run()
    assert cap.status == "failed" and "did not reach the game's benchmark" in cap.message
    assert "'stop' did not appear" in cap.message and fakes.closed == [[7]]
    import app.simulation.desktop as desktop
    from app.simulation.x11_desktop import X11Desktop
    monkeypatch.setattr(desktop, "default_desktop", lambda: None)
    monkeypatch.setattr(X11Desktop, "why", "this is a Wayland session")
    fakes = Fakes(tmp_path / "b")
    bench = GameBenchmark(s, tmp_path / "b" / "run", None, lambda m: None, linux=True, mangohud_hooks=fakes,
                          clock=fakes.clock, sleep=fakes.sleep, folder_procs=fakes.folder_procs, closer=fakes.closer)
    cap = bench.run()
    assert cap.status == "failed" and "Wayland" in cap.message and fakes.started is None


class FakeDesk:
    """The game's window is in front (pid 7); keys and mouse moves are recorded."""

    def __init__(self):
        self.events = []

    def foreground_pid(self):
        return 7

    def key_down(self, codes):
        self.events.append(("down", tuple(codes)))

    def key_up(self, codes):
        self.events.append(("up", tuple(codes)))

    def press(self, codes):
        self.events.append(("press", tuple(codes)))

    def mouse_move(self, dx, dy):
        self.events.append(("move", dx, dy))


def test_linux_route_is_played_and_its_keys_released(tmp_path, monkeypatch):
    from app.simulation.desktop import parse_chord
    route = {"loop_from": 5, "loop_seconds": 10, "loop": [[0, "hold", "w", 4], [5, "mouse", 300, 0, 1]]}
    fakes = Fakes(tmp_path)
    desk = FakeDesk()
    s = GameBenchmarkSpec(game_name="Game", app_id="123", install_path=str(fakes.install), discovered_appids=["123"],
                          presentmon_path="/usr/bin/mangohud", seconds=20, warmup_runs=1, repetitions=2,
                          settle_seconds=10, route=route)
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=True, mangohud_hooks=fakes,
                          clock=fakes.clock, sleep=fakes.sleep, folder_procs=fakes.folder_procs,
                          closer=fakes.closer, remote_session=lambda: False, desktop=desk)
    cap = bench.run()
    assert cap.status == "recorded", cap.message
    w = tuple(parse_chord("w"))
    downs = [e for e in desk.events if e == ("down", w)]
    ups = [e for e in desk.events if e == ("up", w)]
    assert downs and len(ups) >= len(downs)            # every held key let go (also at the end)
    assert sum(e[1] for e in desk.events if e[0] == "move") % 300 == 0 and "route: " in cap.message
    import app.simulation.desktop as desktop
    from app.simulation.x11_desktop import X11Desktop
    monkeypatch.setattr(desktop, "default_desktop", lambda: None)
    monkeypatch.setattr(X11Desktop, "why", "no X11 display (DISPLAY is not set)")
    fakes = Fakes(tmp_path / "b")
    _, cap = run(tmp_path / "b", fakes, route=route)
    assert cap.status == "failed" and "DISPLAY" in cap.message and fakes.started is None


def test_linux_whole_benchmark_starts_the_game_for_every_run_like_windows(tmp_path, monkeypatch):
    """Owner, 2026-11-19: on Linux as on Windows. BL3 had been closed 60 s after its start, before its benchmark
    ended: no results page and no own report. Now each run is one launch, its whole benchmark from the mark, with the
    game's own number next to the app's."""
    from types import SimpleNamespace
    from tests.test_menu_steps import FakeSteps
    fakes = Fakes(tmp_path, frames=3000)            # 150 s of frames at 20 FPS
    fakes.log_at = 1e12                             # written when the app's key ends the log

    def logged(c):
        fakes.log_at = fakes.now[0]

    def closer(started):
        fakes.closed.append(sorted(started))
        (fakes.logs / "done").unlink()              # the next launch writes a log of its own
        fakes.log_at, fakes.started = 1e12, None     # closed: the next launch starts a new one
        return "game closed (Game.exe)"
    desk = SimpleNamespace(foreground_pid=lambda: 7, key_down=lambda c: None, key_up=logged)
    begin, plain_sleep = time.time(), fakes.sleep
    monkeypatch.setattr(time, "time", lambda: begin + fakes.now[0])     # the wall clock follows the fake one

    def sleep(seconds):                             # the log's last frame is written when MangoHud writes the file
        plain_sleep(seconds)
        for log in fakes.logs.glob("*.csv") if fakes.started else ():
            os.utime(log, (time.time(), time.time()))
    s = GameBenchmarkSpec(game_name="Game", app_id="123", install_path=str(fakes.install), discovered_appids=["123"],
                          presentmon_path="/usr/bin/mangohud", warmup_runs=1, repetitions=1, benchmark_seconds=60,
                          steps=[{"see": "stop", "within": 60, "mark": True}])
    assert s.whole_benchmark
    launches = []
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=True, mangohud_hooks=fakes,
                          clock=fakes.clock, sleep=sleep, folder_procs=fakes.folder_procs, closer=closer,
                          remote_session=lambda: False, desktop=desk, own_result=lambda app_id, since: {"avg": 20.0},
                          step_player=lambda st: launches.append(1) or FakeSteps(fakes, mark_after=40))
    cap = bench.run()
    assert cap.status == "recorded", cap.message
    assert len(launches) == 2 and len(fakes.closed) == 2 and "2 of 2 runs reached the benchmark" in cap.message, cap.message
    assert [(w.is_warmup, w.run_index) for w in cap.windows] == [(True, 0), (False, 1)]
    for k, w in enumerate(cap.windows):
        assert w.result.status == "completed", w.result.error
        assert abs(w.result.duration_seconds - 60) < 1 and round(w.result.score) == 20
        assert "the game 20 (+0.0%: they match)" in w.result.note and w.result.metrics["game_avg_fps"][0] == 20.0
        assert list((tmp_path / "run" / f"run_{k}" / "mangohud_logs").glob("Game.exe_*.csv"))
    conf = (tmp_path / "run" / "MangoHud.conf").read_text()
    assert "log_duration=" in conf and int(conf.split("log_duration=")[1].split()[0]) >= 60


CPU_HEAD = HEAD.replace("Ubuntu,CPU,GPU,", "Steam Runtime 4,CPU,llvmpipe (LLVM 23.1.5),")


def test_the_device_is_read_from_the_first_lines_of_a_new_log(tmp_path):
    (tmp_path / "Game.exe_2026-11-20_09-02-44.csv").write_text(CPU_HEAD + "20,50,10,90,1\n", encoding="utf-8")
    (tmp_path / "fossilize_replay_2026-11-20_09-02-40.csv").write_text(HEAD, encoding="utf-8")
    assert mh.drawing_device(tmp_path, 0) == "llvmpipe (LLVM 23.1.5)"
    assert mh.drawing_device(tmp_path, time.time() + 60) is None             # an old log is not this run's
    (tmp_path / "Other.exe_2026-11-20_09-03-00.csv").write_text("os,cpu", encoding="utf-8")
    assert mh.drawing_device(tmp_path / "none", 0) is None
    # Fedora 2026-11-20: an RDR2 left running wrote its llvmpipe log during FC6's run: only the game's own logs
    (tmp_path / "FarCry6_2026-11-20_11-39-49.csv").write_text(HEAD + "20,50,10,90,1\n", encoding="utf-8")
    assert mh.drawing_device(tmp_path, 0, ["FarCry6.exe", "upc.exe"]) == "GPU"


def test_a_game_on_the_cpu_is_stopped_as_soon_as_mangohud_names_it(tmp_path):
    """Fedora 2026-11-20: RDR2 drew on llvmpipe; the app waited for its whole benchmark while RAM and swap filled."""
    fakes = Fakes(tmp_path, frames=20000)       # 1000 s of frames, if it had been let run
    fakes.log_at = 5.0
    plain = fakes.sleep

    def sleep(seconds):
        plain(seconds)
        for log in fakes.logs.glob("Game.exe_*[0-9].csv") if fakes.started else ():
            text = log.read_text(encoding="utf-8")
            if text.startswith(HEAD):
                log.write_text(CPU_HEAD + text[len(HEAD):], encoding="utf-8")
    fakes.sleep = sleep
    _, cap = run(tmp_path, fakes, seconds=900)
    assert cap.status == "failed" and cap.on_cpu and "llvmpipe" in cap.message, cap.message
    assert "stopped as soon as MangoHud named it" in cap.message and fakes.closed == [[7]]
    assert fakes.now[0] < 60                                              # not the 900 s it would have run

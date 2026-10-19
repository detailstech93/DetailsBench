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
    assert "log_duration=123" in text and "autostart_log=1" in text and "no_display" in text
    env = mh.environment(tmp_path / "MangoHud.conf", base={"PATH": "/usr/bin", "MANGOHUD_CONFIG": "fps_limit=30"})
    assert env == {"PATH": "/usr/bin", "MANGOHUD": "1", "MANGOHUD_CONFIGFILE": str(tmp_path / "MangoHud.conf")}
    assert mh.steam_command("1091500", ["-benchmark"], which=lambda n: "/usr/games/steam") == \
        ["/usr/games/steam", "-applaunch", "1091500", "-benchmark"]
    assert mh.steam_command("1", which=lambda n: None) is None
    assert mh.steam_running(lambda: ["bash", "Steam"]) and not mh.steam_running(lambda: ["steamwebhelper", "x"])


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
    fakes = Fakes(tmp_path)
    _, cap = run(tmp_path, fakes, route={"loop_from": 0})
    assert cap.status == "failed" and "Windows only" in cap.message and fakes.started is None


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
    assert cap.status == "cancelled"


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
    fakes.log_at = 120.0                            # MangoHud still logging when the measured time is over
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
            stamped.append(time.time())
            os.utime(log, (stamped[0] + 150, stamped[0] + 150))
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=True, mangohud_hooks=fakes,
                          clock=fakes.clock, sleep=sleep, folder_procs=fakes.folder_procs,
                          closer=fakes.closer, remote_session=lambda: False,
                          desktop=SimpleNamespace(foreground_pid=lambda: 7), step_player=lambda st: player)
    cap = bench.run()
    assert cap.status == "recorded", cap.message
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

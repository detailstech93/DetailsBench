"""Linux game benchmark with MangoHud, all faked: no Steam, no game, no MangoHud (runs on any host)."""

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
    def __init__(self, tmp_path, frames=2000, steam_open=False, mangohud=True, writes_log=True):
        self.install = tmp_path / "steamapps" / "common" / "Game"
        self.now = [0.0]
        self.started = None
        self.frames, self.steam_open, self.mangohud, self.writes_log = frames, steam_open, mangohud, writes_log
        self.closed = []

    # Hooks
    def mangohud_installed(self):
        return self.mangohud

    def steam_running(self):
        return self.steam_open

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
        if self.started and self.writes_log and self.now[0] >= 30 and not (self.logs / "done").exists():
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
    for fakes, words in ((Fakes(tmp_path, steam_open=True), "Steam is open"),
                         (Fakes(tmp_path, mangohud=False), "MangoHud is not installed")):
        _, cap = run(tmp_path, fakes)
        assert cap.status == "failed" and words in cap.message and fakes.started is None
    fakes = Fakes(tmp_path)
    _, cap = run(tmp_path, fakes, steps=[{"see": "options"}])
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

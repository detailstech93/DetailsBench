"""Checked menu steps (read the screen, then press) and Windows OCR, with fakes: no game, no screen, no keys."""

import base64
import subprocess
from types import SimpleNamespace

from app.frames import screen_text
from app.frames.game_benchmark import GameBenchmark
from app.frames.menu_steps import StepPlayer, step_problems
from app.simulation.desktop import parse_chord
from data.game_start_keys import GAME_MENU_STEPS, menu_steps_for
from tests.test_game_benchmark import Fakes, spec


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


class Desk:
    def __init__(self, clock):
        self.clock, self.events = clock, []

    def press(self, codes):
        self.events.append(("press", codes))

    def key_down(self, codes):
        self.events.append(("down", codes, self.clock.now))

    def key_up(self, codes):
        self.events.append(("up", codes, self.clock.now))

    def mouse_to(self, x, y):
        self.events.append(("mouse", (x, y)))

    def mouse_button(self, down):
        self.events.append(("button", down, self.clock.now))


def boxed(lines):
    """Lines as OCR reads them: line k at y = 100 k, each word 100 px wide, one after another."""
    out = []
    for k, text in enumerate(lines):
        line = screen_text.Line(text)
        line.words = tuple((w, 100 * j, 100 * k, 80, 20) for j, w in enumerate(text.split()))
        out.append(line)
    return out


def screens(clock, timeline):
    """A reader that shows the lines of the last (from second, lines) reached, and writes the 'screenshot'."""
    def reader(desktop, path):
        lines = []
        for at, shown in timeline:
            if clock.now >= at:
                lines = boxed(shown)
        path.write_bytes(b"png:" + "|".join(lines).encode())
        return lines, None
    return reader


def player(tmp_path, steps, timeline, front=lambda: True):
    clock = Clock()
    desk = Desk(clock)
    p = StepPlayer(steps, desk, front, tmp_path, clock=clock, sleep=clock.sleep, reader=screens(clock, timeline))
    return p, desk, clock


def test_rdr2_path_presses_only_after_each_word_and_marks_the_start(tmp_path):
    timeline = [(0, ["Rockstar Games"]), (60, ["A strange man"]), (61, ["Story Mode", "Online", "Settings"]),
                (70, ["Graphics", "Display", "Audio"]), (76, ["Resolution", "Run Benchmark Test"]), (85, []),
                (120, ["Stop Benchmarking"])]
    p, desk, clock = player(tmp_path, menu_steps_for(1174180), timeline)
    p.run()
    assert p.problem is None and 120 <= p.mark_time < 122
    keys = [e[1] if e[0] == "press" else (e[0], e[1]) for e in desk.events]
    assert keys == [parse_chord("enter"), parse_chord("z"), ("mouse", (40, 10)), ("button", True), ("button", False),
                    ("down", parse_chord("x")), ("up", parse_chord("x")), parse_chord("enter")]
    assert "clicked 'graphics' at 40,10" in p.summary()
    buttons = [e[2] for e in desk.events if e[0] == "button"]
    assert round(buttons[1] - buttons[0], 6) == 0.15
    held = [e for e in desk.events if e[0] in ("down", "up")]
    assert held[1][2] - held[0][2] == 2.5
    assert "closed a popup ('strange')" in p.summary() and "benchmark started ('stop' seen at 12" in p.summary()
    assert (tmp_path / "benchmark_started.png").read_bytes() == b"png:Stop Benchmarking"
    assert p.started_shot == str(tmp_path / "benchmark_started.png")


def test_a_word_that_never_comes_stops_without_guessing(tmp_path):
    steps = [{"see": "settings", "within": 30, "press": "z"}, {"see": "stop", "within": 5, "mark": True}]
    p, desk, clock = player(tmp_path, steps, [(0, ["Loading", "Please wait"])])
    p.run()
    assert desk.events == [] and p.mark_time is None and p.done.is_set()
    assert p.problem.startswith("step 1: 'settings' did not appear within 30 s; the screen showed: Loading, "
                                "Please wait (screenshot ")
    assert (tmp_path / "step_1_not_found.png").is_file()


def test_no_key_when_the_game_is_not_in_front(tmp_path):
    p, desk, clock = player(tmp_path, [{"press": "enter"}, {"see": "x", "within": 1, "mark": True}], [],
                            front=lambda: False)
    p.run()
    assert desk.events == [] and p.problem == "enter not sent: the game was not in front (another window was)"


def test_optional_word_and_a_black_screen(tmp_path):
    steps = [{"see": "popup", "within": 3, "optional": True, "press": "enter"},
             {"see": "menu", "within": 3, "mark": True}]
    p, desk, clock = player(tmp_path, steps, [(0, [])])
    p.run()
    assert desk.events == [] and "'popup' not seen (optional)" in p.summary()
    assert "no words at all (a black screenshot? use borderless window)" in p.problem


def test_without_a_windows_desktop_nothing_is_pressed(tmp_path):
    p = StepPlayer([{"press": "enter"}], None, lambda: True, tmp_path)
    p.run()
    assert p.problem == "keys and screen reading need Windows or a Linux X11 session"


def test_steps_data_is_checked():
    for app_id, entry in GAME_MENU_STEPS.items():
        assert isinstance(entry["verified"], bool) and entry["note"] and step_problems(entry["steps"]) == []
    assert menu_steps_for(1) is None and menu_steps_for("x") is None
    assert step_problems([{"see": "a", "press": "nokey"}]) == [
        "step 1: unknown key 'nokey'", "step 1: 'see' needs 'within' seconds", "no step marks the start of the benchmark"]
    assert step_problems([]) == ["no steps"]


def test_ocr_command_passes_the_path_inside_the_encoded_script(tmp_path):
    image = tmp_path / "it's.png"
    command = screen_text.ocr_command(image)
    assert command[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(command[4]).decode("utf-16-le")
    assert str(image.resolve()).replace("'", "''") in script and "Windows.Media.Ocr.OcrEngine" in script


def test_read_image_lines_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_text, "_on_windows", lambda: True)     # not os.name: pathlib reads it too
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    ok = lambda *a, **k: SimpleNamespace(returncode=0, stdout="Story Mode\r\n\r\n Settings \r\n".encode(), stderr=b"")
    boxes = lambda *a, **k: SimpleNamespace(returncode=0, stderr=b"",
                                            stdout="Story Mode\r\nSettings\x1f12|30|80|20|Settings\r\n".encode())
    assert screen_text.read_image(tmp_path / "a.png", run=ok) == (["Story Mode", "Settings"], None)
    lines = screen_text.read_image(tmp_path / "a.png", run=boxes)[0]
    assert lines[1].words == (("Settings", 12, 30, 80, 20),) and screen_text.word_center(lines, "sett") == (52, 40)
    assert screen_text.word_center(lines, "story") is None          # a line without word boxes
    bad = lambda *a, **k: SimpleNamespace(returncode=3, stdout=b"", stderr=b"no OCR language is installed in Windows\n")
    assert screen_text.read_image(tmp_path / "a.png", run=bad) == ([], "Windows OCR failed: no OCR language is "
                                                                     "installed in Windows")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("powershell.exe", 30)
    assert "did not answer" in screen_text.read_image(tmp_path / "a.png", run=slow)[1]
    monkeypatch.setattr(screen_text, "_on_windows", lambda: False)       # Linux: Tesseract
    monkeypatch.setattr(screen_text.shutil, "which", lambda name: None)
    assert "Tesseract (the OCR program for Linux) is not installed" in screen_text.read_image(tmp_path / "a.png",
                                                                                              run=ok)[1]
    assert screen_text.find_word(["Run Benchmark Test"], ["stop", "BENCHMARK"]) == "BENCHMARK"
    assert screen_text.find_word([], "x") is None


class FakeSteps:
    """Sees the benchmark start `mark_after` s after it was started, or gives up with a problem."""

    def __init__(self, fakes, mark_after=None, problem=None):
        self.fakes, self.mark_after, self.problem = fakes, mark_after, problem
        self.stop = SimpleNamespace(set=lambda: None)
        self.done = SimpleNamespace(is_set=lambda: self.problem is not None and self.fakes.now[0] >= self.t + 5)

    def start(self):
        self.t = self.fakes.now[0]
        return self

    @property
    def mark_time(self):
        if self.mark_after is not None and self.fakes.now[0] >= self.t + self.mark_after:
            return self.t + self.mark_after
        return None

    def join(self, timeout=None):
        pass

    def summary(self):
        return "menu steps: test" + (f"; {self.problem}" if self.problem else "")


def bench(tmp_path, fakes, steps_obj):
    s = spec(install_path=str(fakes.install), steps=[{"see": "stop", "within": 60, "mark": True}])
    return s, GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                            launcher=fakes.launcher, snapshot=fakes.snapshot, clock=fakes.clock, sleep=fakes.sleep,
                            folder_procs=fakes.folder_procs, closer=fakes.closer,
                            exe_lister=lambda folder: ["Game.exe"], step_player=lambda steps: steps_obj).run()


def test_measuring_starts_when_the_benchmark_starts(tmp_path):
    fakes = Fakes(tmp_path, game_frames=4000)          # 200 s of frames: menus first, then the benchmark
    s, cap = bench(tmp_path, fakes, FakeSteps(fakes, mark_after=40))
    assert cap.status == "recorded" and "menu steps: test" in cap.message
    starts = [w.result.metrics["window_start_s"][0] for w in cap.windows]
    assert starts[1] - starts[0] == 20 and starts[2] - starts[1] == 20
    assert 98 <= starts[0] - 10 <= 100                 # shifted past the time spent in the menus
    assert all(w.result.status == "completed" for w in cap.windows)
    assert s.max_record_seconds() > spec().max_record_seconds()
    assert all(w.result.note is None for w in cap.windows)        # no screenshot kept: no proof line


def test_the_runs_carry_the_proof_screenshot_and_the_game_build(tmp_path):
    from app.frames.game_benchmark import plan_for
    fakes = Fakes(tmp_path, game_frames=4000)
    steps = FakeSteps(fakes, mark_after=40)
    steps.started_shot = str(tmp_path / "run" / "benchmark_started.png")
    s, cap = bench(tmp_path, fakes, steps)
    assert {w.result.note for w in cap.windows} == {f"Proof screenshot: {steps.started_shot}"}
    s.version = "15427801"
    assert plan_for(cap, s)[0].workload.version() == "15427801"
    assert plan_for(cap, s, "1")[0].workload.version() == "1"


def test_menus_that_do_not_reach_the_benchmark_store_nothing(tmp_path):
    fakes = Fakes(tmp_path)
    s, cap = bench(tmp_path, fakes, FakeSteps(fakes, problem="step 1: 'stop' did not appear within 60 s"))
    assert cap.status == "failed" and cap.windows == []
    assert cap.message.startswith("the app did not reach the game's benchmark; game closed (Game.exe)")
    assert cap.message.endswith("menu steps: test; step 1: 'stop' did not appear within 60 s")
    assert fakes.closed == [[7]]


def test_every_read_is_logged(tmp_path):
    steps = [{"see": "menu", "within": 2, "press": "enter"}, {"see": "stop", "within": 1, "mark": True}]
    p, desk, clock = player(tmp_path, steps, [(0, ["Loading"]), (1, ["Main menu", "Settings"]), (2, ["Stop"])])
    p.run()
    log = (tmp_path / "screen_log.txt").read_text(encoding="utf-8").splitlines()
    assert log[0] == "0 s\tstep 1\tLoading" and log[1] == "1 s\tstep 1\tMain menu | Settings"
    assert log[-1].endswith("step 2\tStop") and p.mark_time is not None


def test_a_remote_desktop_capture_is_never_a_score(tmp_path):
    fakes = Fakes(tmp_path, game_frames=4000)
    s = spec(install_path=str(fakes.install))
    cap = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                        launcher=fakes.launcher, snapshot=fakes.snapshot, clock=fakes.clock, sleep=fakes.sleep,
                        folder_procs=fakes.folder_procs, closer=fakes.closer, exe_lister=lambda folder: ["Game.exe"],
                        remote_session=lambda: True).run()
    assert cap.status == "recorded" and cap.windows
    for w in cap.windows:
        assert w.result.status == "invalid" and w.result.score is None
        assert w.result.error.startswith("captured in a Remote Desktop session") and "not stored" in w.result.error


def test_a_dropped_key_is_pressed_again_until_the_menu_opens(tmp_path):
    steps = [{"see": "settings", "within": 10, "settle": 3, "press": "z"},
             {"see": "graphics", "within": 30, "again": {"press": "z", "every": 5}, "mark": True}]
    p, desk, clock = player(tmp_path, steps, [(0, ["Story", "Settings"]), (14, ["Settings", "Graphics"])])
    p.run()
    assert p.problem is None and p.mark_time >= 14
    assert [e[1] for e in desk.events] == [parse_chord("z")] * 3     # 3 s after the menu, then again at 8 and 13 s
    assert "pressed z again" in p.summary()
    assert step_problems([{"see": "a", "within": 1, "again": {"press": "z"}, "mark": True}]) == [
        "step 1: 'again' needs 'see', 'press', 'hold' or 'click', and 'every' seconds"]


def test_a_click_needs_the_word_s_place(tmp_path):
    steps = [{"see": "graphics", "within": 2, "click": "graphics", "mark": True}]
    clock = Clock()
    desk = Desk(clock)
    p = StepPlayer(steps, desk, lambda: True, tmp_path, clock=clock, sleep=clock.sleep,
                   reader=lambda d, path: (["Graphics"], None))          # text, but no word boxes
    p.run()
    assert desk.events == [] and p.problem == ("step 1: 'graphics' was read but not where it is on the screen; "
                                               "nothing clicked")
    assert step_problems([{"click": "x", "mark": True}]) == ["step 1 does nothing", "step 1: 'click' needs 'see'"]


def test_a_click_the_menu_missed_is_clicked_again(tmp_path):
    steps = [{"see": "graphics", "within": 30, "again": {"click": "settings", "every": 5, "while": "new game"},
              "mark": True}]
    timeline = [(0, ["Continue", "New Game", "Settings"]), (7, ["Sound", "Graphics"])]
    p, desk, clock = player(tmp_path, steps, timeline)
    p.run()
    assert p.problem is None
    assert [(e[0], e[1]) for e in desk.events] == [("mouse", (40, 210)), ("button", True), ("button", False)]
    assert "clicked 'settings' at 40,210 again" in p.summary()


def test_a_hold_is_tried_again_only_while_the_menu_is_still_there(tmp_path):
    steps = [{"see": "stop", "within": 60, "mark": True,
              "again": {"hold": "x", "seconds": 2.5, "press": "enter", "every": 10, "while": "benchmark test"}}]
    timeline = [(0, ["Graphics", "Run Benchmark Test"]), (15, []), (40, ["Stop Benchmarking"])]
    p, desk, clock = player(tmp_path, steps, timeline)
    p.run()
    assert p.problem is None and p.mark_time >= 40
    kinds = [e[0] for e in desk.events]
    assert kinds == ["down", "up", "press"]           # once at 10 s; the loading screen (no menu) gets nothing
    assert "held x 2.5 s again" in p.summary() and "pressed enter again" in p.summary()


def test_windows_fit_inside_the_built_in_benchmark():
    from app.frames.game_benchmark import fit_to_benchmark
    s = spec(seconds=60, warmup_runs=1, repetitions=3, settle_seconds=30)
    note = fit_to_benchmark(s, 150)
    assert s.settle_seconds == 5 and s.seconds == 36 and s.settle_seconds + s.seconds * s.count <= 150
    assert note.startswith("each window 36 s instead of 60 s")
    s = spec(seconds=60, warmup_runs=1, repetitions=3, settle_seconds=30)
    assert fit_to_benchmark(s, 270) is None and s.seconds == 60 and s.settle_seconds == 5
    s = spec(seconds=60, warmup_runs=2, repetitions=10, settle_seconds=30)
    assert "only 7 of 12 windows fit" in fit_to_benchmark(s, 150) and s.seconds == 20


def test_shadow_of_the_tomb_raider_path_and_launch(tmp_path):
    from app.ui.frame_capture_panel import game_launch_args
    from data.game_start_keys import benchmark_seconds_for, menu_launch_args
    from data.steam_benchmarks import STEAM_BENCHMARK_APPS_BY_ID
    assert menu_launch_args(750920) == ["-nolauncher"] and game_launch_args(750920) == ["-nolauncher"]
    assert benchmark_seconds_for(750920) == 150 and benchmark_seconds_for(1174180) == 270
    assert benchmark_seconds_for(1) is None and menu_launch_args("x") == []
    assert STEAM_BENCHMARK_APPS_BY_ID[750920]["exe"] == "SOTTR.exe"
    timeline = [(0, ["Square Enix"]), (30, ["Your graphics adapter is not supported"]),
                (32, ["Continue", "New Game", "Options", "Exit"]), (40, ["Display and Graphics", "Audio"]),
                (50, ["Display", "Graphics", "Run Benchmark"]), (60, ["Loading"]), (70, ["FPS 141"])]
    p, desk, clock = player(tmp_path, menu_steps_for(750920), timeline)
    p.run()
    assert p.problem is None and 70 <= p.mark_time < 72
    keys = [e[1] if e[0] == "press" else (e[0], e[1]) for e in desk.events]
    assert keys == [parse_chord("enter"), ("mouse", (40, 210)), ("button", True), ("button", False),
                    ("mouse", (240, 10)), ("button", True), ("button", False),
                    ("down", parse_chord("r")), ("up", parse_chord("r"))]     # R alone, held (2026-10-29)
    assert "closed a popup ('adapter')" in p.summary()


def test_far_cry_6_path(tmp_path):
    from data.game_start_keys import benchmark_seconds_for
    assert benchmark_seconds_for(2369390) == 60
    timeline = [(0, ["Ubisoft"]), (20, ["The Government of Yara"]), (22, ["Loading"]),
                (30, ["An original story"]), (32, ["Loading"]), (34, ["Remind me later"]), (39, ["Loading"]),
                (40, ["Continue", "Options", "Exit"]), (50, ["Video", "Quality"]), (60, ["Video Adapter"]),
                (66, ["Texture Filtering"]), (72, ["Enable Framerate Lock"]), (80, ["Toggle UI"])]
    p, desk, clock = player(tmp_path, menu_steps_for(2369390), timeline)
    p.run()
    assert p.problem is None and 80 <= p.mark_time < 82
    keys = [e[1] if e[0] == "press" else (e[0], e[1]) for e in desk.events]
    space, esc, f5 = parse_chord("space"), parse_chord("esc"), parse_chord("f5")
    # Space (the intro), then Space, Esc for the popups and F5: held, not pressed in an instant (2026-10-23)
    assert keys == [("down", space), ("up", space)] * 4 + [("down", esc), ("up", esc),
                    ("mouse", (40, 110)), ("button", True), ("button", False),
                    ("mouse", (40, 10)), ("button", True), ("button", False),
                    parse_chord("e"), parse_chord("e"), parse_chord("e"), ("down", f5), ("up", f5)]
    held = [e for e in desk.events if e[0] in ("down", "up")]
    assert [round(up[2] - down[2], 2) for down, up in zip(held[::2], held[1::2])] == [0.15] * 6
    assert "benchmark started ('toggle' seen at 8" in p.summary()


def test_borderlands_3_path(tmp_path):
    from data.game_start_keys import benchmark_seconds_for
    assert benchmark_seconds_for(397540) == 110
    timeline = [(0, ["Gearbox"]), (20, ["Press any key"]), (24, ["Loading"]),
                (30, ["Play", "Options", "Social", "Quit"]), (40, ["Visuals", "Sound", "Gameplay"]),
                (50, ["Basic", "Display Mode"]), (60, ["Start Benchmark"]), (70, ["Loading"]), (80, ["FPS 140"])]
    p, desk, clock = player(tmp_path, menu_steps_for(397540), timeline)
    p.run()
    assert p.problem is None and 80 <= p.mark_time < 82
    keys = [e[1] if e[0] == "press" else (e[0], e[1]) for e in desk.events]
    assert keys == [parse_chord("enter"),
                    ("mouse", (40, 110)), ("button", True), ("button", False),
                    ("mouse", (40, 10)), ("button", True), ("button", False),
                    parse_chord("e"), parse_chord("e"), parse_chord("e"),
                    ("mouse", (40, 10)), ("button", True), ("button", False)]
    assert "benchmark started ('fps' seen at 8" in p.summary()


def test_the_games_results_screen_is_kept_and_the_screen_mode_read(tmp_path):
    """Owner, 2026-10-23: the result the game showed (not its first frame) and the resolution it ran at."""
    fakes = Fakes(tmp_path, game_frames=4000)
    steps = FakeSteps(fakes, mark_after=40)
    steps.started_shot = str(tmp_path / "run" / "benchmark_started.png")
    reads = []

    def reader(desktop, path):
        reads.append(path)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return (["Loading"] if len(reads) < 3 else ["BENCHMARK RESULTS", "Average FPS 71"]), None

    s = spec(install_path=str(fakes.install), steps=[{"see": "stop", "within": 60, "mark": True}])
    cap = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                        launcher=fakes.launcher, snapshot=fakes.snapshot, clock=fakes.clock, sleep=fakes.sleep,
                        folder_procs=fakes.folder_procs, closer=fakes.closer, desktop=object(),
                        exe_lister=lambda folder: ["Game.exe"], step_player=lambda st: steps,
                        screen_mode=lambda: "2560x1440 at 144 Hz", screen_reader=reader).run()
    shot = tmp_path / "run" / "benchmark_result.png"
    assert len(reads) == 4 and cap.display_mode == "2560x1440 at 144 Hz"     # once more after its words show
    assert {w.result.note for w in cap.windows} == {f"Proof screenshot: {shot}"}
    assert "results screen kept ('result' seen)" in cap.message
    assert all(len(w.frames) > 100 for w in cap.windows if w.result.status == "completed")


def test_fps_over_time_and_the_charts_a_game_run_stores(tmp_path):
    from app.automation.workload import RunContext, WorkloadResult
    from app.frames.game_benchmark import RecordedWindow, WindowOutcome, fps_over_time
    assert fps_over_time([10.0] * 250) == [(1.0, 100.0), (2.0, 100.0)]          # the last half second left out
    result = WorkloadResult("completed", score=100.0, unit="fps")
    samples = [(0.0, {"gpu0_load": (90.0, "%")}), (1.0, {"gpu0_load": (95.0, "%")})]
    w = RecordedWindow("game", "Game", [WindowOutcome(False, 1, result, samples, frames=[10.0] * 200)])
    out = w.run(RunContext(output_dir=str(tmp_path)))
    assert out.series["fps"] == ("FPS", [(1.0, 100.0), (2.0, 100.0)])
    assert out.series["tel_gpu0_load"][1] == [(0.0, 90.0), (1.0, 95.0)]


class PassFakes:
    """A game started once per run (pid 100, 101, ...): 30 s of menus at 100 FPS, then its benchmark at
    1000 / (20 + run) FPS until it is closed."""

    def __init__(self, tmp_path):
        self.install = tmp_path / "steamapps" / "common" / "Game"
        self.now, self.run, self.up, self.closed, self.launches = [0.0], -1, False, [], 0
        self.halted = None

    def clock(self):
        return self.now[0]

    def sleep(self, s):
        import time
        time.sleep(0.001)           # a real moment, so the PresentMon thread starts before the fake UAC wait ends
        self.now[0] += s

    def folder_procs(self):
        from app.frames.game_close import ProcInfo
        return {100 + self.run: ProcInfo(100 + self.run, 1.0, str(self.install / "Game.exe"))} if self.up else {}

    def launcher(self, *a, **kw):
        self.run, self.up, self.launches = self.run + 1, True, self.launches + 1
        return {"status": "launched", "message": "ok"}

    def closer(self, started):
        self.closed.append(sorted(started))
        self.up = False
        return "game closed"

    def elevated(self, exe, args, cwd, timeout, halt, on_started=None):
        from app.frames.elevated import ElevatedOutcome
        self.args = list(args)
        on_started()
        halt.wait(10)
        rows = []
        for r in range(self.run + 1):
            rows += [f"Game.exe,{100 + r},10.0"] * 3000 + [f"Game.exe,{100 + r},{20.0 + r}"] * (60000 // (20 + r))
        with open(args[args.index("--output_file") + 1], "w", encoding="utf-8") as f:
            f.write("Application,ProcessID,MsBetweenPresents\n" + "\n".join(rows) + "\n")
        return ElevatedOutcome("stopped", exit_code=1, terminated=True)


def test_a_built_in_benchmark_is_measured_whole_once_per_launch(tmp_path):
    """Owner, 2026-10-23: each run is the game's whole benchmark (the span its own result covers), the game is
    started anew for each, the first run is the warm-up, and the game's own numbers go next to the app's."""
    fakes = PassFakes(tmp_path)
    desk = SimpleNamespace(foreground_pid=lambda: 100 + fakes.run)
    own = []

    def own_result(app_id, since):
        own.append(app_id)
        return {"avg": 48.0, "min": 45.0, "max": 55.0}

    s = spec(install_path=str(fakes.install), steps=[{"see": "stop", "within": 60, "mark": True}],
             benchmark_seconds=60)
    assert s.whole_benchmark and s.max_record_seconds() > 3 * 60
    cap = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                        launcher=fakes.launcher, snapshot=lambda: [], clock=fakes.clock, sleep=fakes.sleep,
                        folder_procs=fakes.folder_procs, closer=fakes.closer, desktop=desk,
                        exe_lister=lambda folder: ["Game.exe"],
                        step_player=lambda st: FakeSteps(fakes, mark_after=30), own_result=own_result).run()
    assert cap.status == "recorded", cap.message
    assert "--terminate_on_proc_exit" not in fakes.args                # PresentMon outlives each run's game
    assert fakes.launches == 3 and fakes.closed == [[100], [101], [102]] and own == ["123"] * 3
    assert [(w.is_warmup, w.run_index) for w in cap.windows] == [(True, 0), (False, 1), (False, 2)]
    for r, w in enumerate(cap.windows):
        assert w.result.status == "completed", w.result.error
        assert abs(w.result.score - 1000 / (20 + r)) < 0.2                # the benchmark only, not its menus
        assert abs(w.result.metrics["duration_seconds"][0] - 60) < 0.1
        assert w.result.metrics["game_avg_fps"] == (48.0, "FPS")
        assert "Measured: the game's whole benchmark (60 s" in w.result.note
        assert "Game's own result: Average 48, Minimum 45, Maximum 55 FPS" in w.result.note


def test_a_run_that_does_not_reach_the_benchmark_ends_the_runs(tmp_path):
    fakes = PassFakes(tmp_path)
    desk = SimpleNamespace(foreground_pid=lambda: 100 + fakes.run)
    made = []

    def player(st):
        made.append(FakeSteps(fakes, mark_after=30) if not made else FakeSteps(fakes, problem="no 'stop' seen"))
        return made[-1]

    s = spec(install_path=str(fakes.install), steps=[{"see": "stop", "within": 60, "mark": True}],
             benchmark_seconds=60)
    cap = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, elevated_runner=fakes.elevated,
                        launcher=fakes.launcher, snapshot=lambda: [], clock=fakes.clock, sleep=fakes.sleep,
                        folder_procs=fakes.folder_procs, closer=fakes.closer, desktop=desk,
                        exe_lister=lambda folder: ["Game.exe"], step_player=player).run()
    assert fakes.launches == 2 and [w.result.status for w in cap.windows] == ["completed", "invalid", "invalid"]
    assert "did not reach the game's benchmark" in cap.windows[1].result.error
    assert "earlier run did not complete" in cap.windows[2].result.error


def test_sottr_answers_its_quit_question_and_the_closer_gets_it():
    from data.game_start_keys import quit_steps_for
    from app.frames.game_close import close_game
    from app.frames.menu_steps import step_problems
    steps = quit_steps_for(750920)
    assert steps and not step_problems(steps, marked=False) and steps[0]["click"] == "yes" and quit_steps_for(397540) is None
    assert not step_problems(quit_steps_for(1174180), marked=False)
    asked, alive = [], [True]

    def polite(pids):
        asked.append(pids)
        alive[0] = False                    # the quit question answered: the game ends
        return 1
    from app.frames.game_close import ProcInfo
    out = close_game({7: ProcInfo(7, 1.0, r"C:\g\SOTTR.exe")}, alive=lambda i: alive[0], polite=polite,
                     sleep=lambda s: None)
    assert asked == [[7]] and out == "game closed (SOTTR.exe)"


def test_the_quit_steps_play_after_the_window_is_asked_to_close(tmp_path, monkeypatch):
    from app.frames import game_close, menu_steps
    played = []

    class Player:
        def __init__(self, steps, desktop, in_front, folder):
            played.append(steps)
        def run(self):
            played.append("run")
        def summary(self):
            return "clicked 'yes'"
    monkeypatch.setattr(menu_steps, "StepPlayer", Player)
    monkeypatch.setattr(game_close, "post_close", lambda pids: played.append(("close", pids)) or 1)
    s = spec(install_path=str(tmp_path), steps=[{"see": "stop", "within": 60, "mark": True}])
    s.quit_steps = [{"see": "yes", "within": 15, "click": "yes"}]
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=False, desktop=object())
    assert bench._close_and_answer([7]) == 1
    assert played == [("close", [7]), s.quit_steps, "run"] and bench.key_notes == ["quit: clicked 'yes'"]

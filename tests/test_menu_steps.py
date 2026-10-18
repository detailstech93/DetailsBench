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

    def click(self):
        self.events.append(("click", None))


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
                (70, ["Graphics", "Display", "Audio"]), (80, ["Resolution", "Run Benchmark Test"]),
                (120, ["Stop Benchmarking"])]
    p, desk, clock = player(tmp_path, menu_steps_for(1174180), timeline)
    p.run()
    assert p.problem is None and 120 <= p.mark_time < 122
    keys = [e[1] if e[0] == "press" else (e[0], e[1]) for e in desk.events]
    assert keys == [parse_chord("enter"), parse_chord("z"), ("mouse", (40, 10)), ("click", None),
                    ("down", parse_chord("x")), ("up", parse_chord("x")), parse_chord("enter")]
    assert "clicked 'graphics' at 40,10" in p.summary()
    held = [e for e in desk.events if e[0] in ("down", "up")]
    assert held[1][2] - held[0][2] == 1.5
    assert "closed a popup ('strange')" in p.summary() and "benchmark started ('stop' seen at 12" in p.summary()
    assert (tmp_path / "benchmark_started.png").read_bytes() == b"png:Stop Benchmarking"


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
    assert desk.events == [] and p.problem == "enter not sent: the game was not in front"


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
    assert p.problem == "keys and screen reading work on Windows only for now"


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
    monkeypatch.setattr(screen_text.os, "name", "nt")
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
    monkeypatch.setattr(screen_text.os, "name", "posix")
    assert screen_text.read_image(tmp_path / "a.png", run=ok) == ([], "reading the screen works on Windows only for now")
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
    return s, GameBenchmark(s, tmp_path / "run", None, lambda m: None, elevated_runner=fakes.elevated,
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
    cap = GameBenchmark(s, tmp_path / "run", None, lambda m: None, elevated_runner=fakes.elevated,
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
        "step 1: 'again' needs 'see', 'press' and 'every' seconds"]


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

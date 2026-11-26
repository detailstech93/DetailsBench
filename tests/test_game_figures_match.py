"""Owner, 2026-11-12: before the five games go to the library, the app's numbers and the games' own must agree or
say why (SOTTR +2 %, RDR2 -5.5 %, Cyberpunk's "Custom", a 98 C start with no wait shown)."""

import pytest

from app.environment import conditions as C
from app.frames.game_benchmark import screen_numbers
from app.frames.segments import next_loading
from data.game_start_keys import ends_at_loading, screen_average


def test_sottr_span_ends_at_the_load_before_its_results():
    frames = [5.0] * 2000 + [400.0] + [4.0] * 1000        # 10 s, a load at 10 s, then the menu
    assert next_loading(frames, 2.0) == 10.0 and next_loading(frames, 11.0) is None
    assert ends_at_loading(750920) and not ends_at_loading(1174180)


def test_rdr2_average_from_its_results_screen():
    assert screen_average(1174180)
    assert screen_numbers(["End of benchmark", "Average FPS: 89.2 1 82", "EXIT BENCHMARKING"]) == {"avg": 89.2182}
    assert screen_numbers(["(no words)"]) is None
    assert screen_numbers(["End of benchmark", "Average FPS: 26.1 S 1 6"]) == {"avg": 26.1516}   # session 24
    assert screen_numbers(["Maximum FPS 33.8193", "Average FPS: 26.SOSS"]) == {"avg": 26.5055}


def test_rdr2_average_is_read_again_from_the_enlarged_part_after_its_word():
    """Owner's laptop 2026-11-16: Windows OCR gave "Average FPS:" without 94.5044, and "93.4 1" for 93.4152."""
    from app.frames.game_benchmark import results_average
    from app.frames.screen_text import Line
    line = Line("Average FPS: 93.4 1")
    line.words = (("Average", 824, 544, 80, 20), ("FPS:", 914, 544, 38, 16), ("93.4", 958, 534, 60, 32))
    asked = []
    read = lambda shot, *box: asked.append((shot, box)) or ["FPS:93.41S2"]
    assert results_average(["End of benchmark", line], "shot.png", read) == {"avg": 93.4152}
    assert asked == [("shot.png", (904, 524, 240, 60))]
    assert results_average([line], "shot.png", lambda shot, *box: []) == {"avg": 93.41}     # the whole screen's
    assert results_average(["Average FPS: 93.6353"], "shot.png", read) == {"avg": 93.6353} and len(asked) == 1
    assert results_average([line], None, read) == {"avg": 93.41}


def test_sottr_average_from_its_results_screen_is_a_cut_whole_number():
    """Owner, 2026-11-14: its screen shows "Average FPS: 32" where its file says 32.7."""
    from app.frames.game_benchmark import _match_text
    assert screen_average(750920)
    assert screen_numbers(["Frames rendered: 5024", "Average FPS: 32", "Game Version"]) == {"avg": 32.0}
    assert "they match" in _match_text(32.7, 32.0) and "DO NOT MATCH" in _match_text(33.1, 32.0)
    assert "DO NOT MATCH" in _match_text(29.04, 28.0)      # Far Cry 6, session 21: its file's whole 28


def test_report_says_why_a_far_game_average_differs_and_names_the_written_preset():
    from app.report import _own_result_html
    assert "more than 3 %" in _own_result_html(84.0, 60.0, 89.0, None, None)
    assert "more than 3 %" not in _own_result_html(219.0, 150.0, 215.0, None, None)


def test_one_hot_reading_is_shown_as_no_wait_needed():
    spikes = iter([98.0, 60.0, 58.0])

    class Stop:
        def wait(self, s):
            return False

        def is_set(self):
            return False
    into = {}
    assert C.cool_down(Stop(), read=lambda: {"cpu_temperature_hottest": (next(spikes), "C")}, clock=lambda: 0.0,
                       into=into) == ""
    assert into["items"][-1]["name"] == "Cooling wait" and "not needed" in into["items"][-1]["value"]


def test_sottr_span_up_to_its_load_takes_a_mark_read_late_and_never_runs_past_the_load():
    """Session 23 (NucBox K10, 2026-11-14, 1080p): 'fps' was read 6-7 s into the benchmark, the span was taken from
    the mark and ran 10 s into the 66 FPS results screen (35.4 FPS stored, the game's own 32.0)."""
    from types import SimpleNamespace
    from app.frames.game_benchmark import GameBenchmark
    frames = [30.0] * 8000 + [500.0] + [15.0] * 2000          # 240 s, the load before the results, the results
    me = SimpleNamespace(spec=SimpleNamespace(app_id=750920))
    (a, b), = GameBenchmark._marked_windows(me, frames, [(0.0, 157.0)], 90.0)
    assert (a, b) == (pytest.approx(83.0), pytest.approx(240.0))
    (a, b), = GameBenchmark._marked_windows(me, frames, [(0.0, 157.0)], 110.0)   # far later: a benchmark cut short
    assert (a, b) == (pytest.approx(110.0), pytest.approx(240.0))
    # session 25: 'fps' read at 59 s, before the load into the benchmark (63 s); the results' load at 240 s
    frames = [30.0] * 2100 + [500.0] + [30.0] * 5883 + [500.0] + [15.0] * 2000
    # and the span runs from that load (2026-11-20: the game draws 177.7 s between them on Windows and Linux)
    (a, b), = GameBenchmark._marked_windows(me, frames, [(0.0, 157.0)], 59.0)
    assert (a, b) == (pytest.approx(63.5, abs=0.1), pytest.approx(240.0, abs=0.1))
    # Fedora 2026-11-20 run 3: a 222 ms hitch 64 s into the benchmark is not a load; the span still ends at it
    frames = [30.0] * 2100 + [500.0] + [30.0] * 2200 + [222.0] + [30.0] * 3683 + [500.0] + [15.0] * 2000
    (a, b), = GameBenchmark._marked_windows(me, frames, [(0.0, 157.0)], 70.0)
    assert (a, b) == (pytest.approx(63.5, abs=0.3), pytest.approx(240.0, abs=0.3))
    # a load more than 40 s before the last 157 s is not the one into it: the last 157 s are kept
    frames = [30.0] * 1000 + [500.0] + [30.0] * 6983 + [500.0] + [15.0] * 2000
    (a, b), = GameBenchmark._marked_windows(me, frames, [(0.0, 157.0)], 59.0)
    assert (a, b) == (pytest.approx(83.0, abs=0.1), pytest.approx(240.0, abs=0.1))


def test_sottr_span_never_holds_the_load_into_it():
    """GEEKOM Windows 2026-11-20 runs 2-3: the load's end rounded over it in ms and its 1.1 s frame was measured
    (frame times with 4 decimals as PresentMon's; seeds 0, 98 and 185 took it in before the fix)."""
    import random
    from app.frames.segments import load_end_before, split_frames
    for seed in range(300):
        rng = random.Random(seed)
        frames = ([round(rng.uniform(25.0, 40.0), 4) for _ in range(rng.randint(500, 4000))]
                  + [round(1100.0 + rng.random(), 4)] + [30.0] * 6000)
        a = load_end_before(frames, sum(frames) / 1000.0 - 170.0, 40.0, 400.0)
        assert max(split_frames(frames, [(a, a + 150.0)])[0]) < 500.0, seed


def test_far_cry6_own_average_is_whole_seconds_rounded_down_so_2_fps_under_still_matches():
    """Session 21 (NucBox K10, 2026-11-14): the game said 28 where PresentMon measured 29.04 on the same 60 s."""
    from app.frames.game_benchmark import _match_text
    from app.games import far_cry6
    own = far_cry6.own_numbers({"Average FPS": "28", "Minimum FPS": "25", "Maximum FPS": "32"})
    assert own == {"avg": 28.0, "min": 25.0, "max": 32.0, "cut": 2.0}
    assert "they match" in _match_text(29.37, own["avg"], own["cut"])
    assert "DO NOT MATCH" in _match_text(30.2, own["avg"], own["cut"]) and "DO NOT MATCH" in _match_text(29.04, 28.0)


def test_bl3_after_a_long_loading_screen_is_measured_back_from_the_load_at_its_end():
    """Fedora 2026-11-20 (Proton): ~16 s of animated loading screen after BL3's load, no long frame in it; the
    window began 21 s early and read 31.17 FPS for the game's 29.86. Windows' stretch fits: unchanged."""
    from types import SimpleNamespace
    from app.frames.game_benchmark import GameBenchmark
    me = SimpleNamespace(spec=SimpleNamespace(app_id=397540))
    load = [300.0, 300.0]
    screen = [31.0] * 516                                       # 16 s at ~32 FPS
    bench = [33.5] * 3362                                       # 112.6 s: 109 s and the fade
    frames = [16.0] * 125 + load + screen + bench + [300.0] + [16.0] * 100
    start = (125 * 16.0 + 600.0) / 1000.0
    end = start + (516 * 31.0 + 3362 * 33.5) / 1000.0 - 3.0
    for mark in (1.0, start + 1.0):                              # at its Start click, or at its keys after the load
        (a, b), = GameBenchmark._marked_windows(me, frames, [(5.0, 114.0)], mark)
        assert (a, b) == (pytest.approx(end - 109.0), pytest.approx(end))
    windows = [16.0] * 125 + load + bench + [300.0] + [16.0] * 100     # no loading screen: from the load, as before
    (a, b), = GameBenchmark._marked_windows(me, windows, [(5.0, 114.0)], 1.0)
    assert (a, b) == (pytest.approx(start + 5.0), pytest.approx(start + 3362 * 33.5 / 1000.0 - 3.0))


def test_sottr_text_says_the_whole_span_is_measured():
    from types import SimpleNamespace
    from app.ui.frame_capture_panel import measured_span_text
    assert "load into it to the load before its results" in measured_span_text(SimpleNamespace(app_id="750920",
                                                                                             benchmark_seconds=157))
    assert "(about 130 s) is measured from its start" in measured_span_text(SimpleNamespace(app_id="1174180",
                                                                                         benchmark_seconds=130))

"""Owner, 2026-11-12: before the five games go to the library, the app's numbers and the games' own must agree or
say why (SOTTR +2 %, RDR2 -5.5 %, Cyberpunk's "Custom", a 98 C start with no wait shown)."""

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

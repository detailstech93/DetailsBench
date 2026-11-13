"""Owner, 2026-11-12: games in the library. A group is the game + build + resolution + preset level; ray tracing,
the upscaler and frame generation are shown next to each result, with filters."""

from app.library import compare, games
from tests.test_library_store import export, run

CP = ("Measured: the game's whole benchmark; Settings used: Resolution: 1920x1080, Preset: Custom, Upscaler: Off, "
      "Frame generation: Off, Ray tracing: Off, Game version: 2.5; Forced: upscaler {up}, ray_tracing {rt}, preset Ultra, "
      "frame_gen Off")
BL3 = ("Measured: the game's whole benchmark (110 s); Settings used: Resolution: 1920x1080, Quality preset: Ultra, "
       "Upscaler: not in the game's file, Ray tracing: not in the game's file; Forced: preset Ultra")


def test_group_and_options_from_the_run_note():
    g = games.game_settings("Cyberpunk 2077 built-in benchmark", CP.format(up="Off", rt="Off"), True)
    assert g == {"game": "cyberpunk 2077", "resolution": "1920x1080", "preset": "Ultra", "level": "Max",
                 "build": "2.5", "upscaler": "Off", "frame_gen": "Off", "ray_tracing": "Off"}
    bl3 = games.game_settings("Borderlands 3", BL3, True)
    assert bl3["level"] == "Max-1" and bl3["ray_tracing"] == "Off" and bl3["upscaler"] == "Off"
    assert games.game_settings("Borderlands 3", BL3, False) is None          # not its built-in benchmark
    assert games.game_settings("Some other game", BL3, True) is None


def _pkgs(up, rt, when="2026-11-12T16:54:14"):
    runs = [run("cyberpunk2077_builtin_benchmark", "game", s, note=CP.format(up=up, rt=rt), warm=w)
            for s, w in ((80.0, True), (90.0, False), (92.0, False))]
    from tests.test_library_store import pk
    e = export(runs=runs)
    e["session"]["created_at"] = when
    return pk.build_packages(e, now="2026-11-12T18:00:00")[0]


def test_games_measured_before_the_checked_line_stay_out():
    assert _pkgs("Off", "Off", when="2026-11-12T14:05:20") == []


def test_upscaling_and_rt_are_columns_not_other_tests():
    native, = _pkgs("Off", "Off")
    dlss, = _pkgs("DLSS Quality", "On")
    assert native["test"]["name"] == "Cyberpunk 2077 built-in benchmark - 1920x1080, Max (Ultra), build 2.5" or \
        native["test"]["name"].endswith("1920x1080, Max (Ultra), build 2.5")
    assert native["test"]["fingerprint"] == dlss["test"]["fingerprint"]       # one group
    assert compare._test_key(native) == compare._test_key(dlss)
    assert games.options_text(dlss).startswith("RT on, upscaler DLSS Quality, frame gen Off")
    both = [native, dlss]
    assert compare.filtered(both, upscaling="off") == [native] and compare.filtered(both, rt="on") == [dlss]


def test_a_game_result_is_shown_under_its_cpu_too():
    """Owner, 2026-11-13: a game compares processors as well as graphics cards."""
    from app.library import view
    gpu, = _pkgs("Off", "Off")
    shown = games.with_cpu([gpu])
    cpu = shown[1]["attribution"]
    assert len(shown) == 2 and cpu["part"] == "cpu" and cpu["part_key"] == gpu["attribution"]["linked"][0]["part_key"]
    assert shown[1]["package_id"] == gpu["package_id"] and gpu["attribution"]["part"] == "gpu"   # original untouched
    assert f"with {gpu['attribution']['linked'][0]['part_name']}" in games.options_text(gpu)
    assert f"with {gpu['attribution']['part_name']}" in games.options_text(shown[1])
    assert [p["key"] for p in view.parts(shown, {})["cpu"]] == [cpu["part_key"]]
    assert len(view.rows(shown, cpu["part_key"])) == 1
    labels = [label for _, label in compare.tests(shown)]
    assert any("- processors" in x for x in labels) and any("- graphics cards" in x for x in labels)
    assert compare.ranking(shown, compare._test_key(shown[1]))["parts"][0]["key"] == cpu["part_key"]

"""Tests for app.report.render_session_report."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.report import render_session_report


def test_doctype_title_charset_and_viewport():
    export = {
        "session": {
            "id": 7,
            "name": "My session",
            "created_at": "2024-01-01T00:00:00Z",
            "is_test": False,
            "is_manual": False,
        }
    }

    html_text = render_session_report(export)

    assert html_text.startswith("<!doctype html>")
    assert '<html lang="en">' in html_text
    assert '<meta charset="utf-8">' in html_text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html_text
    assert "<title>Benchmark report: My session</title>" in html_text


def test_session_name_is_escaped_in_title_and_header():
    export = {
        "session": {
            "id": 1,
            "name": '<b>x</b> & "q"',
            "created_at": "2024-01-01T00:00:00Z",
            "is_test": False,
            "is_manual": False,
        }
    }

    html_text = render_session_report(export)

    assert "<title>Benchmark report: &lt;b&gt;x&lt;/b&gt; &amp; &quot;q&quot;</title>" in html_text
    assert "<h1 dir=\"auto\">&lt;b&gt;x&lt;/b&gt; &amp; &quot;q&quot;</h1>" in html_text


def test_run_note_is_escaped():
    export = {
        "runs": [
            {
                "benchmark_name": "bench",
                "run_index": 1,
                "status": "completed",
                "score": 10,
                "unit": "pts",
                "timestamp": "2024-01-01T00:00:00Z",
                "note": '<script>alert("x")</script> & "q"',
                "is_warmup": False,
                "is_test": False,
                "is_manual": False,
            }
        ]
    }

    html_text = render_session_report(export)

    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;q&quot;" in html_text
    assert "<script>alert" not in html_text


def test_forbidden_substrings_are_absent_even_with_hostile_inputs():
    export = {
        "session": {
            "id": 1,
            "name": "<script>https://x src=",
            "created_at": "https://x",
            "is_test": False,
            "is_manual": False,
        },
        "runs": [
            {
                "benchmark_name": "<script>",
                "run_index": 1,
                "status": "completed",
                "score": 1,
                "unit": "https://x",
                "timestamp": "https://x",
                "note": "src=",
                "is_warmup": False,
                "is_test": False,
                "is_manual": False,
            }
        ],
    }

    html_text = render_session_report(export)

    assert "<script" not in html_text
    assert "http://" not in html_text
    assert "https://" not in html_text
    assert "src=" not in html_text
    assert "@import" not in html_text


def test_test_badge_only_when_session_is_test():
    test_export = {
        "session": {
            "id": 1,
            "name": "Test",
            "created_at": "2024-01-01T00:00:00Z",
            "is_test": True,
            "is_manual": False,
        }
    }

    normal_export = {
        "session": {
            "id": 2,
            "name": "Normal",
            "created_at": "2024-01-01T00:00:00Z",
            "is_test": False,
            "is_manual": False,
        }
    }

    test_html = render_session_report(test_export)
    normal_html = render_session_report(normal_export)

    assert '<p class="badge test">TEST DATA - not real results</p>' in test_html
    assert '<p class="badge test">TEST DATA - not real results</p>' not in normal_html


def test_number_formatting_uses_thousands_separators_and_two_decimals():
    export = {
        "summaries": {
            "bench": {
                "included": 2500,
                "excluded": {},
                "mixed_sources": False,
                "stats": {
                    "count": 2500,
                    "mean": 2500,
                    "median": 2500,
                    "min": 2500,
                    "max": 2500,
                    "stdev": 2500,
                    "cv_percent": 1.23,
                },
            }
        }
    }

    html_text = render_session_report(export)

    assert "2,500.00" in html_text
    assert "1.23 %" in html_text


def test_failed_run_score_is_dash_not_zero():
    export = {
        "runs": [
            {
                "benchmark_name": "bench",
                "run_index": 1,
                "status": "failed",
                "score": 0,
                "unit": "pts",
                "timestamp": "2024-01-01T00:00:00Z",
                "note": "-",
                "is_warmup": False,
                "is_test": False,
                "is_manual": False,
            }
        ]
    }

    html_text = render_session_report(export)

    assert "<td>failed</td>" in html_text
    assert "<td>-</td><td>pts</td>" in html_text
    assert "<td>0.00</td><td>pts</td>" not in html_text


def test_excluded_reasons_list_only_non_zero_values():
    export = {
        "summaries": {
            "bench": {
                "included": 3,
                "excluded": {
                    "test": 0,
                    "warmup": 1,
                    "not_completed": 2,
                    "missing_score": 0,
                },
                "mixed_sources": False,
                "stats": {},
            }
        }
    }

    html_text = render_session_report(export)

    assert "warmup 1, not_completed 2" in html_text
    assert "test 0" not in html_text
    assert "missing_score 0" not in html_text


def test_mixed_sources_note_is_added_to_runs_used():
    export = {
        "summaries": {
            "bench": {
                "included": 4,
                "excluded": {},
                "mixed_sources": True,
                "stats": {},
            }
        }
    }

    html_text = render_session_report(export)

    assert "<td>4 (mixed manual/real)</td>" in html_text


def test_empty_export_renders_safe_placeholders():
    html_text = render_session_report({})

    assert "No results recorded." in html_text
    assert "No runs recorded." in html_text
    assert "Environment not recorded for this session." in html_text
    assert "Untitled session" in html_text


def test_environment_unavailable_field_and_boolean_values():
    export = {
        "manifest": {
            "environment": {
                "os_name": {"value": "Linux", "source": "detected", "status": "detected"},
                "gpu": {"value": None, "source": "unavailable", "status": "unavailable"},
                "is_virtual_machine": {"value": True, "source": "detected", "status": "detected"},
            }
        }
    }

    html_text = render_session_report(export)

    assert "<td>Linux</td>" in html_text
    assert "<td>unavailable</td><td>unavailable</td>" in html_text
    assert "<td>yes</td>" in html_text


def test_summary_rows_are_sorted_by_benchmark_name():
    export = {
        "summaries": {
            "zeta": {"included": 1, "excluded": {}, "mixed_sources": False, "stats": {}},
            "alpha": {"included": 1, "excluded": {}, "mixed_sources": False, "stats": {}},
            "mid": {"included": 1, "excluded": {}, "mixed_sources": False, "stats": {}},
        }
    }

    html_text = render_session_report(export)

    alpha_index = html_text.index("<td dir=\"auto\">alpha</td>")
    mid_index = html_text.index("<td dir=\"auto\">mid</td>")
    zeta_index = html_text.index("<td dir=\"auto\">zeta</td>")

    assert alpha_index < mid_index < zeta_index


def test_runs_used_is_an_integer_and_rtl_text_gets_auto_direction():
    export = {
        "session": {"name": "جلسة"},
        "summaries": {"bench": {"included": 3, "excluded": {}, "stats": {"mean": 1.0}}},
        "runs": [{"benchmark_name": "bench", "status": "completed", "score": 1.0, "note": "ملاحظة"}],
    }
    html_text = render_session_report(export)
    assert "<td>3</td>" in html_text and "3.00" not in html_text
    assert '<h1 dir="auto">جلسة</h1>' in html_text
    assert '<td class="note" dir="auto">ملاحظة</td>' in html_text


def _run(name, index, score, **extra):
    return {"benchmark_name": name, "run_index": index, "status": "completed", "score": score, "unit": "MIPS",
            "timestamp": "2024-01-01T00:00:00Z", "note": None, "is_warmup": False, "is_test": False,
            "is_manual": False, **extra}


def test_charts_one_bar_per_measured_run_and_still_safe():
    export = {"session": {"id": 1, "name": "s", "is_test": False, "is_manual": False},
              "runs": [_run("7zip", 0, 999, is_warmup=True), _run("7zip", 1, 173712), _run("7zip", 2, 171550),
                       _run("7zip", 3, None, status="failed"), _run("<script>https://x", 1, 5, unit="https://x"),
                       _run("<script>https://x", 2, 6, unit="https://x"), _run("single", 1, 3)]}
    html_text = render_session_report(export)
    assert "<h2>Charts</h2>" in html_text
    assert html_text.count('class="bar"') == 4                 # 2 measured 7-Zip runs + 2 hostile ones
    assert "7zip, each measured run" in html_text and "173712 MIPS" in html_text
    assert "single, each measured run" not in html_text       # one run: the table says it all
    for bad in ("<script", "http://", "https://", "src="):
        assert bad not in html_text


def test_no_charts_section_without_two_measured_runs():
    assert "<h2>Charts</h2>" not in render_session_report({"runs": [_run("a", 1, 1)]})


def test_chart_title_uses_the_display_name_from_the_manifest():
    export = {"manifest": {"benchmarks": [{"name": "7zip", "display_name": "7-Zip benchmark"}]},
              "runs": [_run("7zip", 1, 1), _run("7zip", 2, 2)]}
    assert "7-Zip benchmark, each measured run" in render_session_report(export)


def _game_export(note, metrics, is_test=False):
    run = {"benchmark_name": "cp", "benchmark_type": "game", "status": "completed", "score": 76.43, "unit": "FPS",
           "run_index": 1, "is_warmup": False, "is_test": is_test, "note": note}
    m = {k: {"unit": u, "count": 1, "mean": v, "min": v, "max": v, "mixed_units": False} for k, (v, u) in metrics.items()}
    return {"session": {"id": 1, "name": "s"}, "runs": [run],
            "summaries": {"cp": {"included": 1, "stats": {"mean": 76.43}}}, "metric_summaries": {"cp": m},
            "manifest": {"benchmarks": [{"name": "cp", "display_name": "Cyberpunk 2077 built-in benchmark (assisted)"}]}}


def test_game_section_whole_numbers_settings_and_readings():
    export = _game_export(
        "Settings used: Resolution: 2560x1440, Frame generation: Off; Forced: upscaler DLSS; "
        "Readings: the benchmark scene only (66 s)",
        {"low_1pct_fps": (57.8, "FPS"), "low_01pct_fps": (39.7, "FPS"), "min_fps": (65.5, "FPS"),
         "max_fps": (104.4, "FPS"), "tel_gpu0_temperature_avg": (75.4, "C"), "tel_gpu0_temperature_max": (77.0, "C"),
         "tel_gpu0_mem_clock_avg": (14001.0, "MHz"), "tel_gpu0_mem_clock_max": (14001.0, "MHz")})
    page = render_session_report(export)
    game = page[page.index("Game results"):page.index("<h2>Summary")]
    assert "Cyberpunk 2077 built-in benchmark</h3>" in game
    for number in ("76 <span>FPS", "58 <span>FPS", "40 <span>FPS", "66 <span>FPS", "104 <span>FPS"):
        assert number in game
    assert "76.43" not in game and "57.8" not in game
    assert "<tr><th>Frame generation</th><td>Off</td></tr>" in game
    assert "Set by the app before the run" in game and "benchmark scene only (66 s)" in game
    assert "<td>GPU temperature</td><td>75 \u00b0C</td><td>77 \u00b0C</td>" in game
    assert "<td>GPU memory clock</td><td>14,001 MHz</td>" in game
    assert "<td>CPU temperature</td><td colspan=\"2\" class=\"muted\">not measured" in game
    assert "Rendered" not in game
    with_fg = _game_export(None, {"rendered_fps": (91.0, "FPS")})
    assert "Rendered (no frame gen)</div><div class=\"value\">91 <span>FPS" in render_session_report(with_fg)


def test_game_section_without_settings_or_frames_and_only_for_games():
    page = render_session_report(_game_export(None, {}))
    assert "did not report its settings" in page and "the whole run" in page
    assert "1% low</div><div class=\"value\"><span>not measured" in page
    export = _game_export(None, {})
    export["runs"][0]["benchmark_type"] = "cpu"
    assert "Game results" not in render_session_report(export)
    export = _game_export(None, {})
    export["runs"][0]["status"] = "failed"
    assert "No completed run" in render_session_report(export)
    hostile = render_session_report(_game_export('Settings used: Res: <script>http://x</script>', {}))
    assert "<script" not in hostile and "http://" not in hostile


def _blender_export(note, status="completed"):
    run = {"benchmark_name": "blender_benchmark_gpu", "benchmark_type": "gpu", "status": status, "score": 8471.68,
           "unit": "samples/min", "run_index": 1, "is_warmup": False, "is_test": False, "note": note,
           "benchmark_version": "5.2.0"}
    values = {"samples_per_minute@monster": (3578.34, "samples/min"), "samples_per_minute@junkshop": (2703.36,
              "samples/min"), "samples_per_minute@classroom": (2189.97, "samples/min"),
              "render_time_s@monster": (30.91, "s"), "peak_memory_mb@monster": (1313.0, "MB"),
              "tel_gpu0_power_avg": (134.1, "W"), "tel_gpu0_power_max": (175.02, "W")}
    m = {k: {"unit": u, "count": 1, "mean": v, "min": v, "max": v, "mixed_units": False} for k, (v, u) in values.items()}
    return {"session": {"id": 1, "name": "s"}, "runs": [run],
            "summaries": {"blender_benchmark_gpu": {"included": 1, "stats": {"mean": 8471.68}}},
            "metric_summaries": {"blender_benchmark_gpu": m},
            "manifest": {"benchmarks": [{"name": "blender_benchmark_gpu", "display_name": "Blender Benchmark 5.2.0 (GPU)"}]}}


def test_blender_has_its_own_section_score_scenes_and_what_ran():
    page = render_session_report(_blender_export(
        "Blender: 5.2.0 LTS; Launcher: 3.3.0; Device: NVIDIA GeForce RTX 5090 Laptop GPU (OPTIX); "
        "Scenes: monster, junkshop, classroom"))
    part = page[page.index("<h2>Blender Benchmark</h2>"):page.index("<h2>Summary")]
    assert "Blender Benchmark 5.2.0 (GPU)</h3>" in part and "8,472 <span>samples/min" in part
    for scene, spm in (("monster", "3,578"), ("junkshop", "2,703"), ("classroom", "2,190")):
        assert f'<div class="label">{scene}</div><div class="value">{spm} ' in part
    assert "<tr><td>monster</td><td>3,578</td><td>30.9 s</td><td>1,313 MB</td></tr>" in part
    assert "<tr><th>Device</th><td>NVIDIA GeForce RTX 5090 Laptop GPU (OPTIX)</td></tr>" in part
    assert "<tr><th>Blender</th><td>5.2.0 LTS</td></tr>" in part
    assert "<td>GPU power</td><td>134 W</td><td>175 W</td>" in part
    assert "Game results" not in page and "Stress tests" not in page


def test_blender_section_old_run_and_failed_run():
    old = render_session_report(_blender_export(None))
    assert "<tr><th>Blender</th><td>5.2.0</td></tr>" in old and "not recorded by this run" in old
    failed = render_session_report(_blender_export("junkshop: exit code 1", status="failed"))
    assert "No score" in failed and "junkshop&#58; exit code 1" in failed
    assert "<h2>Blender Benchmark</h2>" not in render_session_report(_game_export(None, {}))


def test_game_build_and_the_proof_screenshot_are_in_the_report(tmp_path):
    shot = tmp_path / "benchmark_started.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pixels")
    export = _game_export(f"Proof screenshot: {shot}", {})
    export["runs"][0]["benchmark_version"] = "15427801"
    game = render_session_report(export)
    game = game[game.index("Game results"):game.index("<h2>Summary")]
    assert "Game build 15427801 (Steam&#x27;s build number)" in game
    assert '<img class="proof"' in game and "data:image/png;base64,iVBORw0KGgpwaXhlbHM=\"" in game
    other = tmp_path / "secret.png"                    # only the screenshot the app writes, and only a PNG
    other.write_bytes(shot.read_bytes())
    fake = tmp_path / "x" / "benchmark_started.png"
    fake.parent.mkdir()
    fake.write_bytes(b"not a png")
    for path in (other, fake, tmp_path / "gone" / "benchmark_started.png"):
        assert "<img" not in render_session_report(_game_export(f"Proof screenshot: {path}", {}))
    hostile = _game_export('<img class="proof" alt="x" data-proof="AAAA"> data-proof="AAAA"', {})
    hostile["runs"].append({**hostile["runs"][0], "benchmark_name": "other", "benchmark_type": "cpu",
                            "note": 'x <img class="proof" alt="x" data-proof="AAAA">'})
    assert "src=" not in render_session_report(hostile)

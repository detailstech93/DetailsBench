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

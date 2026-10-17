"""Extra metrics (frame capture, telemetry) in the summary, the export, the HTML report and the Compare tab."""

import sys
from unittest.mock import patch

import pytest

from app.database import Database
from app.export import build_session_export, export_to_html, export_to_json
from app.metrics_summary import compare_metrics, metrics_dict, summarize_metrics

NOW = "2026-10-17T12:00:00"


def run(rid, score=100.0, bench="game", status="completed", warmup=False, test=False):
    return {"id": rid, "benchmark_name": bench, "score": score, "status": status, "passed": status == "completed",
            "is_warmup": warmup, "is_test": test}


def test_summary_uses_the_score_inclusion_rules():
    runs = [run(1), run(2), run(3, warmup=True), run(4, score=None, status="failed"), run(5, test=True)]
    metrics = {i: {"fps_avg": (50.0 + i, "FPS")} for i in range(1, 6)}
    out = summarize_metrics(runs, lambda r: metrics[r["id"]])
    m = out["game"]["fps_avg"]
    assert m["count"] == 2 and m["mean"] == pytest.approx(51.5)
    assert (m["min"], m["max"], m["unit"], m["mixed_units"]) == (51.0, 52.0, "FPS", False)
    assert summarize_metrics(runs, lambda r: metrics[r["id"]], include_test=True)["game"]["fps_avg"]["count"] == 3


def test_missing_and_non_finite_readings_are_absent_not_zero():
    runs = [run(1), run(2)]
    metrics = {1: {"tel_gpu_power_w_avg": (200.0, "W"), "x": (float("nan"), "W")}, 2: {}}
    out = summarize_metrics(runs, lambda r: metrics[r["id"]])
    assert out["game"]["tel_gpu_power_w_avg"]["count"] == 1
    assert "x" not in out["game"]


def test_mixed_units_are_not_summarized():
    runs = [run(1), run(2)]
    metrics = {1: {"t": (60.0, "C")}, 2: {"t": (140.0, "F")}}
    m = summarize_metrics(runs, lambda r: metrics[r["id"]])["game"]["t"]
    assert m["mixed_units"] is True and m["mean"] is None and m["count"] == 2


def test_compare_rows_delta_and_notes():
    a = {"game": {"fps": {"unit": "FPS", "mean": 50.0, "mixed_units": False},
                  "only_a": {"unit": "W", "mean": 1.0, "mixed_units": False},
                  "t": {"unit": "C", "mean": 60.0, "mixed_units": False}}}
    b = {"game": {"fps": {"unit": "FPS", "mean": 60.0, "mixed_units": False},
                  "t": {"unit": "F", "mean": 140.0, "mixed_units": False}},
         "other": {"z": {"unit": None, "mean": 0.0, "mixed_units": False}}}
    rows = {(r["benchmark"], r["metric"]): r for r in compare_metrics(a, b)}
    fps = rows[("game", "fps")]
    assert fps["delta"] == pytest.approx(10.0) and fps["delta_percent"] == pytest.approx(20.0) and fps["note"] is None
    assert rows[("game", "only_a")]["note"] == "only on side A" and rows[("game", "only_a")]["delta"] is None
    assert rows[("other", "z")]["note"] == "only on side B"
    assert rows[("game", "t")]["note"] == "units differ" and rows[("game", "t")]["unit"] is None


def test_compare_zero_mean_has_no_percent():
    a = {"g": {"m": {"unit": "%", "mean": 0.0, "mixed_units": False}}}
    b = {"g": {"m": {"unit": "%", "mean": 5.0, "mixed_units": False}}}
    row = compare_metrics(a, b)[0]
    assert row["delta"] == pytest.approx(5.0) and row["delta_percent"] is None


def test_metrics_dict():
    assert metrics_dict({}) is None
    assert metrics_dict({"fps": (60.0, "FPS")}) == {"fps": {"value": 60.0, "unit": "FPS"}}


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "m.db"))


def _session_with_metrics(db, name, fps_values, bench="frame_capture"):
    sid = db.create_session(name, is_manual=True)
    for fps in fps_values:
        rid = db.create_result(sid, bench, "gpu", fps, is_manual=True)
        db.save_result_metrics(rid, {"fps_avg": (fps, "FPS"), "tel_gpu_temperature_max": (70.0, "C"),
                                     "<b>odd</b>": (1.0, None)})
    return sid


def test_export_and_report_include_metrics(db):
    sid = _session_with_metrics(db, "S", (59.0, 61.0))
    export = build_session_export(db, sid, now=NOW)
    assert export["runs"][0]["metrics"]["fps_avg"] == {"value": 59.0, "unit": "FPS"}
    summary = export["metric_summaries"]["frame_capture"]
    assert summary["fps_avg"]["mean"] == pytest.approx(60.0) and summary["fps_avg"]["count"] == 2
    assert '"metric_summaries"' in export_to_json(export)
    html = export_to_html(export)
    assert "<h2>Extra metrics</h2>" in html
    assert "tel_gpu_temperature_max" in html and "Telemetry" in html
    assert "&lt;b&gt;odd&lt;/b&gt;" in html and "<b>odd</b>" not in html


def test_report_without_metrics_has_no_section(db):
    sid = db.create_session("Plain", is_manual=True)
    db.create_result(sid, "cinebench", "cpu", 1000.0, is_manual=True)
    export = build_session_export(db, sid, now=NOW)
    assert export["metric_summaries"] == {} and export["runs"][0]["metrics"] is None
    assert "Extra metrics" not in export_to_html(export)


def test_compare_tab_metric_table(db):
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    QApplication.instance() or QApplication(sys.argv)
    _session_with_metrics(db, "Before", (50.0, 50.0))
    _session_with_metrics(db, "After", (60.0, 60.0))
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(db, {})
    try:
        w.reload_compare_sessions()
        w.run_comparison()
        t = w.compare_metrics_table
        rows = {t.item(r, 1).text(): [t.item(r, c).text() for c in range(t.columnCount())]
                for r in range(t.rowCount())}
        assert rows["fps_avg"] == ["frame_capture", "fps_avg", "FPS", "50.00", "60.00", "+10.00", "+20.00 %", "-"]
        assert rows["tel_gpu_temperature_max"][5:7] == ["+0.00", "+0.00 %"]
    finally:
        w.close()

"""Tests whose score is typed, run several times (owner, 2026-10-21): how many runs first, one field per run,
each score stored as a measured MANUAL run so the report averages them."""

import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.ui import run_tests
from app.ui.record_dialog import ScoresDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_one_field_per_run_empty_ones_skipped_and_only_numbers(qapp):
    d = ScoresDialog("Speedometer 3", "#4", 3, "score", links=["https://x/1"])
    assert len(d.scores) == 3 and d.unit.text() == "score"
    d._try_accept()
    assert d.values is None and "at least one" in d.error.text()
    d.scores[0].setText("31,5")
    d.scores[2].setText("abc")
    d._try_accept()
    assert d.values is None and d.error.text().startswith("Run 3:")
    d.scores[2].setText("33")
    d._try_accept()
    assert d.values["scores"] == [31.5, 33.0] and d.values["runs"] == [(0, 31.5), (2, 33.0)]


def test_pugetbench_keeps_its_application_and_details(qapp):
    from app.pro import pugetbench
    d = ScoresDialog("PugetBench", "#4", 2, puget=True, puget_version="1.2")
    d.application.setCurrentIndex(0)
    d.scores[0].setText("1000")
    d.scores[1].setText("1100")
    d._try_accept()
    app = pugetbench.TESTS[0][1]
    assert d.values["benchmark_name"] == pugetbench.test_for(app) and d.values["scores"] == [1000.0, 1100.0]


class Db:
    def __init__(self):
        self.rows = []

    def get_runs(self, sid, name=None, include_test=False):
        return [r for r in self.rows if r[1] == name]

    def create_result(self, *a, **k):
        self.rows.append((a[0], a[1], a[3], k))


def _window():
    log = []
    return SimpleNamespace(current_session_id=None, db=Db(), run_log=SimpleNamespace(append=log.append), log=log,
                           _ensure_session=lambda: 9, refresh_history_screen=lambda: None)


def test_every_typed_score_is_its_own_measured_manual_run():
    w = _window()
    bm = SimpleNamespace(name="speedometer", display_name="Speedometer 3", version="3.1",
                         type=SimpleNamespace(value="web"))
    seen = []

    def ask(window, name, label, count, puget=False, puget_version=None):
        seen.append((name, label, count, puget))
        return {"scores": [30.0, 31.0, 32.0], "unit": "score", "note": "Chrome"}
    assert run_tests.record_scores(w, bm, 3, ask=ask) == 9
    assert seen == [("Speedometer 3", "new session", 3, False)]
    assert [(r[2], r[3]["run_index"], r[3]["is_manual"], r[3]["status"], r[3]["is_warmup"]) for r in w.db.rows] == \
        [(30.0, 0, True, "completed", False), (31.0, 1, True, "completed", False), (32.0, 2, True, "completed", False)]
    assert run_tests.record_scores(w, bm, 3, ask=lambda *a, **k: None) is None and len(w.db.rows) == 3


def test_a_typed_test_asks_how_many_runs_before_it_starts(monkeypatch):
    launched, offered = [], []
    w = SimpleNamespace(automation_panel=SimpleNamespace(is_running=lambda: False),
                        frame_panel=SimpleNamespace(is_running=lambda: False),
                        _on_launch_clicked=launched.append,
                        tests_page=SimpleNamespace(offer_record=lambda text, fn: offered.append(text)))
    e = SimpleNamespace(source="library", name="Speedometer 3", ref="bm")
    monkeypatch.setattr(run_tests, "ask_count", lambda window, name: None)
    assert not run_tests.start_other(w, e) and launched == []
    monkeypatch.setattr(run_tests, "ask_count", lambda window, name: 3)
    assert run_tests.start_other(w, e) and launched == ["bm"] and "3 times" in offered[0]

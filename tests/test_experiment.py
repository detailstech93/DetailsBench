"""Plan files (app/experiment.py): saved by every run from the Tests screen, read back to run the same again."""

import json
import sys
from datetime import datetime
from unittest.mock import patch

import pytest

from app import experiment
from app.catalog import AUTOMATIC, INSTALLED, MISSING, TestEntry
from app.games.presets import GamePreset
from app.ui.run_tests import RunChoice


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def entry(tid, state=INSTALLED):
    return TestEntry(id=tid, name=tid, group="cpu", mode=AUTOMATIC, state=state, state_reason="", compatible=True,
                     compatibility_reason="", verified=True, source="workload", ref=None)


def test_save_and_load_round_trip(tmp_path):
    choice = RunChoice(4, 1, 30, GamePreset("1920x1080", "dlss", "off", "ultra"), 10, 2)
    path = experiment.save(tmp_path, ["a", "b"], choice, now=datetime(2026, 10, 19, 14, 5, 0))
    assert path.name == "plan_20261019_140500.json" and (path.parent / experiment.LAST).exists()
    plan, why = experiment.load(path)
    assert why is None and plan["tests"] == ["a", "b"]
    assert (plan["repetitions"], plan["warmup_runs"], plan["cooldown_seconds"], plan["extra_runs"],
            plan["stress_minutes"]) == (4, 1, 30, 2, 10)
    assert GamePreset.from_dict(plan["game_preset"]) == GamePreset("1920x1080", "dlss", "off", "ultra")


def test_bad_files_are_refused(tmp_path):
    good = experiment.plan_dict(["a"], RunChoice(3, 0, 0))

    def load(**change):
        f = tmp_path / "p.json"
        f.write_text(json.dumps({**good, **change}), encoding="utf-8")
        return experiment.load(f)
    assert load()[0] is not None
    assert "not a DetailsBench plan" in load(format="other")[1]
    assert "version" in load(version=9)[1]
    assert "no tests" in load(tests=[])[1] and "no tests" in load(tests=[1])[1]
    assert "repetitions" in load(repetitions=0)[1] and "repetitions" in load(repetitions=True)[1]
    assert "cooldown_seconds" in load(cooldown_seconds="5")[1]
    assert "stress_minutes" in load(stress_minutes=-1)[1]
    assert "game_preset" in load(game_preset="ultra")[1]
    (tmp_path / "x.json").write_text("{", encoding="utf-8")
    assert "could not be read" in experiment.load(tmp_path / "x.json")[1]
    assert "could not be read" in experiment.load(tmp_path / "missing.json")[1]


def test_resolve_keeps_order_and_names_missing():
    a, b, gone = entry("a"), entry("b"), entry("c", state=MISSING)
    found, missing = experiment.resolve({"tests": ["b", "x", "a", "c"]}, [a, b, gone])
    assert found == [b, a] and missing == ["x", "c"]


def test_saved_plan_fills_the_dialog_and_a_run_saves_its_plan(qapp, tmp_path):
    from app.database import Database
    from app.ui.main_window import MainWindow
    from app.ui.run_tests import RunTestsDialog, start_tests
    from app.profiles import RunProfile
    saved = {"repetitions": 7, "warmup_runs": 2, "cooldown_seconds": 45, "extra_runs": 0, "stress_minutes": None,
             "game_preset": None, "created": "2026-10-19T14:05:00", "tests": ["a"]}
    d = RunTestsDialog([entry("a")], RunProfile("Standard", 3, 1, 600, 0), [], saved=saved)
    assert d.windowTitle() == "Run a saved plan" and d.choice().extra_runs == 0   # the saved "no extra runs"
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {"cpu_brand": "quick name"})
    try:
        asked = []

        def ask(window, entries, profile, others, games, offered, initial, **kw):
            asked.append((profile.repetitions, profile.warmup_runs, profile.cooldown_seconds, kw))
            return None                                         # cancelled: nothing runs, nothing saved
        assert not start_tests(w, [entry("a")], ask=ask, saved=saved)
        assert asked == [(7, 2, 45, {"saved": saved})]
        assert not (tmp_path / "plans").exists()
        w.automation_panel.start_plan = lambda **kw: False
        w.automation_panel.lhm.active = lambda: False
        start_tests(w, [entry("a")], ask=lambda *a, **k: RunChoice(2, 0, 5))
        plan, _ = experiment.load(tmp_path / "plans" / experiment.LAST)
        assert plan["tests"] == ["a"] and plan["repetitions"] == 2 and plan["cooldown_seconds"] == 5
        w.tests_page.set_entries([entry("a")])
        runs = []
        with patch("app.ui.run_tests.start_tests", lambda win, found, saved=None: runs.append((found, saved))):
            w.run_saved_plan(tmp_path / "plans" / experiment.LAST)
        assert [e.id for e in runs[0][0]] == ["a"] and runs[0][1]["repetitions"] == 2
    finally:
        w.close()

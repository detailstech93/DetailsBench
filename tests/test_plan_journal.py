"""Resuming a plan after a crash (real runner, REAL temporary database, fake workloads)."""

import json

import pytest

from app.automation import journal as jr
from app.automation.runner import AutomationRunner, PlanItem, averages_text, plan_averages
from app.automation.workload import WorkloadResult
from app.database import Database
from tests.test_automation_runner import Scripted, ok


class Crash(BaseException):
    """Stands for the app ending in the middle of a run (BaseException: the runner does not catch it)."""


class Crashing(Scripted):
    def run(self, ctx):
        if ctx.run_index == 2 and not ctx.is_warmup:
            raise Crash()
        return super().run(ctx)


class Configurable(Scripted):
    def configure(self, settings):
        self.settings = settings


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "r.db"))
    return database, database.create_session("auto")


def test_journal_is_kept_current_and_removed_at_the_end(db, tmp_path):
    database, sid = db
    path = tmp_path / "out" / jr.JOURNAL_NAME
    seen = []
    wl = Scripted("tool_a", [ok(1.0), ok(2.0)], on_run=lambda ctx: seen.append(json.loads(path.read_text("utf-8"))))
    item = PlanItem(wl, repetitions=1, warmup_runs=1, timeout_seconds=9, settings={"model": "m"})
    AutomationRunner(database, sid, [item], tmp_path / "out", journal=jr.PlanJournal(path)).run()
    assert seen[0]["current"] == ["tool_a", 0, True] and seen[0]["done"] == []
    assert seen[1]["current"] == ["tool_a", 1, False] and seen[1]["done"] == [["tool_a", 0, True]]
    assert seen[0]["items"] == [{"workload_id": "tool_a", "repetitions": 1, "warmup_runs": 1,
                                 "timeout_seconds": 9, "settings": {"model": "m"}, "retries": 0,
                                 "max_extra_runs": 0}]
    assert not path.exists()


def test_crash_leaves_the_journal_and_resume_finishes_the_plan(db, tmp_path):
    database, sid = db
    out = tmp_path / "out"
    path = out / jr.JOURNAL_NAME
    crashing = Crashing("tool_a", [ok(1.0), ok(10.0)])
    with pytest.raises(Crash):
        AutomationRunner(database, sid, [PlanItem(crashing, repetitions=3, warmup_runs=1)], out,
                         journal=jr.PlanJournal(path)).run()
    data = jr.read_journal(path)
    assert data["current"] == ["tool_a", 2, False] and jr.remaining_runs(data) == 2

    # next start: the same workloads are loaded again, the plan resumes in the same session and folder
    fresh = Scripted("tool_a", [ok(12.0)])
    plan, done, interrupted, missing = jr.plan_from_journal(data, [fresh, Scripted("other", [])])
    assert missing == [] and interrupted == ("tool_a", 2, False) and len(done) == 2
    progress = []
    summary = AutomationRunner(database, data["session_id"], plan, out, journal=jr.PlanJournal(path), done=done,
                               interrupted=interrupted, stamp=data["stamp"], on_progress=progress.append).run()
    assert [(r.run_index, r.result.status) for r in summary.records] == [(2, "failed"), (3, "completed")]
    assert fresh.contexts[0].output_dir.parent.name == f"session_{sid}_{data['stamp']}"
    runs = [(r["run_index"], r["is_warmup"], r["status"], r["score"]) for r in database.get_runs(sid)]
    assert runs == [(0, 1, "completed", 1.0), (1, 0, "completed", 10.0), (2, 0, "failed", None),
                    (3, 0, "completed", 12.0)]
    assert any("interrupted by a crash" in p for p in progress) and not path.exists()


def test_resume_restores_settings_and_reports_missing_tools(tmp_path):
    data = {"session_id": 4, "stamp": "s", "cooldown_seconds": 0, "done": [], "current": None,
            "items": [{"workload_id": "llm", "repetitions": 3, "warmup_runs": 1, "timeout_seconds": None,
                       "settings": {"model": "gemma"}},
                      {"workload_id": "gone", "repetitions": 3, "warmup_runs": 1, "timeout_seconds": None}]}
    llm = Configurable("llm", [])
    plan, done, interrupted, missing = jr.plan_from_journal(data, [llm])
    assert [p.workload.id for p in plan] == ["llm"] and llm.settings == {"model": "gemma"} and missing == ["gone"]
    assert jr.remaining_runs(data) == 8 and interrupted is None and done == set()


def test_unusable_journal_is_ignored(tmp_path):
    assert jr.read_journal(tmp_path / "none.json") is None
    for text in ("{", "[]", json.dumps({"session_id": 1}), json.dumps({"session_id": 1, "stamp": "s", "done": [["a"]],
                                                                       "items": []})):
        (tmp_path / "j.json").write_text(text, encoding="utf-8")
        assert jr.read_journal(tmp_path / "j.json") is None


def test_averages_leave_out_warmups_and_failures(db, tmp_path):
    database, sid = db
    wl = Scripted("tool_a", [ok(100.0), ok(10.0), WorkloadResult(status="failed", error="x"), ok(12.0), ok(14.0)])
    summary = AutomationRunner(database, sid, [PlanItem(wl, repetitions=4, warmup_runs=1)], tmp_path).run()
    assert len(summary.records) == 5
    avg = plan_averages(database.get_runs(sid))["tool_a"]
    assert avg["count"] == 3 and avg["mean"] == 12.0 and avg["unit"] == "pts" and round(avg["cv_percent"], 1) == 16.7
    assert averages_text({"tool_a": avg}) == "tool_a: mean 12 pts over 3 run(s), CV 16.7%"


def test_database_error_ends_the_plan_but_keeps_it_resumable(db, tmp_path):
    import sqlite3
    database, sid = db
    real = database.create_result
    calls = []

    def full(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("database or disk is full")
        return real(*args, **kwargs)
    database.create_result = full
    path = tmp_path / jr.JOURNAL_NAME
    with pytest.raises(sqlite3.OperationalError):
        AutomationRunner(database, sid, [PlanItem(Scripted("tool_a", [ok(1.0), ok(2.0), ok(3.0)]), repetitions=3,
                                                  warmup_runs=0)], tmp_path, journal=jr.PlanJournal(path)).run()
    data = jr.read_journal(path)
    assert data["done"] == [["tool_a", 1, False]] and data["current"] == ["tool_a", 2, False]

"""AutomationRunner with fake workloads (real runner, REAL temporary database)."""

import json
import threading

import pytest

from app.automation.runner import AutomationRunner, PlanItem
from app.automation.workload import Workload, WorkloadResult
from app.database import Database


class Scripted(Workload):
    """Returns the scripted results in order."""

    def __init__(self, wid, results, on_run=None):
        self.id = wid
        self.display_name = wid
        self.results = list(results)
        self.contexts = []
        self.on_run = on_run

    def version(self):
        return "1.0"

    def run(self, ctx):
        self.contexts.append(ctx)
        if self.on_run:
            self.on_run(ctx)
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(score):
    return WorkloadResult(status="completed", score=score, unit="pts")


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "r.db"))
    return database, database.create_session("auto")


def test_warmup_then_runs_stored_in_order(db, tmp_path):
    database, sid = db
    wl = Scripted("tool_a", [ok(1.0), ok(10.0), ok(11.0)])
    progress = []
    summary = AutomationRunner(database, sid, [PlanItem(wl, repetitions=2, warmup_runs=1, timeout_seconds=7)],
                               tmp_path / "out", on_progress=progress.append).run()
    assert [(r.run_index, r.is_warmup) for r in summary.records] == [(0, True), (1, False), (2, False)]
    assert wl.contexts[0].timeout_seconds == 7 and wl.contexts[0].is_warmup
    runs = database.get_runs(sid)
    assert [(r["run_index"], r["is_warmup"], r["score"], r["benchmark_version"]) for r in runs] == \
        [(0, 1, 1.0, "1.0"), (1, 0, 10.0, "1.0"), (2, 0, 11.0, "1.0")]
    assert not summary.stopped and summary.not_started == 0
    assert any("run 2 completed (11 pts)" in p for p in progress)


def test_failure_stored_without_score_and_gets_diagnostics(db, tmp_path):
    database, sid = db
    wl = Scripted("tool_b", [WorkloadResult(status="timed_out", error="timeout after 5 s"),
                             RuntimeError("boom")])
    summary = AutomationRunner(database, sid, [PlanItem(wl, repetitions=2, warmup_runs=0)], tmp_path / "o").run()
    runs = database.get_runs(sid)
    assert [(r["status"], r["score"], r["note"]) for r in runs] == \
        [("timed_out", None, "timeout after 5 s"), ("failed", None, "workload raised: boom")]
    diag = json.loads(summary.records[0].diagnostics.read_text(encoding="utf-8"))
    assert diag["result"]["status"] == "timed_out"
    assert diag["screenshot"]["taken"] is False and "system" in diag


def test_emergency_stop_mid_run(db, tmp_path):
    database, sid = db
    stop = threading.Event()
    runner = AutomationRunner(database, sid, [PlanItem(Scripted("x", [ok(1.0), ok(2.0)]), 2, 0),
                                              PlanItem(Scripted("y", [ok(3.0)]), 1, 0)],
                              tmp_path / "o", stop_event=stop)
    # the second run sees the stop and reports itself cancelled
    first = runner.plan[0].workload
    first.results[1] = WorkloadResult(status="cancelled", error="stopped by user")
    first.on_run = lambda ctx: stop.set() if ctx.run_index == 2 else None
    summary = runner.run()
    assert summary.stopped and summary.not_started == 1
    assert [r["status"] for r in database.get_runs(sid)] == ["completed", "cancelled"]


def test_stop_during_cooldown(db, tmp_path):
    database, sid = db
    stop = threading.Event()
    wl = Scripted("z", [ok(1.0), ok(2.0)], on_run=lambda ctx: threading.Timer(0.2, stop.set).start())
    summary = AutomationRunner(database, sid, [PlanItem(wl, 2, 0)], tmp_path / "o",
                               cooldown_seconds=30, stop_event=stop).run()
    assert summary.stopped and summary.not_started == 1 and len(summary.records) == 1


def test_screenshot_function_used(db, tmp_path):
    database, sid = db
    wl = Scripted("s", [WorkloadResult(status="failed", error="exit code 2")])

    def shot(path):
        path.write_bytes(b"png")
        return True

    summary = AutomationRunner(database, sid, [PlanItem(wl, 1, 0)], tmp_path / "o", screenshot=shot).run()
    diag = json.loads(summary.records[0].diagnostics.read_text(encoding="utf-8"))
    assert diag["screenshot"]["taken"] is True
    assert (summary.records[0].run_dir / "screenshot.png").read_bytes() == b"png"

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


def test_python_spec_is_unavailable_in_the_packaged_app():
    from app.automation.cli_workload import CliWorkload, load_tool_specs
    spec = next(s for s in load_tool_specs()[0] if s.id == "selftest_python")
    packaged = CliWorkload(spec, python_executable=None)
    assert packaged.availability() == (False, "needs Python, which the packaged app does not include")
    assert CliWorkload(spec, python_executable="py.exe").availability()[0]


def bad(error="exit code 1", status="failed"):
    return WorkloadResult(status=status, error=error)


def test_a_failed_run_is_kept_and_run_once_more(db, tmp_path):
    database, sid = db
    wl = Scripted("tool_a", [ok(1.0), bad(), ok(10.0), ok(11.0)])
    summary = AutomationRunner(database, sid, [PlanItem(wl, repetitions=2, warmup_runs=1, retries=1)],
                               tmp_path / "out").run()
    runs = database.get_runs(sid, include_test=True)
    assert [(r["run_index"], r["status"], r["score"]) for r in runs] == \
        [(0, "completed", 1.0), (1, "failed", None), (1, "completed", 10.0), (2, "completed", 11.0)]
    assert runs[2]["note"] == "Retry after a failed try (failed: exit code 1)"
    assert summary.records[2].run_dir.name == "tool_a_r1_retry1" and len(wl.contexts) == 4


def test_retries_are_limited_and_skip_what_a_retry_cannot_fix(db, tmp_path):
    database, sid = db
    twice = Scripted("tool_a", [bad(), bad("timeout", "timed_out"), ok(5.0)])
    AutomationRunner(database, sid, [PlanItem(twice, repetitions=2, warmup_runs=0, retries=1)], tmp_path / "a").run()
    assert [r["status"] for r in database.get_runs(sid, "tool_a", include_test=True)] == \
        ["failed", "timed_out", "completed"]                 # one retry only, then the next run
    for result in (bad("could not start: not found"), bad("x", "invalid"), bad("x", "skipped")):
        wl = Scripted("tool_b", [result, ok(1.0)])
        AutomationRunner(database, sid, [PlanItem(wl, repetitions=1, warmup_runs=0, retries=1)], tmp_path / "b").run()
        assert len(wl.contexts) == 1
    warm = Scripted("tool_c", [bad(), ok(2.0)])              # warmups are not retried
    AutomationRunner(database, sid, [PlanItem(warm, repetitions=1, warmup_runs=1, retries=1)], tmp_path / "c").run()
    assert len(warm.contexts) == 2
    off = Scripted("tool_d", [bad()])                        # retries=0 (game plans): as before
    AutomationRunner(database, sid, [PlanItem(off, repetitions=1, warmup_runs=0)], tmp_path / "d").run()
    assert len(off.contexts) == 1


def test_a_crash_is_not_run_again(db, tmp_path):
    # Radxa Dragon Q8B, 2026-10-21: vkmark crashed in its warm-up and its run, the retry started and the board went off
    database, sid = db
    crash = bad("crashed (SIGSEGV, exit code -11)")
    crash.exit_code = -11
    wl = Scripted("vkmark", [crash, ok(1.0), ok(2.0)])
    nxt = Scripted("stress", [ok(3.0)])
    progress = []
    AutomationRunner(database, sid, [PlanItem(wl, repetitions=2, warmup_runs=1, retries=1),
                                     PlanItem(nxt, repetitions=1, warmup_runs=0)],
                     tmp_path / "o", on_progress=progress.append).run()
    assert len(wl.contexts) == 1 and len(nxt.contexts) == 1
    assert [r["status"] for r in database.get_runs(sid, "vkmark", include_test=True)] == ["failed"]
    assert any("crashed" in p and "not started" in p for p in progress)


def test_uneven_scores_get_extra_runs_until_even_or_used_up(db, tmp_path):
    database, sid = db
    wl = Scripted("tool_a", [ok(100.0), ok(100.0), ok(130.0), ok(100.0), ok(101.0), ok(99.0)])
    other = Scripted("tool_b", [ok(7.0)])
    progress = []
    AutomationRunner(database, sid, [PlanItem(wl, repetitions=3, warmup_runs=0, max_extra_runs=2),
                                     PlanItem(other, repetitions=1, warmup_runs=0, max_extra_runs=2)],
                     tmp_path / "out", on_progress=progress.append).run()
    runs = database.get_runs(sid, "tool_a", include_test=True)
    assert [r["run_index"] for r in runs] == [1, 2, 3, 4, 5]        # 2 extra runs, the 6th score is never asked
    assert runs[3]["note"].startswith("Extra run: the scores varied 15.7% (above 5%), extra run 1 of 2")
    assert runs[4]["note"].startswith("Extra run: the scores varied 14.0%")
    assert len(wl.results) == 1 and len(other.contexts) == 1         # then the next test as planned
    even = Scripted("tool_c", [ok(100.0), ok(101.0), ok(100.0)])
    AutomationRunner(database, sid, [PlanItem(even, repetitions=3, warmup_runs=0, max_extra_runs=2)],
                     tmp_path / "e").run()
    assert len(even.contexts) == 3
    two = Scripted("tool_d", [ok(1.0), ok(9.0), ok(5.0)])            # 2 scores: too few to judge
    AutomationRunner(database, sid, [PlanItem(two, repetitions=2, warmup_runs=0, max_extra_runs=2)],
                     tmp_path / "t").run()
    assert len(two.contexts) == 2


def test_the_report_tile_counts_retries_and_extra_runs():
    from app.report import _passmark_section
    run = {"benchmark_name": "passmark_cpu", "is_warmup": False, "unit": "points"}
    export = {"session": {}, "summaries": {"passmark_cpu": {"stats": {"mean": 100.0}}}, "runs": [
        {**run, "status": "failed", "note": "exit code 1"},
        {**run, "status": "completed", "note": "Retry after a failed try (failed: exit code 1)"},
        {**run, "status": "completed", "note": "x"}, {**run, "status": "completed", "note": "y"},
        {**run, "status": "completed", "note": "Extra run: the scores varied 9.0% (above 5%), extra run 1 of 2"}]}
    html = _passmark_section(export)
    assert "Run again after a failure</th><td>1 (" in html and "Extra runs</th><td>1 (" in html


def test_skip_mid_run_ends_only_that_test(db, tmp_path):
    database, sid = db
    holder = {}

    def skip_on_second(ctx):
        if ctx.run_index == 2:
            holder["runner"].skip_test()
            assert ctx.stop_event.is_set()          # the run in progress sees the skip as its stop

    x = Scripted("x", [ok(1.0), WorkloadResult(status="cancelled", error="stopped by user"), ok(9.0)],
                 on_run=skip_on_second)
    runner = AutomationRunner(database, sid, [PlanItem(x, 3, 0), PlanItem(Scripted("y", [ok(3.0)]), 1, 0)],
                              tmp_path / "o")
    holder["runner"] = runner
    summary = runner.run()
    assert not summary.stopped
    assert [(r["status"], r["note"]) for r in database.get_runs(sid)] ==         [("completed", None), ("skipped", "Skipped by the user after 1 of 3 runs"), ("completed", None)]
    assert len(x.contexts) == 2 and not x.contexts[1].stop_event.is_set()


def test_skip_during_a_pause_skips_the_test_about_to_run(db, tmp_path):
    database, sid = db
    holder = {}
    x = Scripted("x", [ok(1.0)], on_run=lambda ctx: threading.Timer(0.2, holder["runner"].skip_test).start())
    y = Scripted("y", [ok(3.0), ok(4.0)])
    runner = AutomationRunner(database, sid, [PlanItem(x, 1, 0), PlanItem(y, 2, 0)], tmp_path / "o",
                              cooldown_seconds=30)
    holder["runner"] = runner
    summary = runner.run()
    assert not summary.stopped and y.contexts == []
    assert [(r["status"], r["note"]) for r in database.get_runs(sid)] == \
        [("completed", None), ("skipped", "Skipped by the user before any result")]


def test_skip_as_a_run_ends_well_skips_its_other_runs(db, tmp_path):
    database, sid = db
    holder = {}
    x = Scripted("x", [ok(1.0), ok(2.0), ok(3.0)],
                 on_run=lambda ctx: holder["runner"].skip_test() if ctx.run_index == 2 else None)
    runner = AutomationRunner(database, sid, [PlanItem(x, 3, 0)], tmp_path / "o")
    holder["runner"] = runner
    runner.run()
    assert [(r["run_index"], r["status"], r["score"], r["note"]) for r in database.get_runs(sid)] == \
        [(1, "completed", 1.0, None), (2, "completed", 2.0, None),
         (3, "skipped", None, "Skipped by the user after 2 of 3 runs")]


def test_skip_with_nothing_going_on_does_nothing(db, tmp_path):
    database, sid = db
    runner = AutomationRunner(database, sid, [PlanItem(Scripted("x", [ok(1.0)]), 1, 0)], tmp_path / "o")
    assert runner.skip_test() is None
    runner.run()
    assert [r["status"] for r in database.get_runs(sid)] == ["completed"]


def test_after_skips_the_last_step_reaches_the_end(db, tmp_path):
    """Owner, 2026-10-21: two tests, each skipped while running; the bar stayed at 50 % (the count had shrunk)."""
    database, sid = db
    holder, steps = {}, []
    skip = lambda ctx: holder["runner"].skip_test() if ctx.run_index == 1 else None
    x = Scripted("x", [WorkloadResult(status="cancelled", error="stopped")] * 3, on_run=skip)
    y = Scripted("y", [WorkloadResult(status="cancelled", error="stopped")] * 3, on_run=skip)
    runner = AutomationRunner(database, sid, [PlanItem(x, 3, 0), PlanItem(y, 3, 0)], tmp_path / "o",
                              on_step=lambda done, total, text: steps.append((done, total)))
    holder["runner"] = runner
    assert not runner.run().stopped
    assert steps[-1][0] == steps[-1][1]


def test_marks_follow_each_test_for_its_dot(db, tmp_path):
    # owner, 2026-10-22: a dot by each test of the plan, green blinking while it runs, then green or red
    database, sid = db
    seen = []
    first = Scripted("tool_a", [ok(1.0), ok(2.0)])
    broken = Scripted("tool_b", [bad(), bad()])
    last = Scripted("tool_c", [ok(3.0)])
    plan = [PlanItem(first, repetitions=2, warmup_runs=0), PlanItem(broken, repetitions=2, warmup_runs=0),
            PlanItem(last, repetitions=1, warmup_runs=0)]
    runner = AutomationRunner(database, sid, plan, tmp_path / "o")
    for wl in (first, broken, last):
        wl.on_run = lambda ctx: seen.append(runner.marks())
    assert runner.marks() == {}
    runner.run()
    assert seen[0] == {"tool_a": "running"} and seen[1] == {"tool_a": "running"}
    assert seen[2] == {"tool_a": "ok", "tool_b": "running"}
    assert seen[-1] == {"tool_a": "ok", "tool_b": "failed", "tool_c": "running"}
    assert runner.marks() == {"tool_a": "ok", "tool_b": "failed", "tool_c": "ok"}     # solid after the run


def test_not_enough_memory_is_not_tried_again(db, tmp_path):
    # Radxa Dragon Q8B, 2026-10-22: LM Studio refused the 27B model three times in a row
    database, sid = db
    wl = Scripted("llama_benchy", [bad("not enough memory: LM Studio would not load q (it needs about 19.24 GB)"),
                                   ok(1.0)])
    AutomationRunner(database, sid, [PlanItem(wl, repetitions=1, warmup_runs=0, retries=1)], tmp_path / "o").run()
    assert len(wl.contexts) == 1


def test_a_test_queued_during_the_run_runs_after_the_planned_ones(db, tmp_path):
    """Queue next (owner, 2026-10-23): added while a test runs, it runs last in the same session; a test already in
    the run is not added twice; after the run nothing is queued."""
    from app.automation.journal import PlanJournal
    database, sid = db
    later = Scripted("later", [ok(5.0), ok(6.0)])
    seen = {}

    def add(ctx):
        if not seen:
            seen["queued"] = runner.queue([PlanItem(later, 1, 1), PlanItem(Scripted("a", []), 1, 0)])
            seen["marks"] = runner.marks()
    a = Scripted("a", [ok(1.0), ok(2.0)], on_run=add)
    journal = PlanJournal(tmp_path / "plan.json")
    journal.finish = lambda: None                       # keep the file to read it here
    runner = AutomationRunner(database, sid, [PlanItem(a, 2, 0)], tmp_path / "o", journal=journal)
    summary = runner.run()
    taken, refused = seen["queued"]
    assert [p.workload.id for p in taken] == ["later"] and refused == {"a": "already in this run"}
    assert seen["marks"]["later"] == "queued"
    assert [(r.workload_id, r.run_index, r.is_warmup) for r in summary.records] == \
        [("a", 1, False), ("a", 2, False), ("later", 0, True), ("later", 1, False)]
    assert {r["benchmark_name"] for r in database.get_runs(sid)} == {"a", "later"}
    assert [i["workload_id"] for i in json.loads((tmp_path / "plan.json").read_text())["items"]] == ["a", "later"]
    assert runner.marks()["later"] == "ok"
    assert runner.queue([PlanItem(Scripted("late", []), 1, 0)]) == ([], {"late": "the run has ended"})

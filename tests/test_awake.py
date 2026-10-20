"""Keep the screen on during a run, and no screen test on a locked screen (overnight run, 2026-10-20)."""

import subprocess

import pytest

from app.automation import awake
from app.automation.runner import AutomationRunner, PlanItem
from app.automation.workload import Workload, WorkloadResult
from app.database import Database
from app.pro import passmark


class FakeKernel:
    def __init__(self, answer=1):
        self.calls = []
        self.answer = answer

    def SetThreadExecutionState(self, flags):
        self.calls.append(flags)
        return self.answer


def test_windows_asks_for_the_screen_and_gives_it_back():
    k = FakeKernel()
    with awake.keep_awake("win32", kernel32=k) as held:
        assert held
        assert k.calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED | awake.ES_DISPLAY_REQUIRED]
    assert k.calls[-1] == awake.ES_CONTINUOUS


def test_windows_refused_is_not_given_back():
    k = FakeKernel(answer=0)
    with awake.keep_awake("win32", kernel32=k) as held:
        assert not held
    assert len(k.calls) == 1


class FakeProc:
    def __init__(self):
        self.ended = False

    def terminate(self):
        self.ended = True

    def wait(self, timeout):
        return 0


def test_linux_holds_systemd_inhibit_for_the_run(monkeypatch):
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/" + name)
    started = []

    def popen(command, **kw):
        started.append(command)
        started.append(FakeProc())
        return started[-1]
    with awake.keep_awake("linux", popen=popen) as held:
        assert held and not started[1].ended
    assert started[0][:2] == ["systemd-inhibit", "--what=idle:sleep"]
    assert started[1].ended


def test_nothing_to_hold_is_fine(monkeypatch):
    monkeypatch.setattr(awake.shutil, "which", lambda name: None)
    with awake.keep_awake("linux", popen=lambda *a, **k: pytest.fail("nothing should start")) as held:
        assert not held


def test_mac_uses_caffeinate(monkeypatch):
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/" + name)
    assert awake._helper_command("darwin")[:3] == ["caffeinate", "-d", "-i"]


@pytest.mark.parametrize("out, expected", [("LockedHint=yes\n", True), ("LockedHint=no\n", False), ("", None)])
def test_linux_lock_from_logind(monkeypatch, out, expected):
    monkeypatch.setenv("XDG_SESSION_ID", "3")
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/loginctl")
    seen = []

    def run(command, **kw):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")
    assert awake._linux_locked(run=run) is expected
    assert seen == [["loginctl", "show-session", "3", "-p", "LockedHint"]]


def test_other_systems_cannot_tell():
    assert awake.screen_locked("darwin") is None


class Screen(Workload):
    def __init__(self, wid, needs):
        self.id = self.display_name = wid
        self.needs_desktop = needs
        self.ran = 0

    def version(self):
        return "1"

    def run(self, ctx):
        self.ran += 1
        return WorkloadResult(status="completed", score=5.0, unit="pts")


def test_a_screen_test_is_skipped_on_a_locked_screen_and_the_rest_run(tmp_path):
    database = Database(str(tmp_path / "r.db"))
    sid = database.create_session("auto")
    held = []
    draws, computes = Screen("draws", True), Screen("computes", False)

    def keep():
        held.append("on")
        return awake.keep_awake("none")
    plan = [PlanItem(draws, repetitions=1, warmup_runs=0), PlanItem(computes, repetitions=1, warmup_runs=0)]
    summary = AutomationRunner(database, sid, plan, tmp_path / "out", screen_locked=lambda: True, keep_awake=keep).run()
    assert held == ["on"]
    assert draws.ran == 0 and computes.ran == 1
    statuses = [(r.workload_id, r.result.status) for r in summary.records]
    assert statuses == [("draws", "skipped"), ("computes", "completed")]
    assert "screen was locked" in summary.records[0].result.error


def test_an_unlocked_screen_runs_it(tmp_path):
    database = Database(str(tmp_path / "r.db"))
    draws = Screen("draws", True)
    plan = [PlanItem(draws, repetitions=1, warmup_runs=0)]
    AutomationRunner(database, database.create_session("auto"), plan, tmp_path / "out",
                     screen_locked=lambda: False, keep_awake=lambda: awake.keep_awake("none")).run()
    assert draws.ran == 1


def test_passmark_timeouts_match_its_length():
    assert passmark.PassMarkWorkload("passmark_3d", exe=None).timeout_seconds == 900
    assert passmark.PassMarkWorkload("passmark_3d", exe=None).needs_desktop
    for t in ("passmark_cpu", "passmark_2d", "passmark_memory"):
        wl = passmark.PassMarkWorkload(t, exe=None)
        assert wl.timeout_seconds == 600 and not wl.needs_desktop

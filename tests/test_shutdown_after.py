"""Shut down after the run: only when everything is saved, after a countdown, with the system's own command.
Nothing here shuts anything down: the command, the countdown and the database are fakes."""

import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.ui import shutdown_after as sa


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class Db:
    def __init__(self, rows=(1,), fail=False):
        self.rows, self.fail = list(rows), fail

    def get_runs(self, session_id, include_test=False):
        if self.fail:
            raise RuntimeError("locked")
        return self.rows


def _window(db):
    label = SimpleNamespace(value="Session #5 done.")
    label.text = lambda: label.value
    label.setText = lambda t: setattr(label, "value", t)
    return SimpleNamespace(db=db, status_label=label)


SUMMARY = SimpleNamespace(records=[1, 2], stopped=False)


def test_the_systems_own_command_and_not_in_wsl():
    assert sa.command("win32") == ["shutdown", "/s", "/t", "0"]
    assert sa.command("linux") == ["systemctl", "poweroff"]
    assert sa.command("darwin") is None and sa.why_not_offered("darwin")
    assert sa.why_not_offered("linux", wsl=lambda: False) is None
    assert "WSL" in sa.why_not_offered("linux", wsl=lambda: True)
    assert sa.why_not_offered("win32", wsl=lambda: True) is None


def test_only_when_the_run_ended_and_everything_is_saved(tmp_path):
    report = tmp_path / "r.html"
    report.write_text("<html>")
    assert sa.why_not_now(Db(), 5, SUMMARY, report) is None
    assert "stopped" in sa.why_not_now(Db(), 5, SimpleNamespace(records=[1], stopped=True), report)
    assert "stopped" in sa.why_not_now(Db(), 5, "crashed", report)
    assert "database" in sa.why_not_now(Db(rows=()), 5, SUMMARY, report)
    assert "locked" in sa.why_not_now(Db(fail=True), 5, SUMMARY, report)
    assert "report" in sa.why_not_now(Db(), 5, SUMMARY, None)
    assert "report" in sa.why_not_now(Db(), 5, SUMMARY, tmp_path / "missing.html")
    assert sa.why_not_now(Db(), None, SUMMARY, report)


def test_the_countdown_cancel_and_a_refused_command_keep_the_computer_on(tmp_path):
    report = tmp_path / "r.html"
    report.write_text("<html>")
    calls = []
    w = _window(Db())
    assert sa.after_run(w, 5, SUMMARY, report, countdown=lambda win: True, shutdown=lambda: calls.append(1))
    assert calls == [1] and "Shutting down" in w.status_label.text()
    w = _window(Db())
    assert not sa.after_run(w, 5, SUMMARY, report, countdown=lambda win: False, shutdown=lambda: calls.append(2))
    assert calls == [1] and "not shut down: cancelled" in w.status_label.text()
    w = _window(Db())
    assert not sa.after_run(w, 5, SUMMARY, None, countdown=lambda win: calls.append(3), shutdown=lambda: None)
    assert calls == [1] and "report was not written" in w.status_label.text()
    w = _window(Db())
    assert not sa.after_run(w, 5, SUMMARY, report, countdown=lambda win: True, shutdown=lambda: "Access denied")
    assert "the system refused: Access denied" in w.status_label.text()


def test_shut_down_runs_the_command_with_a_timeout_and_says_why_it_failed():
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw["timeout"]))
        return subprocess.CompletedProcess(argv, 1, "", "Failed to power off system: Access denied\n")
    assert sa.shut_down(run, "linux") == "Failed to power off system: Access denied"
    assert seen == [(["systemctl", "poweroff"], 30)]
    ok = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
    assert sa.shut_down(ok, "win32") is None and sa.shut_down(ok, "darwin")


def test_the_countdown_window_ends_by_itself(qapp):
    d = sa.CountdownDialog(seconds=2)
    d.timer.stop()
    assert "2 s" in d.label.text()
    d.tick()
    assert "1 s" in d.label.text() and d.result() == 0
    d.tick()
    assert d.result() == 1


def test_the_run_dialog_has_the_box_off_by_default(qapp, monkeypatch):
    from app.ui.run_tests import RunTestsDialog
    monkeypatch.setattr(sa, "why_not_offered", lambda *a, **k: None)
    entry = SimpleNamespace(name="7-Zip", id="sevenzip_benchmark")
    profile = SimpleNamespace(repetitions=3, warmup_runs=1, cooldown_seconds=10)
    d = RunTestsDialog([entry], profile, [])
    assert not d.choice().shutdown_after
    d.shutdown_after.setChecked(True)
    assert d.choice().shutdown_after
    monkeypatch.setattr(sa, "why_not_offered", lambda *a, **k: "not offered in WSL")
    d = RunTestsDialog([entry], profile, [])
    d.shutdown_after.setChecked(True)
    assert not d.shutdown_after.isEnabled() and not d.choice().shutdown_after


def test_a_skip_cancels_the_shutdown(tmp_path):
    """Owner, 2026-10-21: a Skip of the last (or only) test keeps the PC on; a Skip in the middle does not."""
    from types import SimpleNamespace
    from app.automation.runner import SKIPPED_BY_USER
    from app.ui import shutdown_after
    report = tmp_path / "r.html"
    report.write_text("x", encoding="utf-8")
    db = SimpleNamespace(get_runs=lambda sid, include_test=True: [{"id": 1}])
    rec = lambda error: SimpleNamespace(result=SimpleNamespace(error=error))
    done = SimpleNamespace(stopped=False, records=[rec(None)])
    assert shutdown_after.why_not_now(db, 1, done, report) is None
    skipped = SimpleNamespace(stopped=False, records=[rec(None), rec(SKIPPED_BY_USER + " before any result")])
    assert "skipped" in shutdown_after.why_not_now(db, 1, skipped, report)
    middle = SimpleNamespace(stopped=False, records=[rec(SKIPPED_BY_USER + " after 1 of 3 runs"), rec(None)])
    assert shutdown_after.why_not_now(db, 1, middle, report) is None


def test_an_emergency_stop_or_stop_on_the_phone_cancels_the_countdown(monkeypatch):
    """Owner, 2026-10-21: an emergency stop always keeps the computer on, also during the 60 s countdown."""
    import sys
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from app.ui import shutdown_after
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = shutdown_after.CountdownDialog(None, seconds=60)
    shutdown_after._open.append(dialog)
    try:
        assert shutdown_after.pending() and shutdown_after.seconds_left() == 60     # shown on the phone too
        assert shutdown_after.cancel_pending() and dialog.result() == 0 and not dialog.timer.isActive()
    finally:
        shutdown_after._open.remove(dialog)
    assert not shutdown_after.pending() and not shutdown_after.cancel_pending() and shutdown_after.seconds_left() is None

"""A Steam game run from the Tests screen writes and opens its report (owner, 2026-10-23: Shadow of the Tomb Raider
ran, the game closed and no report came)."""

from pathlib import Path
from types import SimpleNamespace

from app.automation.runner import RunSummary
from app.ui import reports_folder, run_tests


def _window(tmp_path, offered):
    return SimpleNamespace(db="db", status_label=SimpleNamespace(setText=lambda t: offered.append(t)),
                           tests_page=SimpleNamespace(offer_report=lambda fn: offered.append("offer")))


def test_a_stored_game_run_gets_its_report(tmp_path, monkeypatch):
    offered, opened, written = [], [], []
    monkeypatch.setattr(reports_folder, "folder", lambda window: tmp_path)
    monkeypatch.setattr(run_tests, "write_report",
                        lambda db, sid, folder: written.append((sid, folder)) or Path(folder) / "SOTTR.html")
    w = _window(tmp_path, offered)
    done = RunSummary(records=["one run"])
    assert run_tests.game_report(w, 37, done, opener=opened.append) == tmp_path / "SOTTR.html"
    assert written == [(37, tmp_path)] and opened == [tmp_path / "SOTTR.html"] and "offer" in offered
    assert w.last_report_path == tmp_path / "SOTTR.html"


def test_no_report_when_nothing_was_stored(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(reports_folder, "folder", lambda window: tmp_path)
    monkeypatch.setattr(run_tests, "write_report", lambda *a: (_ for _ in ()).throw(AssertionError("written")))
    w = _window(tmp_path, [])
    assert run_tests.game_report(w, None, RunSummary(records=["x"]), opener=opened.append) is None
    assert run_tests.game_report(w, 37, "failed: the game did not start", opener=opened.append) is None
    assert run_tests.game_report(w, 37, RunSummary(), opener=opened.append) is None and opened == []

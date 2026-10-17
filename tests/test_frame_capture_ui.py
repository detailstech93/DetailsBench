"""Frame capture panel: fake process list and a fake elevated runner (no UAC, no PresentMon),
REAL CSV parsing, statistics, runner and temporary database."""

import sys
import threading
from functools import partial
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.database import Database
from app.frames.elevated import ElevatedOutcome
from app.frames.presentmon import FrameCaptureWorkload
from app.paths import load_settings
from app.profiles import RunProfile
from app.ui.frame_capture_panel import FrameCapturePanel, list_processes
from app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def writes_csv(times, outcome=None, calls=None):
    def run(executable, args, cwd, timeout, stop_event):
        if calls is not None:
            calls.append(args)
        out = args[args.index("--output_file") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write("Application,ProcessID,FrameTime\n" + "".join(f"game.exe,42,{t}\n" for t in times))
        return outcome or ElevatedOutcome("exited", exit_code=0)
    return run


@pytest.fixture
def panel(qapp, tmp_path):
    exe = tmp_path / "PresentMon.exe"
    exe.write_bytes(b"MZ")
    db = Database(str(tmp_path / "f.db"))
    p = FrameCapturePanel(db, lambda: RunProfile(name="p", repetitions=2, warmup_runs=0),
                          lambda: None, data_dir=tmp_path / "data",
                          lister=lambda: [("game.exe", 42), ("other.exe", 7)], output_root=tmp_path / "runs")
    p.asked = []
    p.confirm = lambda text: (p.asked.append(text), True)[1]
    p.ask_presentmon_file = lambda: str(exe)
    return p, db, tmp_path


def load_list(qapp, p):
    p.refresh_processes()
    assert p._list_worker.wait(5000)
    qapp.processEvents()


def test_select_presentmon_saves_setting(panel):
    p, _, tmp = panel
    assert "PresentMon not selected" in p.path_label.text()
    p.select_presentmon()
    assert load_settings(tmp / "data")[0] == {"presentmon_path": str(tmp / "PresentMon.exe")}
    assert str(tmp / "PresentMon.exe") in p.path_label.text() and "not yet verified" in p.path_label.text()


def test_frameview_copy_refused(panel):
    p, _, tmp = panel
    bundled = tmp / "FrameView" / "PresentMon_x64.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"MZ")
    p.ask_presentmon_file = lambda: str(bundled)
    p.select_presentmon()
    assert "Not saved" in p.status_label.text() and load_settings(tmp / "data")[0] == {}


def test_needs_presentmon_and_process(qapp, panel):
    p, db, _ = panel
    assert p.start_capture() is False and "Select PresentMon" in p.status_label.text()
    p.select_presentmon()
    assert p.start_capture() is False and "Pick the game process" in p.status_label.text()
    assert db.get_sessions(include_test=True) == [] and p.asked == []


def test_capture_stores_fps_and_low(qapp, panel):
    p, db, _ = panel
    p.select_presentmon()
    load_list(qapp, p)
    assert p.process_combo.count() == 2 and p.process_combo.itemText(0) == "game.exe (pid 42)"
    calls = []
    times = [10.0] * 198 + [50.0, 30.0]
    p.workload_factory = partial(FrameCaptureWorkload, elevated_runner=writes_csv(times, calls=calls),
                                 version_reader=lambda path: "2.3.0")
    assert p.start_capture() is True
    assert "UAC prompt for EACH of the 2 captures" in p.asked[0] and "--process_id 42" in p.asked[0]
    assert p.worker.wait(30000)
    qapp.processEvents()
    runs = db.get_runs(p.last_session_id)
    assert [(r["status"], r["unit"]) for r in runs] == [("completed", "fps")] * 2
    assert runs[0]["score"] == pytest.approx(1000 * 200 / sum(times))
    metrics = db.get_result_metrics(runs[0]["id"])
    assert metrics["low_1pct_fps"] == (pytest.approx(25.0), "fps") and metrics["frames"] == (200.0, "frames")
    assert len(calls) == 2 and "1% low 25.0" in p.status_label.text()
    manifest = db.get_session_manifest(p.last_session_id)
    assert manifest["benchmarks"][0]["version"] == "2.3.0"


def test_declined_uac_cancels_the_rest(qapp, panel):
    p, db, _ = panel
    p.select_presentmon()
    load_list(qapp, p)
    p.workload_factory = partial(FrameCaptureWorkload, version_reader=lambda path: None,
                                 elevated_runner=lambda *a: ElevatedOutcome("uac_declined"))
    assert p.start_capture()
    assert p.worker.wait(30000)
    qapp.processEvents()
    runs = db.get_runs(p.last_session_id)
    assert [(r["status"], r["score"]) for r in runs] == [("cancelled", None)]
    assert "1 capture(s) not started" in p.status_label.text()


def test_list_processes_excludes_self():
    import os
    procs = list_processes()
    assert procs and os.getpid() not in [pid for _, pid in procs]


def test_main_window_shortcut_stops_both_panels(qapp, tmp_path):
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {})
    with patch.object(w.automation_panel, "emergency_stop") as a, patch.object(w.frame_panel, "emergency_stop") as f:
        w.stop_shortcut.activated.emit()
    a.assert_called_once()
    f.assert_called_once()
    with patch.object(w.frame_panel, "shutdown") as shutdown:
        w.close()
    shutdown.assert_called_once()

"""Readings over a test (temperature, power...) kept in the library beside the result, shown as a chart (owner,
2026-10-25)."""

import json
from pathlib import Path

import pytest

from app.library import readings, store

CSV = ("seconds,cpu_load [%],gpu0_temperature [C],gpu0_sm_clock [MHz],empty [W]\n"
       "0.0,10,50,1800,\n1.0,,{peak},{clock},\n2.0,30,55,1700,\n")


@pytest.fixture(scope="module")
def qapp():
    import sys
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def pkg(session_id=7, created="2026-10-25 10:00:00", test="bench"):
    return {"package_id": f"u1_s{session_id}_{test}", "pc": {"unit_id": "u1", "os": {"name": "Windows 11"}},
            "session": {"id": session_id, "created_at": created},
            "test": {"id": test, "name": "Bench", "unit": "points", "group": "cpu"},
            "attribution": {"part": "cpu", "part_key": "cpu-a", "part_name": "CPU A", "context": {}},
            "result": {"mean": 100, "runs": 2, "accuracy": "average of 2", "noisy": False}}


def run_folder(root, name, peak=60, clock=1600):
    d = root / name
    d.mkdir(parents=True)
    (d / "telemetry.csv").write_text(CSV.format(peak=peak, clock=clock), encoding="utf-8")


def runs_root(tmp_path):
    root = tmp_path / "runs"
    s = root / "session_7_20261025_100500"
    run_folder(s, "bench_w0", peak=99)                    # a warm-up: never kept
    run_folder(s, "bench_r1", peak=60, clock=1600)
    run_folder(s, "bench_r2", peak=90, clock=1750)        # tried again: the last try counts
    run_folder(s, "bench_r2_retry1", peak=70, clock=1750)
    run_folder(root / "session_7_20260101_090000", "bench_r1", peak=200)    # another database's session 7
    return root


def test_csv_runs_and_session_match(tmp_path):
    root = runs_root(tmp_path)
    folder = readings.session_folder(root, 7, "2026-10-25 10:00:00")
    assert folder.name == "session_7_20261025_100500"
    assert readings.session_folder(root, 7, "2026-05-01 10:00:00") is None    # no folder near: nothing guessed
    runs = readings.from_runs(folder, "bench")
    assert [r["run"] for r in runs] == [1, 2]
    one = runs[0]
    assert one["seconds"] == [0.0, 1.0, 2.0]
    assert one["series"]["cpu_load"] == {"unit": "%", "values": [10.0, None, 30.0]}    # missing stays missing
    assert "empty" not in one["series"]
    assert max(runs[1]["series"]["gpu0_temperature"]["values"]) == 70          # the retry, not the first try


def test_worst_run_label_and_report():
    data = {"runs": [{"run": 1, "seconds": [0, 1], "series": {"t": {"unit": "C", "values": [50, 60]},
                                                             "gpu0_sm_clock": {"unit": "MHz", "values": [1800, 1700]}}},
                     {"run": 2, "seconds": [0, 1], "series": {"t": {"unit": "C", "values": [55, 80]},
                                                             "gpu0_sm_clock": {"unit": "MHz", "values": [1900, 1900]}}}]}
    run, why = readings.worst(data, "t")
    assert run["run"] == 2 and why == "run 2 of 2: the highest peak"
    run, why = readings.worst(data, "gpu0_sm_clock")
    assert run["run"] == 1 and why == "run 1 of 2: the lowest average"
    assert ("gpu0_sm_clock", "GPU clock", "MHz") in readings.choices(data)
    page = readings.report_html("<PC> - Bench", why, "GPU clock", "MHz", run, "gpu0_sm_clock")
    assert page.startswith("<!doctype html>") and "&lt;PC&gt;" in page and "<svg" in page
    assert "1,750.0 MHz" in page and "http" not in page.replace('xmlns="http://www.w3.org/2000/svg"', "")


def test_outbox_then_library_and_packages_untouched(tmp_path):
    root, outbox, lib = runs_root(tmp_path), tmp_path / "outbox", tmp_path / "lib"
    p = pkg()
    assert readings.to_outbox(outbox, [p], root) == 1
    assert readings.to_outbox(outbox, [p], root) == 0                 # written once
    assert store.waiting(outbox) == []                                # not taken for a package
    assert readings.load([None, outbox], p)["runs"][0]["run"] == 1    # not sent yet: read from the outbox
    lib.mkdir()
    store.open_library(lib, create=True)
    assert readings.send(outbox, lib) == 1
    assert not list(outbox.glob("readings/*/*/*.json"))
    data = readings.load([lib, outbox], p)
    assert data["package_id"] == p["package_id"] and len(data["runs"]) == 2
    assert readings.load([lib], pkg(test="other")) is None
    assert readings.to_outbox(outbox, [pkg(created="2026-05-01 10:00:00")], root) == 0   # session not matched


def test_screen_button_and_dialog(qapp, tmp_path):
    from app.ui.library_tab import LibraryTab
    from app.library import sync
    p = pkg()
    readings.to_outbox(tmp_path / sync.OUTBOX, [p], runs_root(tmp_path))

    class Index:
        def results(self, include_deleted=False):
            return [p]

        def names(self):
            return {}

        def info(self):
            return {}
    shown = []
    tab = LibraryTab(lambda: tmp_path, save_as=lambda start: str(tmp_path / "r.html"))
    tab.show_dialog = shown.append
    tab._index = lambda: Index()
    tab.refresh()
    cpus = next(tab.tree.topLevelItem(i) for i in range(tab.tree.topLevelItemCount())
                if tab.tree.topLevelItem(i).text(0) == "Processors")
    tab.tree.setCurrentItem(cpus.child(0))
    assert not tab.readings_btn.isEnabled()
    tab.table.setCurrentItem(tab.table.topLevelItem(0))
    assert tab.readings_btn.isEnabled()
    tab.readings_chart()
    dialog, = shown
    items = [dialog.reading.itemText(i) for i in range(dialog.reading.count())]
    assert items[:2] == ["CPU load", "GPU temperature"]
    dialog.reading.setCurrentIndex(1)
    assert "run 2 of 2: the highest peak" in dialog.detail.text() and "highest 70.0" in dialog.detail.text()
    dialog.save_report()
    assert "<svg" in (tmp_path / "r.html").read_text(encoding="utf-8")
    (tmp_path / sync.OUTBOX / readings.relative_path(p)).unlink()
    tab.readings_chart()
    assert len(shown) == 1 and "No readings over the test" in tab.part_title.text()

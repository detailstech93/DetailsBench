"""Results library: packages, outbox, library folder, index, delete and restore (app/library), in tmp folders."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.library import package as pk
from app.library import store, sync
from app.library.index import LibraryIndex

@pytest.fixture(scope="module")
def qapp():
    import sys
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


HW = {"unit_id": "c502abe010156c02",
      "device": {"maker": "Dell Inc.", "model": "XPS 15", "kind": "laptop", "placeholder": False, "bios": "1.2"},
      "cpu": {"name": "Intel(R) Core(TM) Ultra 9 275HX", "cores": 24, "threads": 24},
      "gpus": [{"name": "NVIDIA GeForce RTX 5090 Laptop GPU", "vendor_id": "10de", "device_id": "2c18",
                "vram_mb": 24463, "driver": "617.42", "power_limit_w": 175.0}],
      "ram": {"total_gb": 96.0, "modules": []},
      "drives": [{"model": "FORESEE", "size_gb": 2048, "firmware": "X", "holds_app_data": True}],
      "os": {"name": "Windows 11", "build": "26300"}}


def run(name, kind, score, warm=False, status="completed", unit="points", note=None):
    return {"benchmark_name": name, "benchmark_type": kind, "score": score, "is_warmup": warm, "status": status,
            "is_test": False, "unit": unit, "benchmark_version": "5.2.0", "timestamp": "2026-10-24 12:00", "note": note}


def export(hw=HW, runs=None, is_test=False, summaries=None):
    runs = runs if runs is not None else [run("blender_benchmark", "cpu", 99.0, warm=True),
                                          run("blender_benchmark", "cpu", 100.0), run("blender_benchmark", "cpu", 102.0),
                                          run("stress_cpu", "stress", 1.0), run("cyberpunk2077_builtin_benchmark", "game", 90.0),
                                          run("vray_benchmark", "cpu", None, status="failed")]
    from app.stats import summarize_runs
    names = {r["benchmark_name"] for r in runs}
    return {"app": {"version": "0.1.3"}, "session": {"id": 7, "name": "Automated", "created_at": "2026-10-24T12:00:00", "is_test": is_test},
            "manifest": {"environment": {"hardware": hw, "is_wsl": {"value": False}},
                         "benchmarks": [{"name": "blender_benchmark", "version": "5.2.0"}], "run_settings": {}},
            "runs": runs,
            "summaries": summaries or {n: summarize_runs([r for r in runs if r["benchmark_name"] == n]) for n in names},
            "metric_summaries": {"blender_benchmark": {"monster": {"mean": 50.0}}}}


def test_one_package_per_finished_test():
    pkgs, left = pk.build_packages(export(), now="2026-10-24T12:00:00")
    assert [p["test"]["id"] for p in pkgs] == ["blender_benchmark", "stress_cpu"]
    assert pkgs[1]["attribution"]["kind"] == "stress" and not pkgs[1]["attribution"]["ranked"]
    p = pkgs[0]
    assert p["package_id"] == "c502abe010156c02_s7_blender_benchmark"
    assert p["result"]["scores"] == [100.0, 102.0] and p["result"]["mean"] == 101.0          # warm-up left out
    assert p["result"]["runs"] == 2 and p["result"]["accuracy"].startswith("average of 2 (+/-1.4%)")
    assert p["attribution"]["part"] == "cpu" and p["result"]["metrics"] == {"monster": {"mean": 50.0}}
    assert pk.check(p) is None
    why = dict(left)
    assert "Games section" in why["cyberpunk2077_builtin_benchmark"] and "stress_cpu" not in why
    assert why["vray_benchmark"] == "no measured run"            # a failure is never a score


def test_no_package_without_the_parts_or_for_a_test_session():
    assert pk.build_packages(export(hw={}))[0] == []
    assert "before 0.1.3" in pk.build_packages(export(hw={}))[1][0][1]
    assert pk.build_packages(export(is_test=True))[0] == []


def test_accuracy_labels():
    assert pk.accuracy(1, None) == "1 run (low confidence)" and pk.accuracy(5, 0.42) == "average of 5 (+/-0.4%)"


def test_a_changed_or_newer_package_is_refused():
    p = pk.build_packages(export())[0][0]
    assert "changed" in pk.check(dict(p, result=dict(p["result"], mean=1)))
    added = dict(p, format_version=pk.FORMAT_VERSION + 1, a_new_field={"x": 1})     # a newer app added fields
    added["hash"] = pk.content_hash(added)
    assert pk.check(added) is None                                                  # still read here
    changed = dict(p, readable_by=pk.READABLE_BY + 1)                               # a field changed its meaning
    changed["hash"] = pk.content_hash(changed)
    assert "newer app" in pk.check(changed)


def test_library_folder(tmp_path):
    with pytest.raises(store.LibraryError, match="not a results library"):
        store.open_library(tmp_path)
    assert store.open_library(tmp_path, create=True) == tmp_path
    assert json.loads((tmp_path / "library.json").read_text())["format_version"] == store.LIBRARY_VERSION
    with pytest.raises(store.LibraryError, match="cannot be reached"):
        store.open_library(tmp_path / "nas-offline")
    (tmp_path / "library.json").write_text(json.dumps({"format": store.LIBRARY_FORMAT, "format_version": 99}))
    with pytest.raises(store.LibraryError, match="newer"):
        store.open_library(tmp_path)


def test_outbox_then_library_then_index(tmp_path):
    outbox, lib = tmp_path / "outbox", tmp_path / "lib"
    lib.mkdir()
    pkgs = pk.build_packages(export())[0][:1]       # the Blender package
    assert store.to_outbox(outbox, pkgs) == 1 and store.to_outbox(outbox, pkgs) == 0      # written once
    with pytest.raises(store.LibraryError):                    # no library there yet: everything stays waiting
        store.send(outbox, lib)
    assert len(store.waiting(outbox)) == 1
    store.open_library(lib, create=True)
    assert store.send(outbox, lib) == (1, []) and store.waiting(outbox) == []
    path = lib / "packages" / "c502abe010156c02" / "s7" / "blender_benchmark.json"
    assert pk.check(json.loads(path.read_text(encoding="utf-8"))) is None
    assert not list(lib.rglob("*.part"))
    index = LibraryIndex(tmp_path / "index.db")
    assert index.refresh(lib)["added"] == 1 and index.refresh(lib)["added"] == 0         # only new files
    assert [r["package_id"] for r in index.results(part="cpu")] == [pkgs[0]["package_id"]]


def test_a_test_already_in_the_library_stays_as_it_is_there(tmp_path):
    (tmp_path / "lib").mkdir()
    outbox, lib = tmp_path / "outbox", store.open_library(tmp_path / "lib", create=True)
    pkgs = pk.build_packages(export())[0][:1]       # the Blender package
    store.to_outbox(outbox, pkgs)
    same = dict(pkgs[0], created_at="earlier")      # the same test of the same session, packaged before
    same["hash"] = pk.content_hash(same)
    dest = lib / store.relative_path(same)
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(same), encoding="utf-8")
    assert store.send(outbox, lib) == (0, []) and store.waiting(outbox) == []
    assert json.loads(dest.read_text(encoding="utf-8"))["created_at"] == "earlier"


def test_a_different_file_of_the_same_name_is_not_overwritten(tmp_path):
    (tmp_path / "lib").mkdir()
    outbox, lib = tmp_path / "outbox", store.open_library(tmp_path / "lib", create=True)
    pkgs = pk.build_packages(export())[0][:1]
    store.to_outbox(outbox, pkgs)
    other = dict(pkgs[0], package_id="someone-else", created_at="another")
    other["hash"] = pk.content_hash(other)
    dest = lib / store.relative_path(other)
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(other), encoding="utf-8")
    sent, problems = store.send(outbox, lib)
    assert sent == 0 and "another file" in problems[0] and len(store.waiting(outbox)) == 1
    assert json.loads(dest.read_text(encoding="utf-8"))["created_at"] == "another"


def test_delete_and_restore_never_erase(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    store.open_library(lib, create=True)
    pkgs = pk.build_packages(export())[0][:1]       # the Blender package
    store.to_outbox(tmp_path / "o", pkgs)
    store.send(tmp_path / "o", lib)
    index = LibraryIndex(tmp_path / "i.db")
    pid = pkgs[0]["package_id"]
    store.write_event(lib, pid, "delete", "c502", "retest after a driver update", now=datetime(2026, 10, 24, 12))
    index.refresh(lib)
    assert index.results() == [] and index.results(include_deleted=True)[0]["deleted"]["reason"].startswith("retest")
    store.write_event(lib, pid, "restore", "c502", now=datetime(2026, 10, 24, 13))
    index.refresh(lib)
    assert [r["package_id"] for r in index.results()] == [pid]
    assert len(list((lib / "packages").rglob("*.json"))) == 1                 # the package itself never went
    assert index.rebuild(lib)["added"] == 1 and index.results()[0]["deleted"] is None


def test_add_session_waits_without_a_folder_then_goes(tmp_path, monkeypatch):
    import app.export
    monkeypatch.setattr(app.export, "build_session_export", lambda db, sid: export())
    data = tmp_path / "data"
    data.mkdir()
    text = sync.add_session(None, 7, data)
    assert "no results library folder" in text and len(store.waiting(data / sync.OUTBOX)) == 2   # Blender, stress
    from app.paths import save_setting
    lib = tmp_path / "nas"
    lib.mkdir()
    store.open_library(lib, create=True)
    save_setting(data, sync.FOLDER_KEY, str(lib))
    assert sync.send_waiting(data) == "2 result(s) added to the library."
    assert len(LibraryIndex(data / sync.INDEX).results()) == 2


def test_ai_run_read_from_the_session_folder(tmp_path):
    run_dir = tmp_path / "session_7_20261024" / "llama_benchy_r1"
    run_dir.mkdir(parents=True)
    (run_dir / "ai_run.json").write_text(json.dumps({"backend": "cuda12"}))
    read = sync.ai_run_reader(tmp_path, 7)
    assert read("llama_benchy") == {"backend": "cuda12"} and read("mlperf_client") is None
    assert sync.ai_run_reader(None, 7)("llama_benchy") is None


def test_library_tab(qapp, tmp_path):
    from app.ui.library_tab import LibraryTab
    lib = tmp_path / "nas"
    lib.mkdir()
    tab = LibraryTab(lambda: tmp_path, pick=lambda start: str(lib))
    tab.refresh()
    assert "none chosen" in tab.folder_label.text() and tab.auto.isChecked()
    tab.choose_folder()
    assert str(lib) in tab.folder_label.text() and (lib / "library.json").is_file()
    tab.auto.setChecked(False)
    assert sync.auto_setting(tmp_path) is False


def _library(tmp_path):
    """A data folder with a chosen library holding the Blender and CPU stress packages of session 7."""
    from app.paths import save_setting
    data, lib = tmp_path / "data", tmp_path / "nas"
    data.mkdir()
    lib.mkdir()
    store.open_library(lib, create=True)
    save_setting(data, sync.FOLDER_KEY, str(lib))
    store.to_outbox(data / sync.OUTBOX, pk.build_packages(export())[0])
    sync.send_waiting(data)
    return data, lib


def test_names_newest_wins_and_empty_takes_it_back(tmp_path):
    data, lib = _library(tmp_path)
    index = LibraryIndex(data / sync.INDEX)
    store.write_name(lib, "system:x", "Living room mini PC", "u1", now=datetime(2026, 10, 24, 12))
    store.write_name(lib, "system:x", "Office mini PC", "u2", now=datetime(2026, 10, 24, 13))
    index.refresh(lib)
    assert index.names() == {"system:x": "Office mini PC"}
    store.write_name(lib, "system:x", "  ", "u1", now=datetime(2026, 10, 24, 14))
    index.refresh(lib)
    assert index.names() == {}


def test_view_parts_and_rows(tmp_path):
    from app.library import view
    data, _ = _library(tmp_path)
    results = LibraryIndex(data / sync.INDEX).results()
    grouped = view.parts(results, {})
    assert [p["name"] for p in grouped["cpu"]] == ["Intel Core Ultra 9 275HX"]
    assert grouped["cpu"][0]["results"] == 2 and grouped["gpu"] == [] and grouped["system"] == []
    rows = view.rows(results, grouped["cpu"][0]["key"])
    blender = next(r for r in rows if not r["stress"])
    assert blender["result"] == "101 points" and blender["pc"] == "Dell Inc. XPS 15"
    assert "BIOS 1.2" in blender["software"]
    stress = next(r for r in rows if r["stress"])
    assert stress["result"].endswith("no load numbers")       # the made-up run has no telemetry
    named = view.parts(results, {grouped["cpu"][0]["key"]: "My CPU"})
    assert named["cpu"][0]["name"] == "My CPU"


def test_stress_text_reads_the_load_numbers():
    from app.library import view
    pkg = {"attribution": {"part": "system"}, "test": {"settings": {"stress_minutes": 5}},
           "result": {"metrics": {"cpu_stability": {"mean": 98.8}, "tel_cpu_package_power_avg": {"mean": 63.2},
                                  "tel_cpu_package_power_max": {"mean": 149.0},
                                  "tel_cpu_temperature_hottest_max": {"mean": 102.0},
                                  "gpu_stability": {"mean": 99.5}, "gpu_errors": {"mean": 2},
                                  "tel_gpu0_temperature_max": {"mean": 80.0}}}}
    assert view.stress_text(pkg) == ("5 min: CPU 98.8% stable, 63 W avg / 149 W max, 102 C max; "
                                     "GPU 99.5% stable, 2 errors, 80 C max")


def test_add_earlier_only_sessions_with_the_parts(tmp_path, monkeypatch):
    import app.export
    monkeypatch.setattr(app.export, "build_session_export", lambda db, sid: export())

    class Db:
        def get_sessions(self):
            return [{"id": 6}, {"id": 7}]

        def get_session_manifest(self, sid):
            return {"environment": {"hardware": HW}} if sid == 7 else {"environment": {}}
    data = tmp_path / "data"
    data.mkdir()
    text = sync.add_earlier(Db(), data)
    assert text.startswith("1 session(s) read.") and len(store.waiting(data / sync.OUTBOX)) == 2

    class Old(Db):
        def get_session_manifest(self, sid):
            return {}
    assert "nothing to add" in sync.add_earlier(Old(), tmp_path)


def _line(tab, pick):
    from PySide6.QtCore import Qt
    return next(tab.table.topLevelItem(i) for i in range(tab.table.topLevelItemCount())
                if tab.table.topLevelItem(i).data(0, Qt.UserRole) and pick(tab.table.topLevelItem(i).data(0, Qt.UserRole)))


def test_library_screen_delete_restore_and_name(qapp, tmp_path):
    from types import SimpleNamespace
    from app.ui.library_tab import LibraryTab
    data, lib = _library(tmp_path)
    answers = []
    window = SimpleNamespace(environment_profile=SimpleNamespace(hardware={"unit_id": "c502abe010156c02"}))
    tab = LibraryTab(lambda: data, window=window, ask_text=lambda *a: answers.pop(0))
    tab.refresh()
    cpu = tab.tree.topLevelItem(1)
    assert cpu.text(0) == "Processors" and cpu.text(1) == "2" and cpu.childCount() == 1
    tab.tree.setCurrentItem(cpu.child(0))
    assert tab.table.topLevelItemCount() == 2 and not tab.name_btn.isEnabled() and not tab.delete_btn.isEnabled()
    tab.table.setCurrentItem(_line(tab, lambda r: not r["stress"]))
    assert tab.delete_btn.isEnabled() and not tab.restore_btn.isEnabled()
    answers.append("retest after a driver update")
    tab.delete()
    assert tab.table.topLevelItemCount() == 1 and tab.tree.topLevelItem(1).child(0).text(1) == "1"
    assert "1 deleted result(s) hidden" in tab.part_title.text()
    assert len(list((lib / "events").glob("*.json"))) == 1 and len(list((lib / "packages").rglob("*.json"))) == 2
    tab.show_deleted.setChecked(True)
    assert tab.table.topLevelItemCount() == 2 and "(+1 deleted)" in tab.tree.topLevelItem(1).child(0).text(1)
    gone = _line(tab, lambda r: r["deleted"])
    assert "retest" in gone.text(7)
    tab.table.setCurrentItem(gone)
    tab.restore()
    tab.show_deleted.setChecked(False)
    assert tab.table.topLevelItemCount() == 2
    answers.append(None)                            # cancelled: nothing written
    tab.table.setCurrentItem(tab.table.topLevelItem(0))
    tab.delete()
    assert len(list((lib / "events").glob("*.json"))) == 2


def test_library_screen_names_a_device(qapp, tmp_path):
    from app.ui.library_tab import LibraryTab
    data, lib = _library(tmp_path)
    store.to_outbox(data / sync.OUTBOX, pk.build_packages(export(runs=[run("stress_cpu_gpu", "stress", 1.0)]))[0])
    sync.send_waiting(data)
    tab = LibraryTab(lambda: data, ask_text=lambda *a: "Test bench")
    tab.refresh()
    devices = tab.tree.topLevelItem(0)
    assert devices.text(0) == "Devices" and devices.child(0).text(0) == "Dell Inc. XPS 15"
    tab.tree.setCurrentItem(devices.child(0))
    assert tab.name_btn.isEnabled()
    tab.name_part()
    assert tab.tree.topLevelItem(0).child(0).text(0) == "Test bench" and tab.part_title.text() == "Test bench"


def test_a_run_cut_short_is_added_at_the_next_app_start(tmp_path, monkeypatch):
    """Owner, 2026-10-24: nothing lost when a run is cut short; packages built only from the whole run
    (a test packaged mid-run kept 2 of llama-benchy's 3 runs)."""
    from types import SimpleNamespace
    import threading
    import app.export
    monkeypatch.setattr(app.export, "build_session_export", lambda db, sid: export())
    monkeypatch.setattr(threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))
    data, lib = _library(tmp_path)
    for f in (lib / "packages").rglob("*.json"):
        f.unlink()                                  # scratch library of this test only
    LibraryIndex(data / sync.INDEX).rebuild(lib)
    sync.mark_running(data, 7)
    sync.mark_running(data, 7)
    assert sync.cut_short(data) == [7] and store.waiting(data / sync.OUTBOX) == []      # nothing packaged yet
    window = SimpleNamespace(frame_panel=SimpleNamespace(data_dir=data), db=None,
                             automation_panel=SimpleNamespace(output_root=None))
    sync.send_at_start(window)                      # the app opens again
    assert sync.cut_short(data) == [] and len(LibraryIndex(data / sync.INDEX).results()) == 2
    sync.after_run(SimpleNamespace(frame_panel=window.frame_panel, automation_panel=window.automation_panel,
                                   db=None), 7)
    assert sync.cut_short(data) == []                # a run that ends removes its own marker too
    from app.paths import save_setting
    save_setting(data, sync.AUTO_KEY, False)        # the owner adds by hand: no marker
    sync.mark_running(data, 8)
    assert sync.cut_short(data) == []


def test_send_at_start_only_with_a_folder_and_something_waiting(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import threading
    data, lib = _library(tmp_path)
    calls, started = [], []
    monkeypatch.setattr(sync, "send_waiting", lambda d: calls.append(d) or "")
    monkeypatch.setattr(threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: started.append(target())))
    window = SimpleNamespace(frame_panel=SimpleNamespace(data_dir=data), db=None,
                             automation_panel=SimpleNamespace(output_root=None))
    sync.send_at_start(window)                       # nothing waits: no thread
    assert started == []
    store.to_outbox(data / sync.OUTBOX, pk.build_packages(export(runs=[run("stress_gpu", "stress", 1.0)]))[0])
    sync.send_at_start(window)
    assert calls == [data] and len(started) == 1


def test_tested_on_shows_the_name_the_owner_gave_the_device(tmp_path):
    from app.library import view
    data, lib = _library(tmp_path)
    results = LibraryIndex(data / sync.INDEX).results()
    key = results[0]["pc"]["system_key"]
    assert key == "system:dell-inc-xps-15" or key.startswith("system:")
    cpu = results[0]["attribution"]["part_key"]
    assert {r["pc"] for r in view.rows(results, cpu, {key: "Olares -1"})} == {"Olares -1"}
    old = [dict(p, pc={k: v for k, v in p["pc"].items() if k != "system_key"}) for p in results]   # before 0.1.3's
    device = dict(old[0], attribution=dict(old[0]["attribution"], part="system", part_key=key))   # system_key
    assert {r["pc"] for r in view.rows(old + [device], cpu, {key: "Olares -1"})} == {"Olares -1"}
    assert {r["pc"] for r in view.rows(results, cpu, {})} == {"Dell Inc. XPS 15"}


def test_a_package_with_more_runs_replaces_the_one_that_missed_some(tmp_path):
    (tmp_path / "lib").mkdir()
    outbox, lib = tmp_path / "outbox", store.open_library(tmp_path / "lib", create=True)
    pkgs = pk.build_packages(export())[0][:1]               # Blender: 2 measured runs
    short = pk.build_packages(export(runs=[run("blender_benchmark", "cpu", 100.0)]))[0][0]
    store.to_outbox(outbox, [short])
    store.send(outbox, lib)
    index = LibraryIndex(tmp_path / "i.db")
    index.refresh(lib)
    assert index.results()[0]["result"]["runs"] == 1
    store.to_outbox(outbox, pkgs)
    assert store.send(outbox, lib) == (1, [])
    index.refresh(lib)
    assert [r["result"]["runs"] for r in index.results()] == [2]
    store.to_outbox(outbox, [short])                        # fewer runs never replace more
    assert store.send(outbox, lib) == (0, []) and store.waiting(outbox) == []


def test_a_test_run_in_several_sessions_is_one_line_that_opens_newest_first(qapp, tmp_path):
    """Owner, 2026-10-24: the same test in one line; open it to see every result by date."""
    from PySide6.QtCore import Qt
    from app.ui.library_tab import LibraryTab
    data, lib = _library(tmp_path)
    for sid, day in ((8, "2026-10-25 10:00"), (9, "2026-10-26 10:00")):
        e = export()
        e["session"] = dict(e["session"], id=sid, created_at=day)
        store.to_outbox(data / sync.OUTBOX, pk.build_packages(e)[0][:1])
    sync.send_waiting(data)
    tab = LibraryTab(lambda: data)
    tab.refresh()
    tab.tree.setCurrentItem(tab.tree.topLevelItem(1).child(0))
    lines = [tab.table.topLevelItem(i) for i in range(tab.table.topLevelItemCount())]
    blender = next(x for x in lines if x.childCount())
    assert len(lines) == 2 and blender.childCount() == 3 and "(3 results)" in blender.text(0)
    assert [blender.child(i).text(6) for i in range(3)] == ["2026-10-26 10:00", "2026-10-25 10:00", "2026-10-24 12:00"]
    assert blender.data(0, Qt.UserRole) is None and blender.child(0).data(0, Qt.UserRole)["result"] == "101 points"
    tab.table.setCurrentItem(blender)
    assert not tab.delete_btn.isEnabled()               # the test's line is not one result
    tab.table.setCurrentItem(blender.child(0))
    assert tab.delete_btn.isEnabled()

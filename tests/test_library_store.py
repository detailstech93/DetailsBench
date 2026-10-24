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
    return {"app": {"version": "0.1.3"}, "session": {"id": 7, "name": "Automated", "created_at": "x", "is_test": is_test},
            "manifest": {"environment": {"hardware": hw, "is_wsl": {"value": False}},
                         "benchmarks": [{"name": "blender_benchmark", "version": "5.2.0"}], "run_settings": {}},
            "runs": runs,
            "summaries": summaries or {n: summarize_runs([r for r in runs if r["benchmark_name"] == n]) for n in names},
            "metric_summaries": {"blender_benchmark": {"monster": {"mean": 50.0}}}}


def test_one_package_per_finished_test():
    pkgs, left = pk.build_packages(export(), now="2026-10-24T12:00:00")
    assert [p["test"]["id"] for p in pkgs] == ["blender_benchmark"]
    p = pkgs[0]
    assert p["package_id"] == "c502abe010156c02_s7_blender_benchmark"
    assert p["result"]["scores"] == [100.0, 102.0] and p["result"]["mean"] == 101.0          # warm-up left out
    assert p["result"]["runs"] == 2 and p["result"]["accuracy"].startswith("average of 2 (+/-1.4%)")
    assert p["attribution"]["part"] == "cpu" and p["result"]["metrics"] == {"monster": {"mean": 50.0}}
    assert pk.check(p) is None
    why = dict(left)
    assert "Games section" in why["cyberpunk2077_builtin_benchmark"] and "stability" in why["stress_cpu"]
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
    newer = dict(p, format_version=pk.FORMAT_VERSION + 1)
    newer["hash"] = pk.content_hash(newer)
    assert "newer app" in pk.check(newer)


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
    pkgs = pk.build_packages(export())[0]
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


def test_a_different_file_of_the_same_name_is_not_overwritten(tmp_path):
    (tmp_path / "lib").mkdir()
    outbox, lib = tmp_path / "outbox", store.open_library(tmp_path / "lib", create=True)
    pkgs = pk.build_packages(export())[0]
    store.to_outbox(outbox, pkgs)
    other = dict(pkgs[0], created_at="another")
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
    pkgs = pk.build_packages(export())[0]
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
    assert "no results library folder" in text and len(store.waiting(data / sync.OUTBOX)) == 1
    from app.paths import save_setting
    lib = tmp_path / "nas"
    lib.mkdir()
    store.open_library(lib, create=True)
    save_setting(data, sync.FOLDER_KEY, str(lib))
    assert sync.send_waiting(data) == "1 result(s) added to the library."
    assert len(LibraryIndex(data / sync.INDEX).results()) == 1


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

"""Phase 5: parts of the results library side by side, and the comparison as an HTML report."""

import json
from pathlib import Path

import pytest

from app.library import compare


@pytest.fixture(scope="module")
def qapp():
    import sys
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def pkg(part, test, mean, os="Windows 11", driver=None, date="2026-10-20T10:00:00", unit="points", runs=3,
        kind=None, version="6.4", fingerprint=None, deleted=None, name=None):
    return {"package_id": f"{part}_{test}_{date}_{driver}", "created_at": date, "deleted": deleted,
            "pc": {"unit_id": "u1", "os": {"name": os}},
            "session": {"id": 1, "created_at": date},
            "test": {"id": test, "name": name or test.title(), "unit": unit, "group": "cpu", "version": version,
                     "fingerprint": fingerprint},
            "attribution": {"part": "gpu", "part_key": part, "part_name": part.upper(), "kind": kind,
                            "context": {"driver": driver} if driver else {}},
            "result": {"mean": mean, "runs": runs, "accuracy": f"average of {runs}", "noisy": False}}


def test_newest_driver_difference_and_best():
    results = [pkg("a", "bench", 100, driver="581.8", date="2026-10-22T10:00:00"),
               pkg("a", "bench", 120, driver="617.42", date="2026-10-01T10:00:00"),     # newer driver wins
               pkg("b", "bench", 150, driver="617.42"),
               pkg("b", "bench", 999, driver="617.42", deleted={"reason": "retest"})]   # deleted: left out
    out = compare.compare(results, ["a", "b"])
    assert [p["name"] for p in out["parts"]] == ["A", "B"]
    row, = out["rows"]
    assert [c["mean"] for c in row["cells"]] == [120, 150]
    assert row["cells"][0]["diff"] is None and row["cells"][1]["diff"] == pytest.approx(25.0)
    assert row["best"] == 1 and row["os"] == "Windows" and not row["notes"]


def test_time_is_lower_is_better_and_windows_apart_from_linux():
    results = [pkg("a", "render", 50, unit="s"), pkg("b", "render", 40, unit="s"),
               pkg("a", "render", 45, unit="s", os="Fedora Linux 44")]
    rows = compare.compare(results, ["a", "b"])["rows"]
    assert [(r["os"], r["all"]) for r in rows] == [("Windows", True), ("Linux", False)]   # both parts first
    assert rows[0]["lower_is_better"] and rows[0]["best"] == 1
    assert rows[1]["cells"][1] is None and rows[1]["best"] is None


def test_what_does_not_compare_cleanly_is_said():
    results = [pkg("a", "bench", 100, runs=1, version="6.3"), pkg("b", "bench", 110, version="6.4"),
               pkg("a", "notes", None, kind="info"), pkg("b", "load", 99.0, kind="stress")]
    row, = compare.compare(results, ["a", "b"])["rows"]          # text and load checks are not lined up
    assert "low confidence (one run, or imported): A" in row["notes"]
    assert "another benchmark version: not the same test" in row["notes"]


def test_imported_names_lose_the_os_and_the_report_stands_alone():
    results = [pkg("a", "gb", 900, name="Geekbench 6 - Linux", os="Linux"),
               pkg("b", "gb", 1000, name="Geekbench 6 - Linux", os="Linux")]
    out = compare.compare(results, ["a", "b"], names={"b": "<Pi> 5"})
    assert out["rows"][0]["test"] == "Geekbench 6"
    page = compare.report_html(out, now="2026-10-24 12:00")
    assert "&lt;Pi&gt; 5" in page and "<Pi>" not in page                    # the owner's names are escaped
    assert "Geekbench 6" in page and "+11.1%" in page and "higher is better" in page
    assert "http" not in page                                               # nothing loaded from the web


def test_compare_screen_and_report(qapp, tmp_path):
    from app.ui.library_tab import LibraryTab
    results = [pkg("a", "bench", 100), pkg("b", "bench", 150)]

    class Index:
        def results(self, include_deleted=False):
            return results

        def names(self):
            return {}

        def info(self):
            return {}
    opened = []
    tab = LibraryTab(lambda: tmp_path, save_as=lambda start: str(tmp_path / "c.html"), opener=opened.append)
    tab._index = lambda: Index()
    tab.refresh()
    gpus = next(tab.tree.topLevelItem(i) for i in range(tab.tree.topLevelItemCount())
                if tab.tree.topLevelItem(i).text(0) == "Graphics cards")
    assert not tab.compare_btn.isEnabled()
    gpus.child(0).setSelected(True)
    assert not tab.compare_btn.isEnabled()                                # two or more
    gpus.child(1).setSelected(True)
    assert tab.compare_btn.isEnabled()
    tab.load()                                                            # a reload keeps the choice
    assert tab.compare_btn.isEnabled()
    tab.compare()
    item = tab.compare_table.topLevelItem(0)
    assert item.text(2) == "100 points" and item.text(3) == "150 points (+50.0%)" and item.font(3).bold()
    assert not tab.open_btn.isEnabled()
    tab.save_report()
    assert (tmp_path / "c.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    tab.open_report()
    assert opened == [tmp_path / "c.html"]
    tab.clear_compare()                                                   # owner: empty it when done
    assert tab.compared is None and tab.compare_table.topLevelItemCount() == 0
    assert len(tab._selected_parts()) <= 1 and not tab.compare_btn.isEnabled() and not tab.open_btn.isEnabled()


# ── phase 6: sessions from before the parts record ──

OLD_ENV = {"cpu": {"value": "Intel(R) Core(TM) Ultra 9 275HX"},
           "gpu": {"value": ["Intel(R) Graphics", "NVIDIA GeForce RTX 5090 Laptop GPU"]},
           "os_name": {"value": "Windows 11 Professional"}, "os_build": {"value": "26300.9711"},
           "ram_gb": {"value": 95.4}}


def test_an_old_session_of_this_pc_is_filed_under_its_parts_with_the_driver_unknown():
    from app.library import legacy
    from tests.test_library_store import HW
    hw = legacy.hardware({"environment": OLD_ENV, "session": {"created_at": "2026-10-18T13:45:00"}}, HW)
    assert hw["unit_id"] == HW["unit_id"] and hw["gpus"][0]["driver"] is None
    assert hw["gpus"][0]["power_limit_w"] == 175.0                     # owner: always at 175 W
    assert hw["os"]["name"] == "Windows 11 Professional" and HW["gpus"][0]["driver"] == "617.42"   # a copy


def test_an_old_session_of_another_pc_is_a_build_of_its_parts():
    from app.library import legacy
    from tests.test_library_store import HW
    env = dict(OLD_ENV, cpu={"value": "AMD Ryzen 9 9950X"}, gpu={"value": ["NVIDIA GeForce RTX 4090"]})
    hw = legacy.hardware({"environment": env, "session": {"created_at": "2026-10-18T13:45:00"}}, HW)
    assert hw["unit_id"].startswith("older-") and hw["gpus"] == [{"name": "NVIDIA GeForce RTX 4090",
                                                                   "vendor_id": "10de"}]
    assert legacy.hardware({"environment": {}}, HW) is None             # not even the processor: left out


def test_add_earlier_takes_old_sessions_marked_driver_unknown(tmp_path, monkeypatch):
    import app.export
    from app.library import legacy, package as pk, store, sync
    from tests.test_library_store import HW, export, run
    gpu_runs = [run("vkmark", "gpu", 900.0), run("vkmark", "gpu", 910.0)]
    monkeypatch.setattr(app.export, "build_session_export", lambda db, sid: export(hw=None, runs=gpu_runs))

    class Db:
        def get_sessions(self):
            return [{"id": 7}]

        def get_session_manifest(self, sid):
            return {"environment": OLD_ENV, "session": {"created_at": "2026-10-18T13:45:00"}}
    data = tmp_path / "data"
    data.mkdir()
    text = sync.add_earlier(Db(), data, this_pc=HW)
    assert "1 of them from before the parts record (driver unknown)" in text
    path, = store.waiting(data / sync.OUTBOX)
    made = json.loads(Path(path).read_text(encoding="utf-8"))
    assert pk.check(made) is None and made["old_session"]                # the hash is made again
    a = made["attribution"]
    assert a["variant"].endswith("@ 175 W") and a["context"]["driver"] is None
    assert legacy.NOTE in a["guessed"]


def test_compare_takes_an_old_result_only_where_nothing_newer_is_there():
    old = dict(pkg("a", "bench", 90, date="2026-10-18T10:00:00"), old_session=True)
    new = pkg("a", "bench", 100, driver="617.42", date="2026-10-01T10:00:00")
    only_old = dict(pkg("a", "other", 50), old_session=True)
    rows = compare.compare([old, new, only_old, pkg("b", "bench", 120), pkg("b", "other", 60)], ["a", "b"])["rows"]
    bench = next(r for r in rows if r["test"] == "Bench")
    other = next(r for r in rows if r["test"] == "Other")
    assert bench["cells"][0]["mean"] == 100 and not any("driver unknown" in n for n in bench["notes"])
    assert "driver unknown (older session): A" in other["notes"]


def test_windows_against_linux_on_one_device():
    results = [pkg("a", "gb", 1000), pkg("a", "gb", 1100, os="Ubuntu 24.04"), pkg("a", "only_win", 5)]
    out = compare.os_compare(results, "a")
    assert [p["name"] for p in out["parts"]] == ["A (Windows)", "A (Linux)"]
    gb = next(r for r in out["rows"] if r["test"] == "Gb")
    assert gb["os"] == "Windows vs Linux" and gb["cells"][1]["diff"] == pytest.approx(10.0) and gb["best"] == 1
    assert "The same part on Windows and on Linux." in compare.report_html(out)


# ── one part across drivers, ranking, filters (owner, 2026-10-25) ──

def test_one_part_across_its_drivers_oldest_first():
    results = [pkg("a", "bench", 100, driver="581.8"), pkg("a", "bench", 90, driver="617.42"),
               dict(pkg("a", "bench", 95, date="2026-10-01T10:00:00"), old_session=True)]
    out = compare.drivers_compare(results, "a")
    assert [p["name"] for p in out["parts"]] == ["driver unknown", "Driver 581.8", "Driver 617.42"]
    row, = out["rows"]
    assert row["cells"][2]["diff"] == pytest.approx((90 - 95) / 95 * 100) and row["best"] == 1   # a drop: minus
    assert out["title"] == "A: driver unknown vs Driver 581.8 vs Driver 617.42"
    assert "oldest first" in compare.report_html(out)


def test_ranking_best_first_and_set_aside_left_out():
    wsl = pkg("d", "render", 1, unit="s")
    wsl["attribution"]["flags"] = ["WSL"]
    results = [pkg("a", "render", 50, unit="s"), pkg("b", "render", 40, unit="s"), pkg("c", "render", 60, unit="s"),
               pkg("a", "render", 30, unit="s", os="Ubuntu 24.04"), wsl]
    keys = dict(compare.tests(results))
    assert keys[("render", "Windows", "s")] == "Render (Windows)"
    out = compare.ranking(results, ("render", "Windows", "s"))
    assert [p["name"] for p in out["parts"]] == ["B", "A", "C"]                 # time: less is better
    assert out["set_aside"] == 1 and out["rows"][0]["cells"][1]["diff"] == pytest.approx(25.0)
    assert out["pcs"] == ["u1", "u1", "u1"]


def test_filters_by_os_and_kind():
    laptop = pkg("a", "x", 1)
    laptop["pc"]["device"] = {"kind": "laptop"}
    board = pkg("b", "x", 2, os="Debian 12")
    results = [laptop, board]
    assert compare.filtered(results, "Linux") == [board]
    assert compare.filtered(results, kind="laptop") == [laptop]
    assert compare.filtered(results, kind="") == [board]                     # kind not recorded
    assert compare.filtered(results) == results


def test_screen_drivers_ranking_and_filters(qapp, tmp_path):
    from app.ui.library_tab import LibraryTab
    laptop = pkg("a", "bench", 100, driver="581.8")
    laptop["pc"]["device"] = {"kind": "laptop"}
    results = [laptop, pkg("a", "bench", 120, driver="617.42"), pkg("b", "bench", 150, os="Fedora Linux 44")]

    class Index:
        def results(self, include_deleted=False):
            return results

        def names(self):
            return {}

        def info(self):
            return {}
    tab = LibraryTab(lambda: tmp_path, save_as=lambda start: "", opener=lambda p: None)
    tab._index = lambda: Index()
    tab.refresh()
    assert [tab.kind_filter.itemText(i) for i in range(tab.kind_filter.count())] == [
        "All devices", "Laptops", "Kind not recorded"]
    gpus = next(tab.tree.topLevelItem(i) for i in range(tab.tree.topLevelItemCount())
                if tab.tree.topLevelItem(i).text(0) == "Graphics cards")
    gpus.child(0).setSelected(True)
    assert tab.drivers_btn.isEnabled()
    tab.compare_drivers()
    assert tab.compare_table.headerItem().text(2) == "Driver 581.8"
    assert tab.compare_table.topLevelItem(0).text(3) == "120 points (+20.0%)"
    tab.rank_test.setCurrentIndex(tab.rank_test.findText("Bench (Windows)"))
    tab.rank()
    first = tab.compare_table.topLevelItem(0)
    assert first.text(0) == "1" and first.text(1) == "A" and first.text(3) == "best"
    tab.os_filter.setCurrentIndex(tab.os_filter.findData("Linux"))           # only Linux: part A is gone
    assert gpus is not None and [tab.rank_test.itemText(i) for i in range(tab.rank_test.count())] == ["Bench (Linux)"]
    tab.kind_filter.setCurrentIndex(tab.kind_filter.findData("laptop"))
    assert tab.rank_test.count() == 0 and not tab.rank_btn.isEnabled()

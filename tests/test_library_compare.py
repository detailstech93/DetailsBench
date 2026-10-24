"""Phase 5: parts of the results library side by side, and the comparison as an HTML report."""

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

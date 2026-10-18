"""This PC and Tests screens, and running tests from them (REAL child process, REAL temporary database)."""

import sys
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.catalog import ASSISTED, AUTOMATIC, INSTALLED, MISSING, TestEntry
from app.database import Database
from app.ui.main_window import MainWindow
from app.ui.run_tests import RunChoice, plan_profile, split_entries, start_tests
from app.ui.tests_page import TestsPage
from app.ui.this_pc_page import ThisPcPage, spec_cards, specs_text
from tests.test_automation_ui import workload

PROFILE = {"cpu": {"value": "Test CPU 9000", "status": "detected", "source": "wmi"},
           "ram_gb": {"value": 64, "status": "detected"},
           "gpu": {"value": ["GPU A", "GPU B"], "status": "unverified"},
           "os_name": {"value": "Windows 11", "status": "detected"},
           "native_arch": {"value": "arm64", "status": "detected"},
           "process_arch": {"value": "x64", "status": "detected"},
           "is_virtual_machine": {"value": False, "status": "detected"},
           "vulkan": {"value": None, "status": "unavailable"}}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def entry(id, state=INSTALLED, mode=AUTOMATIC, group="cpu", compatible=True, url=None, source="workload", ref=None):
    return TestEntry(id=id, name=id, group=group, mode=mode, state=state, state_reason="", compatible=compatible,
                     compatibility_reason="" if compatible else "runs on linux only", verified=True,
                     official_url=url, source=source, ref=ref)


def rows(cards):
    return {(card, name): (text, status) for card, items in cards for name, text, status in items}


def test_spec_cards_from_the_profile_and_before_it_is_ready():
    r = rows(spec_cards({"core_count": 8, "thread_count": 16, "cpu_brand": "quick name"}, PROFILE))
    assert r[("Processor", "CPU")] == ("Test CPU 9000", "detected")
    assert r[("Processor", "Cores / threads")] == ("8 / 16", "detected")
    assert r[("Graphics", "GPU")] == ("GPU A, GPU B", "unverified")
    assert r[("Graphics", "Vulkan")] == (None, "unavailable")
    assert r[("Operating system", "Virtual machine")] == ("no", "detected")
    assert r[("Platform", "This app runs as")] == ("x64 (emulated)", "detected")
    early = rows(spec_cards({"cpu_brand": "quick name", "ram_size_gb": 32}, None))
    assert early[("Processor", "CPU")] == ("quick name", "detected")
    assert early[("Memory", "RAM (GB)")] == ("32", "detected")
    text = specs_text(spec_cards({}, PROFILE))
    assert "GPU: GPU A, GPU B (not certain)" in text and "Vulkan: not available" in text


def test_this_pc_page_shows_cards_and_copies(qapp):
    page = ThisPcPage({"cpu_brand": "quick name"})
    assert page.value_labels[("Processor", "CPU")].text() == "CPU: quick name"
    page.set_profile(PROFILE)
    assert page.value_labels[("Processor", "CPU")].text() == "CPU: Test CPU 9000"
    assert "not certain" in page.value_labels[("Graphics", "GPU")].text()
    assert "Test CPU 9000" in page.copy_specs() and QApplication.clipboard().text().startswith("Processor")


def test_tests_page_marks_selection_links_and_run(qapp):
    opened, runs = [], []
    page = TestsPage(open_url=opened.append)
    page.run_requested.connect(runs.append)
    page.set_entries([entry("a"), entry("b", group="storage"),
                      entry("gone", state=MISSING, url="https://example.org/dl"),
                      entry("linux_only", compatible=False)])
    assert "3 tests can run on this PC; 2 of them are installed. 1 more" in page.summary.text()
    assert set(page.items) == {"a", "b", "gone"}              # the other system's test is hidden
    page.show_all.setChecked(True)
    assert "linux_only" in page.items and not page.items["linux_only"].flags() & Qt.ItemIsUserCheckable
    assert not page.items["gone"].flags() & Qt.ItemIsUserCheckable
    page.tree.itemWidget(page.items["gone"], 5).click()
    assert opened == ["https://example.org/dl"]
    assert not page.run_btn.isEnabled()
    page.select_all_installed()
    assert sorted(page.selected_ids()) == ["a", "b"] and page.run_btn.text() == "Run selected (2)..."
    page.set_entries(page.entries)                             # a rescan keeps the ticks
    assert sorted(page.selected_ids()) == ["a", "b"]
    page.run_btn.click()
    assert [e.id for e in runs[0]] == ["a", "b"]
    page.tree.itemWidget(page.items["b"], 5).click()
    assert [e.id for e in runs[1]] == ["b"]


def test_split_and_profile():
    a, s, m = entry("a"), entry("s", mode=ASSISTED, source="steam"), entry("m", state=MISSING)
    assert split_entries([a, s, m]) == ([a], [s])
    from app.profiles import RunProfile
    p = plan_profile(RunProfile("Standard", 3, 1, 600, 0, builtin=True), RunChoice(5, 0, 10))
    assert (p.repetitions, p.warmup_runs, p.cooldown_seconds, p.builtin) == (5, 0, 10, False)
    assert p.name == "Standard (Tests screen: 5x)"


def test_window_starts_on_this_pc_and_runs_tests_with_chosen_count_then_opens_report(qapp, tmp_path):
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {"cpu_brand": "quick name"})
    try:
        assert w.pages.currentWidget() is w.this_pc_page and w.nav_buttons["this_pc"].isChecked()
        w.this_pc_page.tests_btn.click()
        assert w.pages.currentWidget() is w.tests_page
        panel = w.automation_panel
        panel.output_root = tmp_path / "automation_runs"
        panel.telemetry_box.setChecked(False)
        panel.confirm = lambda text: (panel.__dict__.setdefault("asked", []).append(text), True)[1]
        wl = workload("py_score", "print('SCORE=42')")
        opened = []
        started = start_tests(w, [entry("py_score", ref=wl)], ask=lambda *a: RunChoice(2, 0, 0),
                              opener=opened.append)
        assert started and w.pages.currentWidget() is w.tabs and w.tabs.currentWidget() is w.run_tab
        assert "2" in panel.asked[0]
        while panel.worker.isRunning():
            qapp.processEvents()
        qapp.processEvents()
        runs = w.db.get_runs(panel.last_session_id, include_test=True)
        assert [r["status"] for r in runs] == ["completed", "completed"] and runs[0]["score"] == 42
        assert len(opened) == 1 and opened[0].parent == tmp_path / "reports"
        assert "py_score" in opened[0].read_text(encoding="utf-8")
        assert panel.on_plan_finished is None                  # one plan, one report
        # cancelled at the repetitions question: nothing runs
        before = len(w.db.get_sessions(include_test=True))
        assert not start_tests(w, [entry("py_score", ref=wl)], ask=lambda *a: None, opener=opened.append)
        assert len(w.db.get_sessions(include_test=True)) == before and len(opened) == 1
    finally:
        with patch.object(w.automation_panel, "shutdown"):
            w.close()

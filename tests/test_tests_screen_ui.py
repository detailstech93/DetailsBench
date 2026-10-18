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
        page = w.tests_page
        assert started and w.pages.currentWidget() is page          # stays on Tests
        assert "2" in panel.asked[0]
        assert page.progress_box.isVisibleTo(w) and page.busy and not page.run_btn.isEnabled()
        seen = []
        page.run_requested.connect(seen.append)
        page.request_run([entry("py_score", ref=wl)])                # a second test must wait
        assert seen == []
        while panel.worker.isRunning():
            qapp.processEvents()
        qapp.processEvents()
        page.refresh_progress()
        assert not page.busy and page.progress_bar.value() == 100
        assert "Report opened" in page.progress_label.text()
        runs = w.db.get_runs(panel.last_session_id, include_test=True)
        assert [r["status"] for r in runs] == ["completed", "completed"] and runs[0]["score"] == 42
        assert len(opened) == 1 and opened[0].parent == tmp_path / "reports"
        assert "py_score" in opened[0].read_text(encoding="utf-8")
        assert not page.report_btn.isHidden()
        page.report_btn.click()                                # opens the same report again
        assert len(opened) == 2 and opened[1] == opened[0]
        assert panel.on_plan_finished is None                  # one plan, one report
        # cancelled at the repetitions question: nothing runs
        before = len(w.db.get_sessions(include_test=True))
        assert not start_tests(w, [entry("py_score", ref=wl)], ask=lambda *a: None, opener=opened.append)
        assert len(w.db.get_sessions(include_test=True)) == before and len(opened) == 2
    finally:
        with patch.object(w.automation_panel, "shutdown"):
            w.close()


def test_progress_helpers_and_stop(qapp):
    from app.ui.tests_page import RunState, elapsed_text, progress_percent
    assert progress_percent(RunState(True, 1, 4)) == 25 and progress_percent(RunState(True, 0, None)) is None
    assert elapsed_text(65) == "1:05" and elapsed_text(3725) == "1:02:05"
    page = TestsPage()
    page.set_entries([entry("a")])
    state, stops = RunState(True, 1, 4, "7-Zip: run 1"), []
    page.watch(lambda: state, lambda: stops.append(1))
    assert page.progress_bar.value() == 25 and page.progress_label.text() == "Run 2 of 4: 7-Zip: run 1"
    assert not page.tree.itemWidget(page.items["a"], 5).isEnabled()
    page.stop_btn.click()
    assert stops == [1] and page.stop_btn.text() == "Stopping..."
    state = RunState(True, 0, None, "game capture")
    page.refresh_progress()
    assert page.progress_bar.maximum() == 0                      # moving bar without a run count
    state = RunState(False, 2, 4, "Session #3 done: 1 cancelled.")
    page.refresh_progress()
    assert not page.busy and page.progress_label.text() == "Session #3 done: 1 cancelled."
    assert page.tree.itemWidget(page.items["a"], 5).isEnabled() and not page.timer.isActive()


def test_manual_test_waits_for_the_score(qapp):
    page, recorded = TestsPage(), []
    page.offer_record("Cinebench was started.", lambda: recorded.append(1))
    assert page.busy and page.record_btn.isVisibleTo(page) and page.progress_bar.maximum() == 0
    page.record_btn.click()
    assert recorded == [1] and not page.busy and not page.record_btn.isVisibleTo(page)


def test_plan_state_waits_for_the_finished_signal():
    from types import SimpleNamespace
    from app.ui.run_tests import plan_state
    running = [True]
    panel = SimpleNamespace(is_running=lambda: running[0], step=(1, 4, "7-Zip: run 1"),
                            lhm=SimpleNamespace(busy=lambda: False), on_plan_finished=object())
    result = {}
    assert plan_state(panel, result).running and result["started"]
    running[0] = False                                 # thread ended, signal not delivered yet
    s = plan_state(panel, result)
    assert s.running and s.text == "Writing the report..."
    result["final"] = (4, 4, "Session #1 done.")
    assert plan_state(panel, result) == plan_state(panel, result) and not plan_state(panel, result).running
    never = SimpleNamespace(is_running=lambda: False, step=(0, 0, ""), lhm=SimpleNamespace(busy=lambda: False),
                            on_plan_finished=object())
    assert plan_state(never, {}).text == "Not started." and never.on_plan_finished is None


OFFERED = {"1091500": ({"resolution": ["2560x1440"], "preset": ["Custom", "Ultra"], "upscaler": ["Off", "DLSS"],
                        "ray_tracing": [True, False]}, None),
           "750920": (None, "the app cannot set this game's settings")}


def test_preset_box_shows_what_each_game_gets(qapp):
    from app.games.presets import GamePreset
    from app.ui.run_tests import PresetBox
    box = PresetBox([("1091500", "Cyberpunk 2077"), ("750920", "Shadow of the Tomb Raider")], OFFERED,
                    GamePreset(ray_tracing="on"))
    assert box.preset() == GamePreset(ray_tracing="on")
    box.combos["quality"].setCurrentIndex(box.combos["quality"].findData("high"))
    box.combos["upscaler"].setCurrentIndex(box.combos["upscaler"].findData("dlss"))
    text = box.summary.text()
    assert "Cyberpunk 2077: Upscaler DLSS, Ray tracing On." in text
    assert "not set: Quality High (the game was never seen" in text
    assert "Shadow of the Tomb Raider: settings cannot be set (the app cannot set this game's settings)" in text
    assert [box.combos["resolution"].itemData(i) for i in range(box.combos["resolution"].count())][:3] == \
        ["keep", "1280x720", "1920x1080"]


def test_one_game_from_the_tests_screen_asks_counts_and_preset_then_starts_without_the_settings_window(
        qapp, tmp_path, monkeypatch):
    import app.ui.run_tests as rt
    from app.games.presets import GamePreset
    from app.games.settings_file import SAVED_KEY
    from app.paths import load_settings
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Database(str(tmp_path / "w.db")), {"cpu_brand": "quick name"})
    try:
        frame = w.frame_panel
        frame.data_dir = tmp_path / "data"                       # never the settings of this PC
        monkeypatch.setattr(rt, "read_offered", lambda games, data_dir: dict(OFFERED))
        calls = []
        monkeypatch.setattr(frame, "refresh_games", lambda: None)
        monkeypatch.setattr(frame, "select_game", lambda app_id: calls.append(("select", app_id)) or True)
        monkeypatch.setattr(frame, "benchmark_game", lambda **kw: calls.append(("bench", kw)) or True)
        asked = []

        def ask(window, entries, profile, others, games, offered, initial):
            asked.append((games, initial))
            return RunChoice(5, 2, 0, GamePreset("keep", "dlss", "off", "ultra"))
        game = entry("cp", mode=ASSISTED, group="games", source="steam",
                     ref={"app_id": "1091500", "name": "Cyberpunk 2077", "store": "steam"})
        assert start_tests(w, [game], ask=ask)
        assert asked == [([("1091500", "Cyberpunk 2077")], GamePreset())]
        assert calls == [("select", "1091500"), ("bench", {"ask_settings": False, "counts": (2, 5)})]
        saved, _ = load_settings(tmp_path / "data")
        assert saved[SAVED_KEY] == {"1091500": {"upscaler": "DLSS", "ray_tracing": False, "preset": "Ultra"}}
        assert rt.last_preset(tmp_path / "data") == GamePreset("keep", "dlss", "off", "ultra")   # offered next time
        assert "Cyberpunk 2077: Upscaler DLSS, Ray tracing Off, Quality Ultra." in w.status_label.text()
    finally:
        w.close()


def test_game_of_finds_steam_games_and_game_scripts():
    from types import SimpleNamespace
    from app.ui.run_tests import game_of
    assert game_of(entry("a", source="steam", ref={"appid": 391220, "name": "ROTTR"})) == ("391220", "ROTTR")
    script = SimpleNamespace(script={"steam_app": 1091500})
    assert game_of(entry("cyberpunk", ref=script)) == ("1091500", "cyberpunk")
    assert game_of(entry("7zip", ref=SimpleNamespace(script={}))) is None
    assert game_of(entry("lib", source="library", ref=object())) is None

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
    assert page.skip_btn.isHidden() and not page.skip()             # one test: no Skip button


def test_skip_button_only_for_a_plan_of_several_tests(qapp):
    from app.ui.tests_page import RunState
    page = TestsPage()
    page.set_entries([entry("a"), entry("b")])
    skips, stops = [], []
    page.watch(lambda: RunState(True, 1, 4, "a: run 2"), lambda: stops.append(1), skip=lambda: skips.append(1))
    assert not page.skip_btn.isHidden() and page.can_skip()
    page.skip_btn.click()
    assert skips == [1] and stops == [] and page.busy           # only the test going on ends; the plan goes on
    page.stop_btn.click()
    assert stops == [1] and not page.skip()                     # stopping: nothing left to skip


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
                            lhm=SimpleNamespace(busy=lambda: False, active=lambda: False), on_plan_finished=object())
    result = {}
    assert plan_state(panel, result).running and result["started"]
    running[0] = False                                 # thread ended, signal not delivered yet
    s = plan_state(panel, result)
    assert s.running and s.text == "Writing the report..."
    result["final"] = (4, 4, "Session #1 done.")
    assert plan_state(panel, result) == plan_state(panel, result) and not plan_state(panel, result).running
    never = SimpleNamespace(is_running=lambda: False, step=(0, 0, ""), lhm=SimpleNamespace(busy=lambda: False, active=lambda: False),
                            on_plan_finished=object())
    assert plan_state(never, {}).text == "Not started." and never.on_plan_finished is None
    asked = SimpleNamespace(is_running=lambda: False, step=(0, 0, ""), lhm=SimpleNamespace(busy=lambda: False, active=lambda: False),
                            on_plan_finished=object(), asking=True)      # start question open after the sensors
    assert plan_state(asked, {}).running and asked.on_plan_finished is not None



def test_progress_bar_moves_during_a_long_run():
    from types import SimpleNamespace
    from app.ui.run_tests import plan_state
    from app.ui.tests_page import progress_percent
    panel = SimpleNamespace(is_running=lambda: True, step=(0, 1, "Stress test: CPU: run 1 started"),
                            lhm=SimpleNamespace(busy=lambda: False, active=lambda: False), run_progress=lambda: (0.25, 450.0))
    s = plan_state(panel, {})
    assert progress_percent(s) == 25 and s.text.endswith("(7:30 left)")


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
    box.combos["mode"].setCurrentIndex(box.combos["mode"].findData("upscaling"))
    box.combos["upscaler"].setCurrentIndex(box.combos["upscaler"].findData("dlss"))
    assert box.preset() == GamePreset(upscaler="dlss", frame_gen="off", ray_tracing="on", quality="high")
    text = box.summary.text()
    assert "Cyberpunk 2077: Upscaler DLSS" in text and "Ray tracing On" in text
    assert "not set: Quality High (the game was never seen" in text
    assert "Shadow of the Tomb Raider: settings cannot be set (the app cannot set this game's settings)" in text
    res = box.combos["resolution"]
    assert [res.itemData(i) for i in range(res.count())][:3] == ["1280x720", "1920x1080", "2560x1440"]


def test_preset_box_offers_what_the_card_runs_and_a_preset_per_game(qapp):
    """Owner, 2026-10-20: the owner chooses; the best is marked recommended; what the card cannot run is hidden."""
    from app.games.presets import GamePreset
    from app.ui.run_tests import PresetBox

    def items(box, key):
        c = box.combos[key]
        return [(c.itemData(i), c.itemText(i)) for i in range(c.count())]
    amd = PresetBox([("1091500", "Cyberpunk 2077")], OFFERED, GamePreset(), "AMD Radeon RX 7900 XTX")
    amd.combos["mode"].setCurrentIndex(amd.combos["mode"].findData("upscaling"))
    assert items(amd, "upscaler") == [("fsr", "FSR (recommended)"), ("xess", "XeSS")]       # no DLSS on AMD
    assert amd.combos["frame_gen"].isHidden()
    amd.combos["mode"].setCurrentIndex(amd.combos["mode"].findData("upscaling_fg"))
    assert items(amd, "upscaler") == [("fsr", "FSR (recommended)")]                   # XeSS frame gen: Arc only
    assert items(amd, "frame_gen") == [("x2", "On, 2x (recommended)")]
    assert amd.preset() == GamePreset(upscaler="fsr", frame_gen="x2")
    games = [("1091500", "Cyberpunk 2077"), ("1174180", "Red Dead Redemption 2")]
    box = PresetBox(games, OFFERED, GamePreset(quality="high"), "NVIDIA GeForce RTX 5080")
    box.combos["mode"].setCurrentIndex(box.combos["mode"].findData("upscaling_fg"))
    assert items(box, "frame_gen")[0] == ("x2", "On, 2x (recommended)") and len(items(box, "frame_gen")) == 3
    assert box.combos["upscaler"].currentData() == "dlss"
    box.same.setChecked(False)
    box.which.setCurrentIndex(1)
    assert box.combos["mode"].isHidden()                   # Red Dead Redemption 2: no upscaler the app can set
    box.combos["resolution"].setCurrentIndex(box.combos["resolution"].findData("1920x1080"))
    got = box.presets()
    assert got["1091500"] == GamePreset(upscaler="dlss", frame_gen="x2", quality="high")
    assert got["1174180"] == GamePreset(resolution="1920x1080", quality="high")
    box.which.setCurrentIndex(0)
    assert box.combos["mode"].currentData() == "upscaling_fg"        # each game shows its own preset



def test_preset_box_offers_only_what_the_game_has(qapp):
    """Owner, 2026-10-23: Far Cry 6 has no DLSS and no frame generation, so they are not offered at all; its
    quality is High or Ultra, its upscaler FSR 1.0."""
    from app.games.presets import GamePreset
    from app.ui.run_tests import PresetBox

    def data(box, key):
        c = box.combos[key]
        return [c.itemData(i) for i in range(c.count())]
    fc6 = {"2369390": ({"upscaler": ["Off", "FSR 1.0"], "preset": ["High", "Ultra"],
                        "dxr_reflections": [True, False], "dxr_shadows": [True, False]}, None)}
    box = PresetBox([("2369390", "Far Cry 6")], fc6, GamePreset(quality="low", frame_gen="x2", upscaler="dlss"),
                    "NVIDIA GeForce RTX 5080")
    assert data(box, "mode") == ["keep", "raw", "upscaling"]           # no frame generation in the game
    assert data(box, "quality") == ["high", "ultra", "keep"]
    assert box.preset().quality == "keep"                               # Low: the game's preset has none
    box.combos["mode"].setCurrentIndex(box.combos["mode"].findData("upscaling"))
    assert data(box, "upscaler") == ["fsr"] and box.combos["frame_gen"].isHidden()
    assert box.preset() == GamePreset(upscaler="fsr")
    assert "not set" not in box.summary.text()
    cs2 = PresetBox([("730", "Counter-Strike 2")], {"730": ({"resolution": ["1920x1080"]}, None)}, GamePreset())
    assert [k for k in cs2.KEYS if not cs2.combos[k].isHidden()] == ["resolution"]
    both = PresetBox([("2369390", "Far Cry 6"), ("1091500", "Cyberpunk 2077")], dict(fc6, **OFFERED), GamePreset(),
                     "NVIDIA GeForce RTX 5080")
    assert "low" in data(both, "quality")                               # one preset: what any of them has
    both.same.setChecked(False)
    assert data(both, "quality") == ["high", "ultra", "keep"]           # Far Cry 6's own


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
        monkeypatch.setattr(frame, "is_running", lambda: bool(calls))      # running once the game started
        assert start_tests(w, [game], ask=ask)
        assert w.tests_page.get_state().marks == {"cp": "running"}       # its blinking dot, alone too
        w.tests_page.timer.stop()
        monkeypatch.setattr(frame, "is_running", lambda: False)
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


def test_games_have_their_own_section(qapp):
    """Owner, 2026-10-20: Tests and Games are apart; a selection never mixes them."""
    page = TestsPage()
    page.set_entries([entry("a"), entry("g", group="game")])
    assert list(page.items) == ["a"] and page.head.text() == "Tests"
    page.set_checked("a")
    page.set_section("games")
    assert list(page.items) == ["g"] and page.head.text() == "Games" and page.selected_ids() == []
    assert page.run_all_btn.text() == "Run all games..." and page.ai_models_btn.isHidden()
    assert [e.id for e in page.section_entries()] == ["g"] and "games can run" in page.summary.text()
    page.select_all_installed()
    assert page.selected_ids() == ["g"]
    page.set_section("tests")
    assert list(page.items) == ["a"] and page.selected_ids() == []


def test_tests_with_a_typed_score_are_apart_and_cannot_be_ticked(qapp):
    from app.catalog import TYPED_GROUP
    page = TestsPage()
    page.set_entries([entry("a"), entry("geekbench_6", source="library")])
    tops = [page.tree.topLevelItem(i).text(0) for i in range(page.tree.topLevelItemCount())]
    assert tops == ["CPU (1)", f"{TYPED_GROUP} (1)"]
    item = page.items["geekbench_6"]
    assert not item.flags() & Qt.ItemIsUserCheckable
    assert page.tree.itemWidget(item, 5).text() == "Run alone..."
    page.set_all(True)
    assert page.selected_ids() == ["a"]


def test_nothing_starts_while_the_app_scans_and_it_says_so(qapp):
    """Owner, 2026-10-20: while the app scans for tests nothing starts, and it is clear it is scanning."""
    page = TestsPage()
    page.set_entries([entry("a"), entry("b")])
    asked = []
    page.run_requested.connect(asked.append)
    page.set_checked("a")
    page.set_scanning(True)
    assert not page.scan_banner.isHidden() and "Scanning" in page.scan_banner.text()
    assert not page.run_btn.isEnabled() and not page.run_all_btn.isEnabled() and not page.rescan_btn.isEnabled()
    assert not page.tree.itemWidget(page.items["b"], 5).isEnabled()
    page.request_run([page.entry("b")])
    assert asked == []
    page.set_scanning(False)
    assert page.scan_banner.isHidden() and page.run_btn.isEnabled()
    assert page.tree.itemWidget(page.items["b"], 5).isEnabled()
    page.request_run([page.entry("b")])
    assert len(asked) == 1


def test_queue_next_shows_while_tests_run_and_hands_the_ticked_tests_to_the_run(qapp, monkeypatch):
    """Owner, 2026-10-23: while tests run, ticked tests join that run (Queue next) instead of waiting for it."""
    from types import SimpleNamespace
    from PySide6.QtWidgets import QLabel
    from app.ui.run_tests import queue_tests
    from app.ui.tests_page import RunState
    page = TestsPage()
    a, b = entry("tool_a", ref="A"), entry("tool_b", ref="B")
    game = entry("game", mode=ASSISTED, group="game", source="steam", ref={"app_id": "1"})
    typed = entry("puget", mode=ASSISTED, source="library", ref="P")
    page.set_entries([a, b, game, typed])
    taking = {"now": False}
    page.can_add = lambda: taking["now"]
    state = RunState(True, 0, 2, "x", marks={"tool_a": "running"})
    page.watch(lambda: state, lambda: None)
    row = {k: page.tree.itemWidget(page.items[k], 5) for k in ("tool_a", "tool_b")}
    assert page.run_btn.text().startswith("Run selected") and not page.tree.isEnabled()
    taking["now"] = True
    page.refresh_progress()                    # the plan has started: a test's Run is Queue next (2026-10-24)
    assert page.tree.isEnabled()
    assert row["tool_b"].text() == "Queue next" and row["tool_b"].isEnabled()
    assert row["tool_a"].text() == "Run..." and not row["tool_a"].isEnabled()     # it is the test running
    typed_btn = page.tree.itemWidget(page.items["puget"], 5)
    assert typed_btn.text() == "Run alone..." and not typed_btn.isEnabled()   # you type its score: greyed (2026-10-24)
    page.set_section("games")
    game_btn = page.tree.itemWidget(page.items["game"], 5)
    assert game_btn.text() == "Queue next" and game_btn.isEnabled()     # its dialog, then in line (2026-10-24)
    page.set_section("tests")
    row = {k: page.tree.itemWidget(page.items[k], 5) for k in ("tool_a", "tool_b")}
    asked, ran = [], []
    page.queue_run_requested.connect(asked.append)
    page.run_requested.connect(ran.append)
    page.set_checked("tool_b", True)
    assert page.run_btn.text().startswith("Run selected") and not page.run_btn.isEnabled()   # never changes
    row["tool_b"].click()
    assert [[e.id for e in x] for x in asked] == [["tool_b"]] and ran == []
    state.marks["tool_b"] = "queued"          # in line now: greyed like the test running (owner, 2026-10-24)
    page.refresh_progress()
    assert row["tool_b"].text() == "Run..." and not row["tool_b"].isEnabled()
    taking["now"] = False
    page.refresh_progress()
    assert row["tool_b"].text() == "Run..." and not row["tool_b"].isEnabled()

    queued_refs = []
    panel = SimpleNamespace(can_queue=lambda: True, skip_test=lambda: None, is_running=lambda: True,
                            queue_workloads=lambda refs: (queued_refs.extend(refs) or ["tool_b"],
                                                          {"tool_a": "already in this run"}))
    window = SimpleNamespace(automation_panel=panel, frame_panel=SimpleNamespace(is_running=lambda: False),
                             status_label=QLabel(), tests_page=page)
    from app.ui import run_tests
    asked_for = []
    monkeypatch.setattr(run_tests, "prepare_one", lambda w, e, ask: asked_for.append(e.id) or "chosen")
    assert queue_tests(window, [a, b, game], licenses=lambda w, e: e) == ["tool_b", "game"]
    assert queued_refs == ["A", "B"] and asked_for == ["game"]          # the game's dialog opened now
    assert [(w.entry.id, w.prepared) for w in window.run_line] == [("game", "chosen")]
    assert run_tests.line_marks(window) == {"game": "queued"}
    text = window.status_label.text()
    assert "Queued after the tests going on (same report): tool_b." in text
    assert "tool_a (already in this run)" in text and "In line, each on its own after the run going on: game." in text
    assert queue_tests(window, [game], licenses=lambda w, e: e) == []       # not twice
    assert "game (already in line)" in window.status_label.text()
    assert queue_tests(window, [typed], licenses=lambda w, e: e) == [] and asked_for == ["game"]
    assert "puget (you type its score: run it alone)" in window.status_label.text()
    assert not page.skip_btn.isHidden()                        # a test to skip to now


def test_the_tests_in_line_start_one_by_one_after_the_run_and_a_stop_ends_the_line(qapp, monkeypatch):
    """Owner, 2026-10-24: a game or a test that needs you waits in line and starts on its own once the run
    before it is over; what you stop, the tests after it do not start."""
    from types import SimpleNamespace
    from PySide6.QtWidgets import QLabel
    from app.ui import run_tests
    busy = {"now": False}
    page = SimpleNamespace(busy=False)
    window = SimpleNamespace(automation_panel=SimpleNamespace(is_running=lambda: busy["now"]),
                             frame_panel=SimpleNamespace(is_running=lambda: False), status_label=QLabel(),
                             tests_page=page)
    g1, g2 = entry("g1", source="steam"), entry("g2", source="steam")
    window.run_line = [run_tests.Waiting(g1, "p1"), run_tests.Waiting(g2, "p2")]
    begun = []

    def begin(w, prepared, opener, after=None):
        begun.append(prepared)
        page.busy = prepared != "p1"            # g1 does not start (not installed now): g2 starts instead
        return True
    monkeypatch.setattr(run_tests, "begin", begin)
    busy["now"] = True
    assert run_tests.start_waiting(window) is False and begun == []      # the run before is still going on
    busy["now"] = False
    assert run_tests.start_waiting(window) is True and begun == ["p1", "p2"] and window.run_line == []
    assert "g1 (in line) did not start." in window.status_label.text()
    window.run_line = [run_tests.Waiting(g1, "p1")]
    run_tests.line_next(window, stopped=True)
    assert window.run_line == [] and "the tests in line were not started (g1)" in window.status_label.text()


def test_a_test_that_needs_you_lets_the_line_go_on_once_its_scores_are_typed(qapp):
    page = TestsPage()
    ended = []
    page.offer_record("Run it, then enter the score.", lambda: None, ended.append)
    page._record()
    page.offer_record("Run it again.", lambda: None, ended.append)
    page._stop()
    assert ended == [False, True]


def test_a_test_with_its_own_settings_queued_while_a_game_runs_asks_them_at_once(qapp, monkeypatch):
    """Owner, 2026-10-24: the AI test's settings menu opens when Queue next is pressed, not when its turn comes."""
    from types import SimpleNamespace
    from app.ui import run_tests

    class AI:
        id, asks_settings = "llama", True

        def configure(self, settings):
            pass
    menus = []
    panel = SimpleNamespace(choose_settings=lambda wl: menus.append(wl.id) or {"model": "m"})
    window = SimpleNamespace(automation_panel=panel)
    ai = entry("llama", ref=AI())
    monkeypatch.setattr(run_tests, "choose_run",
                        lambda w, auto, others, lone, ask: run_tests.Prepared(auto, others, run_tests.RunChoice(3, 1, 0)))
    prepared = run_tests.prepare_one(window, ai)
    assert menus == ["llama"] and prepared.settings == {"llama": {"model": "m"}}
    panel.choose_settings = lambda wl: None                 # the menu closed with nothing chosen: not queued
    assert run_tests.prepare_one(window, ai) is None
    assert run_tests.prepare_one(window, entry("puget", source="library")) is None


def test_shut_down_after_the_run_waits_for_the_tests_in_line(qapp):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QLabel
    from app.ui import run_tests
    window = SimpleNamespace(status_label=QLabel("Session #1 done."))
    assert run_tests.shutdown_later(window) is False                     # nothing in line: shut down now
    tool = run_tests.Prepared([entry("tool_a")], [], run_tests.RunChoice(1, 0, 0))
    window.run_line = [run_tests.Waiting(entry("tool_a"), tool)]
    assert run_tests.shutdown_later(window) is True and tool.choice.shutdown_after is True
    assert "shuts down after the tests in line (tool_a last)" in window.status_label.text()
    game = run_tests.Prepared([], [entry("g", source="steam")], run_tests.RunChoice(1, 0, 0))
    window.run_line.append(run_tests.Waiting(entry("g", source="steam"), game))
    assert run_tests.shutdown_later(window) is True and "not shut down: a game is in line" in window.status_label.text()


def test_double_click_on_the_test_running_queues_nothing(qapp):
    from app.ui.tests_page import RunState
    page = TestsPage()
    page.set_entries([entry("tool_a"), entry("tool_b")])
    page.can_add = lambda: True
    page.watch(lambda: RunState(True, 0, 2, "x", marks={"tool_a": "running"}), lambda: None)
    asked = []
    page.queue_run_requested.connect(asked.append)
    page.request_run([page.entry("tool_a")])
    page.request_run([page.entry("tool_b")])
    assert [[e.id for e in x] for x in asked] == [["tool_b"]]

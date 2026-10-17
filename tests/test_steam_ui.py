"""MOCKED UI tests for the Steam tab and SteamLaunchWorker (nothing is launched)."""

import sys
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.discovery.result import DiscoveryResult
from app.ui.main_window import MainWindow, SteamLaunchWorker


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def game(appid, name, installed=True, bench=None):
    return {"store": "steam", "app_id": appid, "name": name, "install_path": rf"Z:\lib\common\{name}",
            "version": "1", "library": r"Z:\lib", "installed": installed, "size_on_disk": 1,
            "platform": "windows", "runtime": "native", "builtin_benchmark": bench}


def discovery():
    return DiscoveryResult(
        games=[game("730", "Counter-Strike 2"), game("1091500", "Cyberpunk 2077", bench="unverified"),
               game("4628710", "Proton 11.0", installed=False),
               {"store": "epic", "app_id": "x", "name": "Epic Game", "install_path": "D:/x", "version": None}],
        stores={"steam": {"status": "detected", "path": r"C:\Steam", "source": "registry",
                          "libraries": [r"C:\Steam", r"Z:\SteamLibrary"], "notes": []}},
    )


@pytest.fixture
def window(qapp):
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Mock(), {})
    w._on_discovery_ready(discovery())
    yield w
    w.close()


def test_table_lists_only_steam_games_sorted(window):
    t = window.steam_table
    assert [t.item(r, 1).text() for r in range(t.rowCount())] == ["Counter-Strike 2", "Cyberpunk 2077", "Proton 11.0"]
    assert t.item(1, 3).text() == "unverified"
    assert "Z:\\SteamLibrary" in window.steam_status_label.text()


def buttons(table, row):
    return {b.text(): b for b in table.cellWidget(row, 6).findChildren(QPushButton)}


def test_launch_and_benchmark_buttons_disabled_for_partial_install(window):
    t = window.steam_table
    assert set(buttons(t, 0)) == {"Launch...", "Benchmark..."}
    assert all(b.isEnabled() for b in buttons(t, 0).values())
    assert not any(b.isEnabled() for b in buttons(t, 2).values())


def test_benchmark_button_hands_the_game_to_the_frame_section(window):
    assert [window.frame_panel.game_combo.itemData(i)["app_id"]
            for i in range(window.frame_panel.game_combo.count())] == ["730", "1091500"]   # installed only
    with patch.object(type(window.frame_panel), "benchmark_game") as bench:
        buttons(window.steam_table, 1)["Benchmark..."].click()
    bench.assert_called_once()
    assert window.frame_panel.game_combo.currentData()["app_id"] == "1091500"
    assert window.tabs.currentWidget() is window.run_tab


def test_cancelled_confirmation_starts_nothing(window):
    with patch.object(MainWindow, "_confirm_steam_launch", return_value=False), \
            patch.object(SteamLaunchWorker, "start") as start:
        window._on_steam_launch_clicked(discovery().games[1])
    start.assert_not_called()
    assert "cancelled by user" in window.steam_log.text()


def test_confirmed_launch_starts_worker_with_discovered_appids(window):
    with patch.object(MainWindow, "_confirm_steam_launch", return_value=True), \
            patch.object(SteamLaunchWorker, "start") as start:
        window._on_steam_launch_clicked(discovery().games[1])
    start.assert_called_once()
    assert window._steam_worker.discovered_appids == ["730", "1091500", "4628710"]


def test_worker_emits_result_from_launch_and_confirm(qapp):
    worker = SteamLaunchWorker(game("1091500", "Cyberpunk 2077"), ["1091500"])
    got = []
    worker.finished.connect(got.append)
    with patch("app.ui.main_window.launch_and_confirm",
               return_value={"status": "unconfirmed", "message": "m", "url": "steam://rungameid/1091500"}) as lac:
        worker.run()
    assert got[0]["status"] == "unconfirmed"
    assert lac.call_args[0][:3] == ("1091500", r"Z:\lib\common\Cyberpunk 2077", ["1091500"])


def test_worker_stop_ends_wait(qapp):
    worker = SteamLaunchWorker(game("1", "G"), ["1"])
    worker.stop()
    captured = {}

    def fake(*a, **kw):
        captured["now"] = kw["clock"]()
        return {"status": "unconfirmed", "message": "", "url": None}
    with patch("app.ui.main_window.launch_and_confirm", side_effect=fake):
        worker.run()
    assert captured["now"] == float("inf")


def test_tool_rows_cannot_be_launched(qapp):
    tool = dict(game("3658110", "Proton 10.0"), kind="tool")
    result = DiscoveryResult(games=[tool], stores={"steam": {"status": "detected", "path": "C:/S",
                                                             "libraries": [], "notes": []}})
    with patch.object(MainWindow, "start_scan"):
        w = MainWindow(Mock(), {})
    w._on_discovery_ready(result)
    assert list(buttons(w.steam_table, 0)) == ["Steam tool"]
    assert buttons(w.steam_table, 0)["Steam tool"].isEnabled() is False
    w.close()

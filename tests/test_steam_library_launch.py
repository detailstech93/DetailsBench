"""A benchmark found in a Steam library (3DMark) starts through Steam: its exe started directly shows
"Could not connect to Steam client" (owner, 2026-10-21)."""

import os

from app import benchmark_manager as bmm
from app.benchmark_manager import BenchmarkInfo, BenchmarkStatus, BenchmarkType


def _info(path):
    return BenchmarkInfo("d3dmark", "3DMark", BenchmarkType.GPU, BenchmarkStatus.READY_MANUAL, executable_path=path)


def test_only_an_exe_inside_the_steam_folder_counts():
    folder = os.path.join("C:" + os.sep, "Steam", "steamapps", "common", "3DMark")
    find = lambda app: folder if app == 231350 else None
    assert bmm.steam_app_of(_info(os.path.join(folder, "bin", "x64", "3DMark.exe")), find) == 231350
    assert bmm.steam_app_of(_info(os.path.join("C:" + os.sep, "UL", "3DMark", "3DMark.exe")), find) is None
    assert bmm.steam_app_of(_info(folder + "X" + os.sep + "3DMark.exe"), find) is None
    assert bmm.steam_app_of(_info(None), find) is None


def test_it_is_started_with_a_steam_url_not_the_exe(monkeypatch):
    opened = []
    snaps = iter([[], [("3dmark.exe", None)]])
    monkeypatch.setattr(bmm, "_get_process_snapshot", lambda: next(snaps))
    monkeypatch.setattr(bmm, "_has_new_process", lambda before, after, name: (name, None) in after)
    monkeypatch.setattr(bmm.time, "sleep", lambda s: None)
    r = bmm._launch_through_steam(231350, _info(r"C:\S\bin\x64\3DMark.exe"), 30, opener=opened.append)
    assert opened == ["steam://rungameid/231350"] and r["status"] == "launched"

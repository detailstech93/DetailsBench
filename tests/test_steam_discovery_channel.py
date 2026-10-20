"""3DMark found through Steam's own libraries (its uninstall entry kept an old drive letter, owner's PC 2026-10-20)."""

import dataclasses

from app.benchmark_manager import BenchmarkManager, BenchmarkStatus
from app.simulation import script
from data.benchmark_definitions import DEFINITIONS_BY_ID


def test_3dmark_demo_found_in_a_steam_library(tmp_path, monkeypatch):
    folder = tmp_path / "SteamLibrary" / "steamapps" / "common" / "3DMark Demo"
    exe = folder / "bin" / "x64" / "3DMark.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(script, "find_steam_game", lambda app_id: str(folder) if app_id == 231350 else None)
    defn = dataclasses.replace(DEFINITIONS_BY_ID["d3dmark"], discovery_channels=["steam"])
    found = BenchmarkManager()._discover_from_definition(defn, {})
    assert found and found[0].status != BenchmarkStatus.NOT_INSTALLED
    assert found[0].executable_path.lower() == str(exe).lower()


def test_nothing_in_steam_is_not_found(monkeypatch):
    monkeypatch.setattr(script, "find_steam_game", lambda app_id: None)
    defn = dataclasses.replace(DEFINITIONS_BY_ID["d3dmark"], discovery_channels=["steam"])
    found = BenchmarkManager()._discover_from_definition(defn, {})
    assert found[0].status == BenchmarkStatus.NOT_INSTALLED

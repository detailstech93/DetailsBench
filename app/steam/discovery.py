"""Add installed Steam games to a DiscoveryResult (called after the client check)."""

from app.discovery.result import DiscoveryResult
from app.steam.library import library_paths, scan_library


def add_steam_games(result: DiscoveryResult, platform: str) -> None:
    """Every library is its own isolated step: a slow or offline network
    library is recorded in diagnostics and never blocks the others."""
    client = result.stores.get("steam") or {}
    if client.get("status") != "detected" or not client.get("path"):
        return
    libraries = []

    def find_libraries():
        found, notes = library_paths(client["path"])
        libraries.extend(found)
        client["libraries"] = found
        client["notes"] = notes

    result.run_step("steam_libraries", find_libraries)
    for i, library in enumerate(libraries):
        def scan(library=library):
            games, notes = scan_library(library, platform)
            result.games.extend(games)
            client.setdefault("notes", []).extend(notes)
        result.run_step(f"steam_library_{i}", scan)

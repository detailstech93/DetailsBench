"""Sanity checks for data/steam_benchmarks.py (no network)."""

from data.steam_benchmarks import STEAM_BENCHMARK_APPS, STEAM_BENCHMARK_APPS_BY_ID


def test_every_entry_is_verified_appid_and_unverified_benchmark():
    for app in STEAM_BENCHMARK_APPS:
        assert isinstance(app["appid"], int) and app["appid"] > 0
        assert app["appid_verified"]
        assert app["builtin_benchmark"] == "unverified"   # until tested on a real machine
        assert app["kind"] in ("benchmark_tool", "game")


def test_ids_unique():
    assert len(STEAM_BENCHMARK_APPS_BY_ID) == len(STEAM_BENCHMARK_APPS)

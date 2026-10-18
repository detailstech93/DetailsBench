"""Hand-maintained list of Steam apps relevant to benchmarking.

Rules:
- An appid is added only after checking it against Steam's official store
  endpoint (https://store.steampowered.com/api/appdetails?appids=<id>);
  "store_name" is the name returned there and "appid_verified" the date.
- "builtin_benchmark" says whether the app is believed to ship a benchmark
  mode. It stays "unverified" until someone runs that mode on a real
  machine; it is general knowledge, not a tested fact.
- Nothing here means the app can be automated.
- "exe" (optional) is the game's executable inside its install folder. It is
  started directly when launch arguments are passed, so Steam does not ask to
  confirm "custom arguments" (app/steam/launch.py).
"""

from typing import Dict, List

STEAM_BENCHMARK_APPS: List[Dict[str, object]] = [
    {"appid": 223850, "store_name": "3DMark", "kind": "benchmark_tool",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 1091500, "store_name": "Cyberpunk 2077", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17",
     "exe": "bin/x64/Cyberpunk2077.exe"},
    {"appid": 1174180, "store_name": "Red Dead Redemption 2", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 750920, "store_name": "Shadow of the Tomb Raider: Definitive Edition", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 391220, "store_name": "Rise of the Tomb Raider™", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 397540, "store_name": "Borderlands 3", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 2369390, "store_name": "Far Cry® 6", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-18"},
    # its benchmark is its own program, metro_benchmark.exe, in the install folder (seen 2026-10-18; see
    # SKIP_LAUNCHER): it opens a settings window and the owner presses its Run button
    {"appid": 286690, "store_name": "Metro 2033 Redux", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-18"},
]

STEAM_BENCHMARK_APPS_BY_ID = {int(a["appid"]): a for a in STEAM_BENCHMARK_APPS}

# Games the app starts by a file in their install folder, with no arguments, instead of through Steam:
# - Steam would open a launcher first (CD Projekt RED's REDprelauncher; launcher-configuration.json of The
#   Witcher 3 4.x lists only bin\x64_dx12\witcher3.exe, checked 2026-10-18);
# - the built-in benchmark is its own program (Metro 2033 Redux).
SKIP_LAUNCHER = {
    292030: "bin\\x64_dx12\\witcher3.exe",      # The Witcher 3: Wild Hunt (next-gen, DirectX 12)
    286690: "metro_benchmark.exe",               # Metro 2033 Redux: its benchmark program
}

# Steam components that show up as installed apps but are not games
# (compatibility tools, runtimes, redistributables): listed, never launched.
STEAM_TOOL_NAME_PATTERNS = [
    r"^Proton\b",
    r"^Steam Linux Runtime\b",
    r"^Steamworks Common Redistributables$",
]

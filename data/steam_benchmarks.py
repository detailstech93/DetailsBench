"""Hand-maintained list of Steam apps relevant to benchmarking.

Rules:
- An appid is added only after checking it against Steam's official store
  endpoint (https://store.steampowered.com/api/appdetails?appids=<id>);
  "store_name" is the name returned there and "appid_verified" the date.
- "builtin_benchmark" says whether the app is believed to ship a benchmark
  mode. It stays "unverified" until someone runs that mode on a real
  machine; it is general knowledge, not a tested fact.
- Nothing here means the app can be automated.
"""

from typing import Dict, List

STEAM_BENCHMARK_APPS: List[Dict[str, object]] = [
    {"appid": 223850, "store_name": "3DMark", "kind": "benchmark_tool",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 1091500, "store_name": "Cyberpunk 2077", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 1174180, "store_name": "Red Dead Redemption 2", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 750920, "store_name": "Shadow of the Tomb Raider: Definitive Edition", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 391220, "store_name": "Rise of the Tomb Raider™", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
    {"appid": 397540, "store_name": "Borderlands 3", "kind": "game",
     "builtin_benchmark": "unverified", "appid_verified": "2026-10-17"},
]

STEAM_BENCHMARK_APPS_BY_ID = {int(a["appid"]): a for a in STEAM_BENCHMARK_APPS}

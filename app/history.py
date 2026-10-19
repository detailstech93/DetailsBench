"""Earlier runs of the same tests on the same PC, for the report (ReBench's idea of comparing over time, owner
2026-10-19).

"Same PC" is read from the sessions' manifests: the same CPU, GPU(s), RAM size and OS family. A session without
those (or with a different one) is never used. For each test of this session the earlier sessions with completed
runs of it are listed, newest first (at most HISTORY_LIMIT); the newest is compared with this one through
app/compare.py, so a different major version, architecture, runtime or emulation still says "not comparable".
Test sessions are only compared with test sessions. Nothing is changed in the database.
"""

from typing import Any, Dict, List, Mapping, Optional

from app.stats import summarize_runs

HISTORY_LIMIT = 5           # earlier sessions shown per test
SESSIONS_SCANNED = 300      # newest earlier sessions looked at


def _value(env: Mapping, name: str) -> Any:
    item = env.get(name)
    return item.get("value") if isinstance(item, Mapping) else None


def pc_key(manifest: Optional[Mapping]) -> Optional[tuple]:
    """(cpu, gpus, ram GB, OS family) from a manifest, or None when the CPU is not known."""
    env = (manifest or {}).get("environment") if isinstance(manifest, Mapping) else None
    if not isinstance(env, Mapping):
        return None
    cpu = _value(env, "cpu")
    if not cpu:
        return None
    gpu = _value(env, "gpu")
    gpus = tuple(sorted(str(g) for g in gpu)) if isinstance(gpu, (list, tuple)) else (str(gpu),) if gpu else ()
    ram = _value(env, "ram_gb")
    try:
        ram = round(float(ram))
    except (TypeError, ValueError):
        ram = None
    return str(cpu).strip(), gpus, ram, _value(env, "os_family")


def _side(runs: List[Mapping], manifest, is_test: bool) -> dict:
    versions = {r.get("benchmark_version") for r in runs if r.get("benchmark_version")}
    return {"summary": summarize_runs(runs, include_test=is_test),
            "version": versions.pop() if len(versions) == 1 else None, "manifest": manifest, "is_test": is_test}


def _unit(runs: List[Mapping]) -> str:
    units = [r.get("unit") for r in runs if r.get("status") == "completed" and r.get("unit")]
    return units[-1] if units else ""


def _game_settings(manifest: Optional[Mapping]):
    """(the game's settings asked for or entered, capture seconds) of a game session, or None when unknown."""
    rs = (manifest or {}).get("run_settings") if isinstance(manifest, Mapping) else None
    record = (rs or {}).get("game_settings") if isinstance(rs, Mapping) else None
    if not isinstance(record, Mapping):
        return None
    values = record.get("requested") or record.get("declared")
    return (dict(values) if isinstance(values, Mapping) else None), rs.get("capture_seconds")


def _is_game(name: str, *manifests) -> bool:
    from data.test_catalog import TEST_INFO
    return (TEST_INFO.get(name, {}).get("group") == "game" or "builtin_benchmark" in name
            or any(isinstance((m or {}).get("run_settings"), Mapping) and "steam_app_id" in m["run_settings"]
                   for m in manifests if isinstance(m, Mapping)))


def game_settings_issue(name: str, before: Optional[Mapping], now: Optional[Mapping]) -> Optional[dict]:
    """A game's FPS depends on its settings: different settings are not comparable, unknown ones are a warning."""
    if not _is_game(name, before, now):
        return None
    a, b = _game_settings(before), _game_settings(now)
    if a is None or b is None or a[0] is None or b[0] is None:
        return {"level": "warn", "code": "game_settings_unknown",
                "message": "The game's settings are not known for both runs: the scores may come from different "
                           "settings."}
    if a != b:
        return {"level": "block", "code": "game_settings",
                "message": "The game's settings (or the measured time) differ."}
    return None


def earlier_comparisons(db, session: Mapping, manifest: Optional[Mapping], runs: List[Mapping]) -> List[dict]:
    """One row per test of this session that has earlier results on this PC:
    {benchmark, unit, now: mean, earlier: [{session_id, name, created_at, mean, runs, version}], compare: {...}}."""
    from app.compare import compare_benchmark
    key = pc_key(manifest)
    if key is None:
        return []
    is_test = bool(session.get("is_test"))
    grouped: Dict[str, List[Mapping]] = {}
    for run in runs:
        grouped.setdefault(run["benchmark_name"], []).append(run)
    now_sides = {n: _side(g, manifest, is_test) for n, g in grouped.items()}
    wanted = {n for n, s in now_sides.items() if (s["summary"].get("stats") or {}).get("mean") is not None}
    if not wanted:
        return []
    found: Dict[str, List[dict]] = {n: [] for n in wanted}
    sessions = [s for s in db.search_sessions(include_test=True)
                if s["id"] != session.get("id") and bool(s.get("is_test")) == is_test
                and str(s.get("created_at") or "") < str(session.get("created_at") or "")
                and set(s.get("benchmarks") or ()) & wanted][:SESSIONS_SCANNED]
    for s in sessions:                      # newest first
        if all(len(v) >= HISTORY_LIMIT for v in found.values()):
            break
        other_manifest = db.get_session_manifest(s["id"])
        if pc_key(other_manifest) != key:
            continue
        other_runs: Dict[str, List[Mapping]] = {}
        for run in db.get_runs(s["id"], include_test=True):
            if run["benchmark_name"] in wanted:
                other_runs.setdefault(run["benchmark_name"], []).append(run)
        for name, group in other_runs.items():
            if len(found[name]) >= HISTORY_LIMIT:
                continue
            side = _side(group, other_manifest, is_test)
            mean = (side["summary"].get("stats") or {}).get("mean")
            if mean is None:
                continue
            found[name].append({"session_id": s["id"], "name": s.get("name"), "created_at": s.get("created_at"),
                                "mean": mean, "runs": side["summary"].get("included"), "version": side["version"],
                                "side": side})
    rows = []
    for name in sorted(n for n in wanted if found[n]):
        latest = found[name][0]
        before = latest.pop("side")
        result = compare_benchmark(name, before, now_sides[name])
        issue = game_settings_issue(name, before["manifest"], manifest)
        if issue:
            result["issues"].append(issue)
            if issue["level"] == "block":
                result["status"] = "not_comparable"
            elif result["status"] == "ok":
                result["status"] = "warning"
        for e in found[name][1:]:
            e.pop("side", None)
        rows.append({"benchmark": name, "unit": _unit(grouped[name]),
                     "now": now_sides[name]["summary"]["stats"]["mean"], "earlier": found[name],
                     "compare": {k: result.get(k) for k in ("status", "delta_percent", "issues")}})
    return rows

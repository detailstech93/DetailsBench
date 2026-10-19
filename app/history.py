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


def effective_game_settings(manifest: Optional[Mapping]) -> Optional[Dict[str, object]]:
    """Every setting the game ran with, as far as the app knows: what its settings file held before the run
    ("before": all settings the app reads, also those it does not change, like DLSS, frame generation, ray tracing)
    with the values the app forced on top (minus refused ones), or the values the owner entered. None: unknown."""
    rs = (manifest or {}).get("run_settings") if isinstance(manifest, Mapping) else None
    record = (rs or {}).get("game_settings") if isinstance(rs, Mapping) else None
    if not isinstance(record, Mapping):
        return None
    if isinstance(record.get("declared"), Mapping):
        return dict(record["declared"])
    before = record.get("before") if isinstance(record.get("before"), Mapping) else {}
    requested = record.get("requested") if isinstance(record.get("requested"), Mapping) else {}
    refused = record.get("refused") if isinstance(record.get("refused"), Mapping) else {}
    values = dict(before)
    values.update({k: v for k, v in requested.items() if k not in refused})
    return values or None


def _capture_seconds(manifest: Optional[Mapping]):
    rs = (manifest or {}).get("run_settings") if isinstance(manifest, Mapping) else None
    return rs.get("capture_seconds") if isinstance(rs, Mapping) else None


def _is_game(name: str, *manifests) -> bool:
    from data.test_catalog import TEST_INFO
    return (TEST_INFO.get(name, {}).get("group") == "game" or "builtin_benchmark" in name
            or any(isinstance((m or {}).get("run_settings"), Mapping) and "steam_app_id" in m["run_settings"]
                   for m in manifests if isinstance(m, Mapping)))


def _show(value) -> str:
    return "-" if value is None else str(value)


def game_settings_issue(name: str, before: Optional[Mapping], now: Optional[Mapping]) -> Optional[dict]:
    """A game's FPS depends on every setting (DLSS / FSR, frame generation, ray or path tracing, preset,
    resolution...): any difference makes the scores not comparable and is named; unknown settings are a warning.
    A setting known on one side only is named as unknown (owner, 2026-10-19)."""
    if not _is_game(name, before, now):
        return None
    a, b = effective_game_settings(before), effective_game_settings(now)
    if a is None or b is None:
        return {"level": "warn", "code": "game_settings_unknown",
                "message": "The game's settings are not known for both runs (DLSS, ray tracing, preset...): the "
                           "scores may come from different settings."}
    differ = [f"{k} {_show(a[k])} -> {_show(b[k])}" for k in sorted(set(a) & set(b)) if a[k] != b[k]]
    one_side = sorted(set(a) ^ set(b))
    seconds = _capture_seconds(before), _capture_seconds(now)
    if seconds[0] is not None and seconds[1] is not None and seconds[0] != seconds[1]:
        differ.append(f"measured time {seconds[0]} s -> {seconds[1]} s")
    if differ:
        return {"level": "block", "code": "game_settings",
                "message": "Different game settings: " + ", ".join(differ) + "."}
    if one_side:
        return {"level": "warn", "code": "game_settings_partly_unknown",
                "message": "Known for one run only: " + ", ".join(one_side) + "."}
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

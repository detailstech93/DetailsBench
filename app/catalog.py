"""One list of every test the app knows, for the Tests screen.

Sources (all already loaded elsewhere; nothing is run or downloaded here):
- automated workloads (tool specs, AI, storage, simulation scripts): app.all_workloads
- library entries from the benchmark scan (Geekbench, Cinebench, 3DMark, web tests): BenchmarkInfo
- Steam games with a built-in benchmark (data.steam_benchmarks), installed or not

Pure functions: callers pass the loaded objects and the environment profile dict in.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from data.steam_benchmarks import STEAM_BENCHMARK_APPS
from data.test_catalog import STEAM_STORE_URL, TEST_INFO

# how a test runs
AUTOMATIC = "automatic"     # the app runs it and reads the result
ASSISTED = "assisted"       # the app starts it, the owner starts the benchmark inside it
MANUAL = "manual"           # the owner runs it and types the score

# install state
INSTALLED = "installed"
MISSING = "missing"
UNUSABLE = "unusable"       # found, but cannot run now (prerequisite, license, scan error)

GROUPS = (("cpu", "CPU"), ("gpu", "GPU"), ("game", "Games"), ("storage", "Storage"),
          ("ai", "AI"), ("web", "Web"), ("other", "Other"))
_GROUP_IDS = {g for g, _ in GROUPS}


@dataclass
class TestEntry:
    __test__ = False        # not a pytest class
    id: str
    name: str
    group: str
    mode: str
    state: str
    state_reason: str
    compatible: bool
    compatibility_reason: str
    verified: bool
    official_url: Optional[str] = None
    expected_minutes: Optional[float] = None
    source: str = ""            # "workload" | "library" | "steam"
    ref: object = None          # the workload, BenchmarkInfo or game dict it came from

    def can_run(self) -> bool:
        return self.compatible and self.state == INSTALLED


def check_platform(info: dict, environment: Optional[dict]) -> Tuple[bool, str]:
    """(compatible, reason) from supported_os / supported_architectures against the profile dict.
    Unknown values never hide a test: only a stated "unsupported" or a missing OS family does."""
    def value(name):
        f = (environment or {}).get(name)
        return f.get("value") if isinstance(f, dict) else f
    os_family, arch = value("os_family"), value("native_arch")
    oses = info.get("supported_os")
    if oses and os_family and os_family not in oses:
        return False, f"runs on {', '.join(oses)} only"
    arches = info.get("supported_architectures") or {}
    support = arches.get(arch) if arch else None
    if support == "unsupported":
        return False, f"no {arch} build"
    if arch and arches and support in (None, "unverified"):
        return True, f"not checked on {arch}"
    if support == "emulated":
        return True, f"runs emulated on {arch}"
    return True, ""


def _group(test_id: str, fallback: str) -> str:
    g = TEST_INFO.get(test_id, {}).get("group") or fallback
    return g if g in _GROUP_IDS else "other"


def _workload_state(available: bool, reason: str) -> Tuple[str, str]:
    if available:
        return INSTALLED, reason
    low = reason.lower()
    if "not found" in low or "not installed" in low:
        return MISSING, reason
    return UNUSABLE, reason


def _workload_verified(wl) -> bool:
    spec = getattr(wl, "spec", None)
    if spec is not None:
        return bool(getattr(spec, "verified", False))
    script = getattr(wl, "script", None)
    if isinstance(script, dict):
        return bool(script.get("verified", False))
    return bool(getattr(wl, "verified", False))


def workload_entries(workloads, environment: Optional[dict]) -> List[TestEntry]:
    entries = []
    for wl in workloads:
        if getattr(wl, "is_test", False):
            continue                      # framework self-tests are not offered on the Tests screen
        info = TEST_INFO.get(wl.id, {})
        try:
            available, reason = wl.availability()
        except Exception as e:            # one broken source must not hide the others
            available, reason = False, f"availability check failed: {e}"
        state, why = _workload_state(available, str(reason))
        ok, comp = check_platform(info, environment)
        script = getattr(wl, "script", None)
        assisted = isinstance(script, dict) and not any(s.get("do") == "keys" for s in script.get("steps", []))
        entries.append(TestEntry(
            id=wl.id, name=wl.display_name, group=_group(wl.id, getattr(wl, "benchmark_type", "other")),
            mode=ASSISTED if assisted else AUTOMATIC, state=state, state_reason=why,
            compatible=ok, compatibility_reason=comp, verified=_workload_verified(wl),
            official_url=info.get("official_url"), expected_minutes=info.get("expected_minutes"),
            source="workload", ref=wl))
    return entries


_LIBRARY_STATE = {
    "ready_auto": INSTALLED, "ready_manual": INSTALLED, "not_installed": MISSING,
    "missing_prereqs": UNUSABLE, "license_required": UNUSABLE, "scan_error": UNUSABLE,
    "compatibility_unverified": INSTALLED,
}


def library_entries(benchmarks) -> List[TestEntry]:
    """BenchmarkInfo objects from the scan; their compatibility was already decided by the scan."""
    entries = []
    for bm in benchmarks:
        status = getattr(bm.status, "value", str(bm.status))
        kind = getattr(bm.type, "value", str(bm.type))
        compatible = status != "unsupported"
        comp = "" if compatible else (bm.reason or "not supported on this PC")
        if status == "compatibility_unverified":
            comp = bm.reason or "compatibility not checked"
        found = bool(bm.executable_path) or kind == "web"
        state = _LIBRARY_STATE.get(status) or (INSTALLED if found else MISSING)
        if status == "compatibility_unverified" and not found:
            state = MISSING
        entries.append(TestEntry(
            id=bm.name, name=bm.display_name, group=_group(bm.name, kind),
            mode=MANUAL if kind == "web" or status != "ready_auto" else ASSISTED,
            state=state, state_reason=bm.reason or status.replace("_", " "),
            compatible=compatible, compatibility_reason=comp, verified=False,
            official_url=bm.official_url or TEST_INFO.get(bm.name, {}).get("official_url"),
            expected_minutes=TEST_INFO.get(bm.name, {}).get("expected_minutes"),
            source="library", ref=bm))
    return entries


def steam_entries(games: List[dict], environment: Optional[dict], covered_app_ids=()) -> List[TestEntry]:
    """Games believed to have a built-in benchmark (frame capture runs them), installed or not.
    App ids already covered by a simulation script are left out."""
    installed = {str(g.get("app_id")): g for g in games or []
                 if g.get("store") == "steam" and g.get("installed") is True}
    covered = {str(a) for a in covered_app_ids}
    entries = []
    for app in STEAM_BENCHMARK_APPS:
        app_id = str(app["appid"])
        if app.get("kind") != "game" or app_id in covered:
            continue
        game = installed.get(app_id)
        ok, comp = check_platform({"supported_os": ["windows", "linux"]}, environment)
        entries.append(TestEntry(
            id=f"steam_{app_id}", name=f"{app['store_name']} (built-in benchmark)", group="game",
            mode=ASSISTED, state=INSTALLED if game else MISSING,
            state_reason="installed in Steam" if game else "not installed in Steam",
            compatible=ok, compatibility_reason=comp, verified=app.get("builtin_benchmark") == "verified",
            official_url=STEAM_STORE_URL.format(app_id=app_id), source="steam", ref=game or dict(app)))
    return entries


def build_catalog(workloads, benchmarks, games, environment: Optional[dict]) -> List[TestEntry]:
    """Every test, in group order then name. Ids are unique: a later source never repeats an id."""
    wl = workload_entries(workloads, environment)
    covered = [w.ref.script.get("steam_app") for w in wl
               if isinstance(getattr(w.ref, "script", None), dict) and w.ref.script.get("steam_app")]
    seen, out = set(), []
    for e in wl + library_entries(benchmarks) + steam_entries(games, environment, covered):
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    return sorted(out, key=lambda e: (order.get(e.group, len(order)), e.name.lower()))


def counts(entries: List[TestEntry]) -> Dict[str, int]:
    shown = [e for e in entries if e.compatible]
    return {"compatible": len(shown), "installed": sum(e.state == INSTALLED for e in shown),
            "incompatible": len(entries) - len(shown)}

"""One list of every test the app knows, for the Tests screen.

Sources (all already loaded elsewhere; nothing is run or downloaded here):
- automated workloads (tool specs, AI, storage, simulation scripts): app.all_workloads
- library entries from the benchmark scan (Geekbench, Cinebench, 3DMark, web tests): BenchmarkInfo
- Steam games with a built-in benchmark (data.steam_benchmarks), installed or not

Pure functions: callers pass the loaded objects and the environment profile dict in.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from data.game_start_keys import GAME_MENU_STEPS
from data.steam_benchmarks import DROPPED_GAMES, STEAM_BENCHMARK_APPS
from data.test_catalog import STEAM_STORE_URL, TEST_INFO

# how a test runs
AUTOMATIC = "automatic"     # the app runs it and reads the result
ASSISTED = "assisted"       # the app starts it, the owner starts the benchmark inside it
MANUAL = "manual"           # the owner runs it and types the score

# install state
INSTALLED = "installed"
MISSING = "missing"
UNUSABLE = "unusable"       # found, but cannot run now (prerequisite, license, scan error)

GROUPS = (("cpu", "CPU"), ("gpu", "GPU"), ("game", "Games"), ("stress", "Stress tests"), ("storage", "Storage"),
          ("ai", "AI"), ("web", "Web"), ("other", "Other"))
_GROUP_IDS = {g for g, _ in GROUPS}
# tests whose score is typed by the owner: their own group on the Tests screen, never ticked into a run of several
TYPED_GROUP = "Score typed by you (each runs alone)"
# a scanned program the app already runs by itself (owner, 2026-10-20): its typed-score copy is left out.
# library id: (the workload that runs it, the version it must start with, or None for any)
AUTOMATED_COPY = {"geekbench_7": ("geekbench7_cpu", None), "geekbench_6": ("geekbench6_cpu", None), "cinebench": ("cinebench_2026", "2026"),
                  "passmark_performancetest": ("passmark_cpu", None), "pugetbench_creators": ("pugetbench_photoshop", None)}


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
    emulated: bool = False      # its program is another processor's build, run emulated (app/emulation.py)

    def can_run(self) -> bool:
        return self.compatible and self.state == INSTALLED

    def waits_for_typed_score(self) -> bool:
        """The owner runs it and types the score (library entries: PugetBench, Geekbench, web tests...). Such a
        test only runs alone: in a run of several tests it would stop everything until someone types (owner,
        2026-10-19). A workload can wait for one too (Geekbench 6 on Linux: it runs, its page opens, the owner types)."""
        return self.source == "library" or (self.source == "workload" and getattr(self.ref, "typed_score", False) is True)


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


def script_mode(script: dict) -> str:
    """A script says how it runs ("mode"); without it, one that presses keys is automatic and one that
    only waits for the owner is assisted. (Cyberpunk starts its benchmark by itself with -benchmark.)"""
    if script.get("mode") in (AUTOMATIC, ASSISTED):
        return script["mode"]
    return AUTOMATIC if any(s.get("do") == "keys" for s in script.get("steps", [])) else ASSISTED


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
        blocks = getattr(wl, "system_blocks", None)
        if ok and callable(blocks):         # this system cannot run it, Install or not: hidden like another OS's
            try:
                why_not = blocks()
            except Exception:
                why_not = None
            if why_not:
                ok, comp = False, why_not
        script = getattr(wl, "script", None)
        assisted = isinstance(script, dict) and script_mode(script) == ASSISTED
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
        checked = getattr(bm, "compatibility", None) or {}
        if compatible and checked.get("status") == "incompatible":    # not installed, and not for this system
            compatible = False                                        # either (owner, 2026-10-20: hide those)
            comp = "Unsupported on this system: " + "; ".join(checked.get("reasons") or [])
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


# Gameplay captures (installed games without a built-in benchmark: a fixed time of play, a route) are hidden
# for now; only games with a built-in benchmark are listed (owner, 2026-10-18). True brings them back.
SHOW_GAMEPLAY_CAPTURES = False


def steam_entries(games: List[dict], environment: Optional[dict], covered_app_ids=(),
                  gameplay_captures: bool = SHOW_GAMEPLAY_CAPTURES) -> List[TestEntry]:
    """Games believed to have a built-in benchmark (frame capture runs them), installed or not, then (with
    gameplay_captures) every other installed Steam game as a gameplay capture (the app starts it, records a
    fixed time of play with PresentMon and closes it). App ids already covered by a simulation script are
    left out."""
    installed = {str(g.get("app_id")): g for g in games or []
                 if g.get("store") == "steam" and g.get("installed") is True}
    covered = {str(a) for a in covered_app_ids} | {str(a) for a in DROPPED_GAMES}
    entries = []
    listed = {str(app["appid"]) for app in STEAM_BENCHMARK_APPS}
    for app_id, game in installed.items():
        if not gameplay_captures or app_id in listed or app_id in covered or game.get("kind") == "tool":
            continue
        ok, comp = check_platform({"supported_os": ["windows", "linux"]}, environment)
        entries.append(TestEntry(
            id=f"steam_{app_id}", name=f"{game.get('name') or app_id} (gameplay capture)", group="game",
            mode=ASSISTED, state=INSTALLED, state_reason="installed in Steam; no built-in benchmark: a fixed time "
            "of play is recorded", compatible=ok, compatibility_reason=comp, verified=False,
            official_url=STEAM_STORE_URL.format(app_id=app_id), source="steam", ref=game))
    for app in STEAM_BENCHMARK_APPS:
        app_id = str(app["appid"])
        if app.get("kind") != "game" or (app_id in covered and int(app_id) not in GAME_MENU_STEPS):
            continue                # a game with menu steps is offered next to its script (a trial)
        game = installed.get(app_id)
        ok, comp = check_platform({"supported_os": ["windows", "linux"]}, environment)
        entries.append(TestEntry(
            id=f"steam_{app_id}", name=f"{app['store_name']} (built-in benchmark)", group="game",
            mode=ASSISTED, state=INSTALLED if game else MISSING,
            state_reason="installed in Steam" if game else "not installed in Steam",
            compatible=ok, compatibility_reason=comp, verified=app.get("builtin_benchmark") == "verified",
            official_url=STEAM_STORE_URL.format(app_id=app_id), source="steam", ref=game or dict(app)))
    return entries


def mark_emulated(entries: List[TestEntry], environment: Optional[dict], read=None) -> None:
    """Sets .emulated on installed tests whose program is an x64/x86 build on an ARM computer."""
    from app import emulation
    f = (environment or {}).get("native_arch")
    native = f.get("value") if isinstance(f, dict) else f
    if native not in emulation.EMULATED_ON:
        return
    for e in entries:
        if e.state == INSTALLED and e.source in ("workload", "library"):
            arch = emulation.program_arch(e.ref, read or emulation.exe_arch)
            e.emulated = emulation.is_emulated(native, arch)


def build_catalog(workloads, benchmarks, games, environment: Optional[dict], read_arch=None) -> List[TestEntry]:
    """Every test, in group order then name. Ids are unique: a later source never repeats an id."""
    wl = workload_entries(workloads, environment)
    covered = [w.ref.script.get("steam_app") for w in wl
               if isinstance(getattr(w.ref, "script", None), dict) and w.ref.script.get("steam_app")]
    workload_ids = {w.id for w in wl}
    library = [e for e in library_entries(benchmarks) if not automated_copy(e, workload_ids)]
    seen, out = set(), []
    for e in wl + library + steam_entries(games, environment, covered):
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    mark_emulated(out, environment, read_arch)
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    return sorted(out, key=lambda e: (order.get(e.group, len(order)), e.name.lower()))


def automated_copy(entry: TestEntry, workload_ids) -> bool:
    """A typed-score library entry the app already runs by itself (Geekbench 7, the Store's Cinebench 2026)."""
    workload, version = AUTOMATED_COPY.get(entry.id, (None, None))
    if workload not in workload_ids:
        return False
    found = str(getattr(entry.ref, "version", "") or "")
    # not on this computer (no version): the automated test's Install brings it (owner, 2026-10-22: Cinebench still
    # showed "Official download page" in the typed group)
    return version is None or entry.state == MISSING or found.startswith(version)


def counts(entries: List[TestEntry]) -> Dict[str, int]:
    shown = [e for e in entries if e.compatible]
    return {"compatible": len(shown), "installed": sum(e.state == INSTALLED for e in shown),
            "incompatible": len(entries) - len(shown)}

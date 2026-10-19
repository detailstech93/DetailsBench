"""Session manifest: what is needed to reproduce a session and judge comparability.

Built once per session and stored as JSON (table session_manifests). Only the
benchmarks and games actually used in the session are included (privacy).
"""

import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from typing import Iterable, List, Mapping, Optional

MANIFEST_SCHEMA_VERSION = 1
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_INFO = "build_info.json"

DEFAULT_RUN_SETTINGS = {"repetitions": 3, "warmup_runs": 1, "timeout_seconds": 600, "cooldown_seconds": 0}


def git_commit(root: str = _PROJECT_ROOT, frozen_dir: Optional[str] = None) -> Optional[str]:
    """Current commit of the app's own repository, or None (not a repo / git missing).
    The packaged app reads the commit it was built from (build_info.json, written by the PyInstaller
    spec); it never asks git, which could answer for some other repository around the install folder."""
    frozen_dir = frozen_dir or getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        try:
            with open(os.path.join(frozen_dir, BUILD_INFO), encoding="utf-8") as f:
                commit = json.load(f).get("commit")
        except (OSError, ValueError, AttributeError):
            return None
        return commit if isinstance(commit, str) and len(commit) == 40 else None
    try:
        res = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    commit = res.stdout.strip()
    return commit if res.returncode == 0 and len(commit) == 40 else None


def _benchmark_entry(bm: Mapping) -> dict:
    compat = bm.get("compatibility") or {}
    return {
        "name": bm.get("name"),
        "display_name": bm.get("display_name"),
        "version": bm.get("version"),
        "architecture": bm.get("architecture"),
        "executable_path": bm.get("executable_path"),
        "detection_method": bm.get("detection_method"),
        "compatibility": compat.get("status"),
        "comparable_to_native": compat.get("comparable_to_native", True),
        **({"model": bm["model"]} if bm.get("model") else {}),       # AI tools: the model measured
    }


def build_manifest(session: Mapping, run_settings: Optional[Mapping] = None,
                   environment: Optional[Mapping] = None,
                   benchmarks_used: Iterable[Mapping] = (),
                   games_used: Iterable[Mapping] = (),
                   commit: Optional[str] = None,
                   now: Optional[str] = None,
                   conditions: Optional[Mapping] = None) -> dict:
    """Pure builder. `benchmarks_used` are BenchmarkInfo dicts (dataclasses.asdict);
    `games_used` are GameEntry dicts of the games actually run in the session."""
    benchmarks = [_benchmark_entry(b) for b in benchmarks_used]   # iterate once
    settings = dict(DEFAULT_RUN_SETTINGS)
    settings.update(run_settings or {})
    games: List[dict] = [
        {"store": g.get("store"), "app_id": g.get("app_id"), "name": g.get("name"),
         "version": g.get("version"), "platform": g.get("platform"), "runtime": g.get("runtime")}
        for g in games_used
    ]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": now or datetime.now().isoformat(),
        "session": {"id": session.get("id"), "name": session.get("name"),
                    "is_test": bool(session.get("is_test")), "is_manual": bool(session.get("is_manual"))},
        "app": {"version": _app_version(), "git_commit": commit, "python": platform.python_version()},
        "run_settings": settings,
        "environment": dict(environment) if environment else None,
        "benchmarks": benchmarks,
        "games": games,
        # Results from different runtimes (Windows vs Linux, Proton versions) or emulated
        # executables are not directly comparable; consumers must check these fields.
        "comparability": {
            "runtimes": sorted({g["runtime"] for g in games if g.get("runtime")}),
            "any_emulated": any(not b["comparable_to_native"] for b in benchmarks),
        },
        # what the PC was doing right before the run (app/environment/conditions.py); None when not read
        "conditions": dict(conditions) if conditions else None,
    }


def _app_version() -> str:
    from app import __version__
    return __version__

#!/usr/bin/env python
"""
Print run statistics and session manifests from the production database.

Shows, per session and benchmark: included runs, mean, median, min, max,
sample std dev, CV %, excluded runs by reason and the source (manual/real),
plus a short manifest summary per session. Test data is hidden unless
--include-test is given. Nothing is written except the additive schema
migrations the app itself applies when it opens the database.

Usage:
    python scripts/verify_stats.py [--include-test]
"""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.database import Database
from app.paths import resolve_database_path
from app.stats import summarize_runs


def _fmt(value, digits=2):
    return "-" if value is None else f"{value:,.{digits}f}"


def main() -> int:
    include_test = "--include-test" in sys.argv
    path, _ = resolve_database_path()
    db = Database(str(path))
    print(f"Database: {path}  (test data {'included' if include_test else 'hidden'})\n")

    groups = {}
    for run in db.get_runs(include_test=include_test):
        groups.setdefault(run["session_id"], {}).setdefault(run["benchmark_name"], []).append(run)
    if not groups:
        print("No runs recorded yet. Record results in the Run Benchmarks tab.")
        return 0

    for sid in sorted(groups):
        manifest = db.get_session_manifest(sid)
        print(f"== Session #{sid}")
        if manifest:
            env = manifest.get("environment") or {}
            os_name = (env.get("os_name") or {}).get("value")
            print(f"   manifest: app {manifest['app']['version']} commit {str(manifest['app']['git_commit'])[:10]}, "
                  f"OS {os_name}, settings {manifest['run_settings']}")
        else:
            print("   manifest: none (session created before Phase 4A or outside the app)")
        for name, runs in sorted(groups[sid].items()):
            s = summarize_runs(runs, include_test=include_test)
            st = s["stats"]
            excluded = ", ".join(f"{k} {v}" for k, v in s["excluded"].items() if v) or "none"
            source = "mixed" if s["mixed_sources"] else ("manual" if s["manual_included"] else "real")
            print(f"   {name:<14} runs={s['included']}  mean={_fmt(st['mean'])}  median={_fmt(st['median'])}  "
                  f"min={_fmt(st['min'])}  max={_fmt(st['max'])}  sd={_fmt(st['stdev'])}  "
                  f"cv%={_fmt(st['cv_percent'], 1)}  excluded: {excluded}  source={source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

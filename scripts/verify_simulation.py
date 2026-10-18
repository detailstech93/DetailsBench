"""Real-machine check (run manually): run each simulation script in data/scripts once and print what happened.

Keys are typed into the program's window: do not touch the keyboard or mouse while it runs.
    .venv\\Scripts\\python.exe scripts\\verify_simulation.py [script_id ...]
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.automation.workload import RunContext  # noqa: E402
from app.simulation.script import CONTROLS_FILE, LOG_FILE, load_scripts  # noqa: E402


def main(ids):
    workloads, errors = load_scripts()
    for e in errors:
        print("SCRIPT ERROR", e)
    for wl in workloads:
        if ids and wl.id not in ids:
            continue
        print(f"== {wl.id}: {wl.availability()}")
        if not wl.availability()[0]:
            continue
        out = Path(tempfile.mkdtemp(prefix="pcbm_sim_"))
        result = wl.run(RunContext(output_dir=out, timeout_seconds=wl.timeout_seconds))
        print("status", result.status, "score", result.score, result.unit, "error", result.error)
        print("duration", round(result.duration_seconds or 0, 1), "s")
        for k, v in result.metrics.items():
            print("  ", k, v)
        print((out / LOG_FILE).read_text(encoding="utf-8"))
        print("controls saved:", (out / CONTROLS_FILE).exists(), out)


if __name__ == "__main__":
    main(sys.argv[1:])

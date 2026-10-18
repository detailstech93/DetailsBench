"""Real check of app/storage/disk_tools.py: the four tests with DiskSpd and with fio on this computer.

Writes a 1 GiB test file into the given folder (deleted after each tool). Run:
python scripts/verify_storage.py <output folder>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.automation.workload import RunContext  # noqa: E402
from app.storage.disk_tools import REPORT_FILE, storage_workloads  # noqa: E402


def main() -> int:
    root = Path(sys.argv[1])
    failed = 0
    for workload in storage_workloads():
        folder = root / workload.id
        folder.mkdir(parents=True, exist_ok=True)
        print("==", workload.display_name, workload.availability())
        result = workload.run(RunContext(output_dir=folder, timeout_seconds=300))
        print("status:", result.status, "score:", result.score, result.unit, "error:", result.error)
        print("metrics:", json.dumps(result.metrics))
        report = folder / REPORT_FILE
        print(report.read_text(encoding="utf-8") if report.exists() else "no report")
        failed += result.status != "completed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

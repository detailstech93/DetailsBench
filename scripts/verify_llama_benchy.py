"""Real check of app/ai/llama_benchy.py: one short llama-benchy run against LM Studio
on this computer (depths 0 and 2048, concurrency 1 and 2, prefix caching), then its report.

Do not run while LM Studio is used for something else (for example a local coding agent).
Run: python scripts/verify_llama_benchy.py <model id> <output folder>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.llama_benchy import LlamaBenchyWorkload  # noqa: E402
from app.automation.workload import RunContext  # noqa: E402


def main() -> int:
    model, folder = sys.argv[1], Path(sys.argv[2])
    folder.mkdir(parents=True, exist_ok=True)
    workload = LlamaBenchyWorkload(model, pp=256, tg=32, runs=1, depths=[0, 2048], concurrency=[1, 2],
                                   prefix_caching=True)
    print("command:", workload.describe_command())
    result = workload.run(RunContext(output_dir=folder, timeout_seconds=600))
    print("status:", result.status, "score:", result.score, result.unit, "error:", result.error)
    print("metrics:", json.dumps({k: v for k, v in result.metrics.items()}))
    print("ai_run.json:", (folder / "ai_run.json").read_text(encoding="utf-8") if (folder / "ai_run.json").exists() else None)
    report = folder / "llama_benchy_report.md"
    print(report.read_text(encoding="utf-8") if report.exists() else "no report")
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())

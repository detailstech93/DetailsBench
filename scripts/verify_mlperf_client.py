"""Real check of app/ai/mlperf_client.py: one MLPerf Client run (default config phi4mini, llama.cpp CUDA).

The first run downloads the model (2.33 GB) into the data folder. Keep the GPU free meanwhile.
Run: python scripts/verify_mlperf_client.py <output folder>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.mlperf_client import MlperfClientWorkload  # noqa: E402
from app.automation.workload import RunContext  # noqa: E402


def main() -> int:
    folder = Path(sys.argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    workload = MlperfClientWorkload()
    print("command:", workload.describe_command())
    result = workload.run(RunContext(output_dir=folder, timeout_seconds=3600))
    print("status:", result.status, "score:", result.score, result.unit, "error:", result.error)
    print("metrics:", json.dumps({k: v for k, v in result.metrics.items()}))
    print("ai_run.json:", (folder / "ai_run.json").read_text(encoding="utf-8") if (folder / "ai_run.json").exists() else None)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())

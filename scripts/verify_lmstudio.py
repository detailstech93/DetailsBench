"""Real, read-only check of app/ai/lmstudio.py on this computer: models, selected engines, one run record.

Nothing is loaded or changed in LM Studio. Run: python scripts/verify_lmstudio.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import lmstudio  # noqa: E402


def main() -> int:
    try:
        models = lmstudio.read_models()
    except (OSError, ValueError) as e:
        print("LM Studio server: not reachable:", e)
        return 1
    lms = lmstudio.lms_path()
    runtimes = lmstudio.read_runtimes(lms)
    print("lms:", lms)
    print("selected engines:", json.dumps(runtimes))
    for m in models:
        print(json.dumps(lmstudio.run_record(m, runtimes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

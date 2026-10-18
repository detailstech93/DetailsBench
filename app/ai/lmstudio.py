"""What LM Studio serves: models (quantization, architecture, format, context) and the selected engine per format.

AI results measure the software, the model and the hardware together, so every AI run records the model,
its quantization, the backend (LM Studio's selected llama.cpp engine and version) and the context length.
Read only: LM Studio's local REST API (GET /api/v0/models, shape read on this computer on 2026-10-18) and
`lms runtime ls` (the CLI's table; the selected engine is marked with a check sign). Nothing is loaded,
unloaded or changed. A value LM Studio does not report is absent, never guessed.
"""

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

DEFAULT_URL = "http://localhost:1234"
FIELDS = ("id", "type", "publisher", "arch", "compatibility_type", "quantization", "state",
          "max_context_length", "loaded_context_length")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_models(text: str) -> List[Dict[str, object]]:
    """Models from /api/v0/models, keeping only the fields above that are present."""
    data = json.loads(text).get("data")
    return [{k: m[k] for k in FIELDS if k in m} for m in data if isinstance(m, dict) and "id" in m] \
        if isinstance(data, list) else []


def parse_runtimes(text: str) -> Dict[str, str]:
    """{model format: selected engine@version} from `lms runtime ls` (e.g. GGUF -> llama.cpp-...-cuda12-avx2@2.60.0)."""
    out: Dict[str, str] = {}
    for line in ANSI.sub("", text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and "@" in parts[0] and "✓" in parts[1:-1]:
            out[parts[-1].upper()] = parts[0]
    return out


def lms_path(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    env = os.environ if env is None else env
    home = env.get("USERPROFILE") or env.get("HOME")
    for name in ("lms.exe", "lms"):
        p = Path(home) / ".lmstudio" / "bin" / name if home else None
        if p and p.is_file():
            return p
    return None


def read_models(base_url: str = DEFAULT_URL, fetch: Optional[Callable[[str], str]] = None) -> List[Dict[str, object]]:
    """Raises OSError/ValueError when LM Studio's server is not reachable or answers something else."""
    def get(url: str) -> str:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode("utf-8")
    return parse_models((fetch or get)(base_url.rstrip("/") + "/api/v0/models"))


def read_runtimes(lms: Optional[Path], run: Callable = subprocess.run) -> Dict[str, str]:
    if lms is None:
        return {}
    try:
        done = run([str(lms), "runtime", "ls"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_runtimes(done.stdout.decode("utf-8", "replace")) if done.returncode == 0 else {}


def run_record(model: Mapping[str, object], runtimes: Mapping[str, str]) -> Dict[str, object]:
    """What an AI run stores about the model it measured (manifest run_settings["ai_model"])."""
    fmt = str(model.get("compatibility_type", "")).upper()
    rec = {"server": "LM Studio", "model": model.get("id"), "quantization": model.get("quantization"),
           "arch": model.get("arch"), "format": fmt or None, "backend": runtimes.get(fmt),
           "context_length": model.get("loaded_context_length"), "max_context_length": model.get("max_context_length")}
    return {k: v for k, v in rec.items() if v is not None}

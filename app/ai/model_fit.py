"""Will an AI model fit before it is downloaded? (owner, 2026-10-21: "if the tool thinks the model won't fit on any
system or architecture it should tell me when I try to download, and I decide").

The fixed tests load the model with every layer on the graphics card (`lms load ... --gpu max`, app/ai/model_set.py)
and a 4096-token context, so the model needs about its file size plus room for the context and LM Studio's runtime;
1.2 x the size is used here (an estimate, said so in the warning). Where it has to fit:

- a graphics card with its own memory (2 GB or more: Windows DXGI's dedicated memory, nvidia-smi's memory.total, or
  amdgpu's mem_info_vram_total on Linux): that memory;
- otherwise (integrated graphics, ARM boards such as the Radxa Dragon Q8B, unified memory): the computer's memory,
  shared with the system and the desktop, so about three quarters of it.

Only a warning: the owner decides whether to download anyway. Nothing is measured here, and an unknown size or
memory gives no warning.
"""

import re
import shutil
import subprocess
import sys
from typing import Callable, Optional

NEEDS = 1.2                 # model file x this = memory it needs loaded with a 4096-token context (estimate)
SHARED_SHARE = 0.75         # of the computer's memory a model may use when the graphics share it
MIN_CARD_GB = 2.0           # less dedicated memory than this is an integrated GPU's carve-out, not a card
SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(GB|GiB|MB|MiB)\b", re.I)


def size_gb(text: str) -> Optional[float]:
    """'17.7 GB' -> 17.7 (as LM Studio lists it); None when no size is given."""
    found = SIZE.search(text or "")
    if not found:
        return None
    value = float(found.group(1))
    return value / 1024 if found.group(2).upper().startswith("M") else value


def _windows_card_gb() -> Optional[float]:
    from app.telemetry.gpu_memory import dxgi_adapters
    sizes = [a.dedicated_total / 1024 ** 3 for a in dxgi_adapters()]
    return max(sizes) if sizes else None


def _linux_card_gb(which: Callable = shutil.which, run: Callable = subprocess.run) -> Optional[float]:
    if which("nvidia-smi"):
        try:
            done = run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=15)
            values = [float(v) for v in (done.stdout or "").split() if v.replace(".", "", 1).isdigit()]
            if values:
                return max(values) / 1024          # MiB
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    from app.telemetry.gpu_memory import linux_reading
    totals = [v[0] for k, v in linux_reading().items() if k.endswith("_dedicated_total")]
    return max(totals) if totals else None


def card_gb(platform: Optional[str] = None) -> Optional[float]:
    """The largest graphics card memory in GB, or None (no card with its own memory, or it cannot be read)."""
    platform = platform or sys.platform
    try:
        found = _windows_card_gb() if platform == "win32" else _linux_card_gb() if platform.startswith("linux") else None
    except Exception:
        return None
    return found if found is not None and found >= MIN_CARD_GB else None


def ram_gb() -> Optional[float]:
    from app.system_info import get_ram_size
    ram = get_ram_size()
    return float(ram[0]) if ram else None


def warning(size: str, ram: Optional[float] = None, card: Optional[float] = None) -> str:
    """'' when the model fits (or its size or the memory is unknown), else why it most likely does not."""
    model = size_gb(size)
    if model is None:
        return ""
    need = model * NEEDS
    if card is not None:
        if need <= card:
            return ""
        return (f"Warning: this model most likely does not fit. The test loads it entirely on the graphics card, "
                f"which has {card:.0f} GB; the model is {model:g} GB and needs about {need:.0f} GB with its context "
                f"(an estimate). It would most likely fail to load and give no score. Download it anyway?")
    if ram is None:
        return ""
    usable = ram * SHARED_SHARE
    if need <= usable:
        return ""
    return (f"Warning: this model most likely does not fit. This computer has {ram:.0f} GB of memory, shared by the "
            f"system and the graphics (about {usable:.0f} GB left for a model); the model is {model:g} GB and needs "
            f"about {need:.0f} GB with its context (an estimate). It would most likely fail to load or be very slow. "
            f"Download it anyway?")


def warning_here(size: str) -> str:
    """warning() with this computer's memory."""
    if size_gb(size) is None:
        return ""
    return warning(size, ram_gb(), card_gb())

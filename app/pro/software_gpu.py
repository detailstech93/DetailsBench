"""Software renderers: a GPU test that drew on one measured the CPU, not the graphics card.

The full run on Linux (WSL, RTX 5090 Laptop, 2026-10-21) gave "completed" scores for Geekbench 7 Vulkan, vkmark and
glmark2 on "llvmpipe (LLVM 20.1.2, 256 bits)", Mesa's CPU renderer: WSL has no Vulkan driver for the card and its
OpenGL fell back too, and GravityMark crashed on it. Such a run is never a GPU score: it is kept as invalid with
the renderer's name. The names are the renderers' own: Mesa llvmpipe / lavapipe / softpipe / swrast, Google
SwiftShader and Windows' Microsoft Basic Render Driver.
"""

import re
from typing import Optional

SOFTWARE = re.compile(r"\b(llvmpipe|lavapipe|softpipe|swrast|SwiftShader|Microsoft Basic Render(?:er)? Driver)\b",
                      re.I)


def software_renderer(text: str) -> Optional[str]:
    """The software renderer named in a GPU test's output, or None."""
    found = SOFTWARE.search(text or "")
    return found.group(1) if found else None


def why(name: str, api: str) -> str:
    return (f"it ran on {name}, a software renderer on the CPU, not on the graphics card: this system has no "
            f"{api} driver for the card (WSL has none for Vulkan; a Linux install with the card's driver has)")

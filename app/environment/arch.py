"""Single source of truth for architecture names: x64, x86, arm64, unknown."""

from typing import Optional

CANONICAL_ARCHES = frozenset({"x64", "x86", "arm64", "unknown"})

_ALIASES = {
    "x64": "x64", "amd64": "x64", "x86_64": "x64", "x86-64": "x64", "64-bit": "x64",
    "x86": "x86", "x32": "x86", "32-bit": "x86", "ia32": "x86",
    "i386": "x86", "i486": "x86", "i586": "x86", "i686": "x86",
    "arm64": "arm64", "arm64ec": "arm64", "aarch64": "arm64", "armv8l": "unknown",
}


def normalize_arch(raw: Optional[str]) -> str:
    """Map any architecture spelling to x64, x86, arm64 or unknown.

    32-bit ARM and anything unrecognized become "unknown" (never guessed).
    """
    if raw is None:
        return "unknown"
    return _ALIASES.get(raw.strip().lower(), "unknown")

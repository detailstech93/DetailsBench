"""Pick the discovery for this OS (the only OS branch in app/discovery)."""

import platform
from typing import Optional

from app.discovery.result import DiscoveryResult


def discover_installed(system: Optional[str] = None) -> DiscoveryResult:
    system = (system or platform.system()).lower()
    if system == "windows":
        from app.discovery.windows import discover_windows
        return discover_windows()
    if system == "linux":
        from app.discovery.linux import discover_linux
        return discover_linux()
    result = DiscoveryResult()
    result.diagnostics.append(f"installed-software discovery not implemented for {system}")
    return result

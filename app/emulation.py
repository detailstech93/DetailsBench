"""Which tests run emulated: an x64 or x86 program on an ARM computer (Windows on ARM runs it through its emulator).

The program's architecture is read from its PE header (app.benchmark_manager.get_pe_architecture), never by
running it. Owner, 2026-10-23: emulated tests get their own colour on the Tests screen and "(emulated)" next to
their name in the report, so native and emulated results are told apart.
"""

import sys
from typing import Callable, Optional

# machine architecture: the program architectures it runs emulated
EMULATED_ON = {"arm64": ("x64", "x86")}


def exe_arch(path) -> str:
    """"x64" / "x86" / "arm64" ... from a Windows program's header, "unknown" elsewhere or when it cannot be read."""
    if sys.platform != "win32" or not path:
        return "unknown"
    from app.benchmark_manager import get_pe_architecture
    return get_pe_architecture(str(path))


def is_emulated(native: Optional[str], arch: Optional[str]) -> bool:
    return arch in EMULATED_ON.get(native or "", ())


def program_arch(ref, read: Callable[[str], str] = exe_arch) -> str:
    """The architecture of the program a test runs: a scanned program's own (the scan read it), else the
    workload's executable read now."""
    arch = getattr(ref, "architecture", None)
    if isinstance(arch, str) and arch not in ("", "unknown"):
        return arch
    path = getattr(ref, "executable_path", None)
    executable = getattr(ref, "executable", None)
    if not path and callable(executable):
        try:
            path = executable()[0]
        except Exception:
            path = None
    return read(path) if path else "unknown"

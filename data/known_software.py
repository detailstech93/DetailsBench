"""Software the app is allowed to look for (data-driven, privacy-preserving).

Discovery reads installed-program lists but keeps ONLY entries matching a
record here; everything else is dropped immediately and never stored,
exported or shown. Benchmark suites reuse the patterns of
benchmark_definitions so there is one source of truth for them.
"""

from __future__ import annotations

import dataclasses
from typing import List

from data.benchmark_definitions import BENCHMARK_DEFINITIONS


@dataclasses.dataclass(frozen=True)
class KnownSoftware:
    id: str
    display_name: str
    kind: str                         # "benchmark" | "browser" | "tool" | "launcher"
    name_patterns: List[str]          # regexes on the installed program's display name
    exclusion_patterns: List[str] = dataclasses.field(default_factory=list)
    linux_packages: List[str] = dataclasses.field(default_factory=list)
    linux_executables: List[str] = dataclasses.field(default_factory=list)
    purpose: str = ""


_OTHER = [
    # Browsers (web benchmarks)
    KnownSoftware("chrome", "Google Chrome", "browser", [r"^Google Chrome$"],
                  linux_packages=["google-chrome-stable"], linux_executables=["google-chrome"]),
    KnownSoftware("edge", "Microsoft Edge", "browser", [r"^Microsoft Edge$"],
                  linux_packages=["microsoft-edge-stable"], linux_executables=["microsoft-edge"]),
    KnownSoftware("firefox", "Mozilla Firefox", "browser", [r"^Mozilla Firefox\b"],
                  linux_packages=["firefox"], linux_executables=["firefox"]),
    KnownSoftware("opera", "Opera", "browser", [r"^Opera( Stable)?\b"], [r"GX"]),
    # Tools planned for later phases
    KnownSoftware("presentmon", "PresentMon", "tool", [r"\bPresentMon\b"], purpose="frame capture (phase 6)"),
    KnownSoftware("hwinfo", "HWiNFO", "tool", [r"^HWiNFO"], purpose="telemetry (phase 7)"),
    KnownSoftware("librehardwaremonitor", "LibreHardwareMonitor", "tool", [r"LibreHardwareMonitor"],
                  purpose="telemetry (phase 7)"),
    KnownSoftware("7zip", "7-Zip", "tool", [r"^7-Zip\b"], linux_packages=["7zip", "p7zip-full"],
                  linux_executables=["7z", "7zz"], purpose="CPU benchmark (phase 10)"),
    KnownSoftware("fio", "fio", "tool", [r"^fio\b"], linux_packages=["fio"], linux_executables=["fio"],
                  purpose="storage benchmark (phase 10)"),
    KnownSoftware("diskspd", "DiskSpd", "tool", [r"\bDiskSpd\b"], purpose="storage benchmark (phase 10)"),
    KnownSoftware("mlperf_client", "MLPerf Client", "tool", [r"\bMLPerf Client\b"],
                  purpose="AI benchmark (phase 9)"),
    # Game launchers (clients only; their game libraries are separate)
    KnownSoftware("steam", "Steam", "launcher", [r"^Steam$"], linux_packages=["steam", "steam-installer"],
                  linux_executables=["steam"]),
    KnownSoftware("epic", "Epic Games Launcher", "launcher", [r"^Epic Games Launcher$"]),
    KnownSoftware("gog_galaxy", "GOG GALAXY", "launcher", [r"^GOG GALAXY"]),
]

KNOWN_SOFTWARE: List[KnownSoftware] = [
    KnownSoftware(d.id, d.display_name, "benchmark", list(d.name_patterns), list(d.exclusion_patterns))
    for d in BENCHMARK_DEFINITIONS if d.category != "web"
] + _OTHER

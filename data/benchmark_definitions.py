"""
Data-driven benchmark definitions.

Every benchmark suite is described by a BenchmarkDefinition record.  No install paths,
executable names, or version numbers are hardcoded in detection code — the generic
discovery routine in benchmark_manager reads these definitions at runtime.

Add a new suite by appending a record to BENCHMARK_DEFINITIONS.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional

# ──────────────────────────────────────────────────────────────────────────────
# Dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class BenchmarkDefinition:
    """Immutable definition for a single benchmark suite."""

    # Identity
    id: str                          # unique key, e.g. "geekbench", "cinebench"
    display_name: str                # human-readable, e.g. "Geekbench"
    category: str                    # "cpu" | "gpu" | "web" | "synthetic"

    # Official page
    official_url: str

    # ── Name-matching patterns ────────────────────────────────────────────────
    # Patterns applied to executable names, Appx package names, registry
    # DisplayName values, and Start Menu entry names.  Each string is a
    # Python regex (compiled case-insensitively at discovery time).
    name_patterns: List[str]

    # Patterns that MUST NOT match — used to exclude sibling products
    # (e.g. "Geekbench AI" when looking for Geekbench).
    exclusion_patterns: List[str]

    # ── Version handling ──────────────────────────────────────────────────────
    # Where the version string is read from.
    #   "pe_version_info"  → File version resource via pefile / PowerShell
    #   "appx_package"     → Get-AppxPackage Version field
    #   "registry"         → Uninstall registry DisplayName or VersionInfo key
    #   "web_page"         → version is a property of the benchmark page, not
    #                        of a local install; the page URL is stored here
    #                        and the version string is recorded at run-time
    version_source: str

    # Normalization rule: "commas_to_dots" (replace ',' with '.', split, rejoin)
    # or "passthrough" (use as-is).
    version_normalization: str

    # Major version policy:
    #   "separate_per_major"  → scores never compared across majors (default)
    #   "single_series"       → all versions are in one series
    #   "web"                 → version is page-specific, no major concept
    major_version_policy: str

    # ── Platform support ──────────────────────────────────────────────────────
    supported_os: List[str]          # e.g. ["windows"]
    supported_architectures: List[str]  # default ["unverified"]

    # ── Discovery hints ───────────────────────────────────────────────────────
    # Which discovery channels to use:
    #   "registry"          → Uninstall keys
    #   "filesystem"        → Program Files pattern scan
    #   "app_paths"         → App Paths registry
    #   "start_menu"        → .lnk shortcuts + Get-StartApps
    #   "appx"              → Get-AppxPackage
    #   "browser"           → browser detection (web benchmarks)
    discovery_channels: List[str]

    # ── License / notes ───────────────────────────────────────────────────────
    license_note: Optional[str] = None

    # For web benchmarks: list of page versions.
    # Each entry: dict with keys:
    #   version  - str, e.g. "3.1"
    #   url      - str, the benchmark page URL
    #   status   - "current" | "superseded"
    #   note     - str, free-form note shown in the UI
    # The first entry with status="current" is the default.
    # official_url is derived from the default version's url (single source of truth).
    benchmark_page_versions: Optional[List[dict]] = None

    # For Cinebench-style products: editions are separate series.
    # When True, the edition string is read from metadata and used as a
    # sub-key so that CB2023 and CB2024 are never mixed.
    editions_are_separate: bool = False


def _derive_official_url(defn: BenchmarkDefinition) -> str:
    """Return the official URL for a benchmark definition.

    For web benchmarks, derive from the default (current) page version.
    For non-web benchmarks, return the official_url field.
    """
    if defn.benchmark_page_versions:
        for v in defn.benchmark_page_versions:
            if v.get("status") == "current":
                return v["url"]
        # fallback: first version
        return defn.benchmark_page_versions[0]["url"]
    return defn.official_url


# ──────────────────────────────────────────────────────────────────────────────
# Definitions — one record per suite
# ──────────────────────────────────────────────────────────────────────────────

BENCHMARK_DEFINITIONS: List[BenchmarkDefinition] = [
    # ── Geekbench ────────────────────────────────────────────────────────────
    BenchmarkDefinition(
        id="geekbench",
        display_name="Geekbench",
        category="cpu",
        official_url="https://www.geekbench.com/",
        name_patterns=[r"Geekbench\s+\d+", r"Geekbench"],
        exclusion_patterns=[r"Geekbench\s+(AI|ML)\b"],
        version_source="pe_version_info",
        version_normalization="commas_to_dots",
        major_version_policy="separate_per_major",
        supported_os=["windows"],
        supported_architectures=["unverified"],
        license_note="Windows GUI requires manual execution; CLI automation needs Pro license",
        discovery_channels=["registry", "filesystem", "app_paths", "start_menu"],
    ),

    # ── Cinebench ────────────────────────────────────────────────────────────
    BenchmarkDefinition(
        id="cinebench",
        display_name="Cinebench",
        category="cpu",
        official_url="https://www.maxon.net/en/cinebench",
        name_patterns=[r"Cinebench"],
        exclusion_patterns=[],
        version_source="appx_package",
        version_normalization="commas_to_dots",
        major_version_policy="separate_per_major",
        supported_os=["windows"],
        supported_architectures=["unverified"],
        license_note=None,
        discovery_channels=["appx", "registry", "start_menu", "filesystem"],
        editions_are_separate=True,
    ),

    # ── 3DMark ───────────────────────────────────────────────────────────────
    BenchmarkDefinition(
        id="d3dmark",
        display_name="3DMark",
        category="gpu",
        official_url="https://www.3dmark.com/",
        name_patterns=[r"3DMark"],
        exclusion_patterns=[],
        version_source="pe_version_info",
        version_normalization="commas_to_dots",
        major_version_policy="separate_per_major",
        supported_os=["windows"],
        supported_architectures=["unverified"],
        license_note=None,
        discovery_channels=["registry", "filesystem", "start_menu"],
    ),

    # ── glmark2 ──────────────────────────────────────────────────────────────
    # glmark2 is not verified for native Windows; only usable via WSL.
    # Discovery is handled by _discover_glmark2() which checks WSL presence.
    BenchmarkDefinition(
        id="glmark2",
        display_name="glmark2",
        category="gpu",
        official_url="https://github.com/glmark2/glmark2",
        name_patterns=[r"glmark2"],
        exclusion_patterns=[],
        version_source="pe_version_info",
        version_normalization="commas_to_dots",
        major_version_policy="single_series",
        supported_os=["wsl"],
        supported_architectures=["unverified"],
        license_note="glmark2 is a Linux OpenGL benchmark; not available natively on Windows",
        discovery_channels=["registry"],  # placeholder — _discover_glmark2 bypasses channels
    ),

    # ── Speedometer ──────────────────────────────────────────────────────────
    # official_url is derived from the default (current) page version via
    # _derive_official_url(); the value here is kept as a stable fallback.
    BenchmarkDefinition(
        id="speedometer",
        display_name="Speedometer",
        category="web",
        official_url="https://browserbench.org/Speedometer3.1/",
        name_patterns=[],  # web benchmarks are not installed
        exclusion_patterns=[],
        version_source="web_page",
        version_normalization="passthrough",
        major_version_policy="web",
        supported_os=["windows"],
        supported_architectures=["unverified"],
        license_note=None,
        discovery_channels=["browser"],
        benchmark_page_versions=[
            {
                "version": "3.1",
                "url": "https://browserbench.org/Speedometer3.1/",
                "status": "current",
                "note": "Current Speedometer benchmark (default).",
            },
            {
                "version": "3.0",
                "url": "https://browserbench.org/Speedometer3.0/",
                "status": "superseded",
                "note": "Superseded by 3.1. Scores are not comparable with 3.1 (treated as a separate series).",
            },
        ],
    ),

    # ── JetStream ────────────────────────────────────────────────────────────
    BenchmarkDefinition(
        id="jetstream",
        display_name="JetStream",
        category="web",
        official_url="https://browserbench.org/JetStream3.0/",
        name_patterns=[],  # web benchmarks are not installed
        exclusion_patterns=[],
        version_source="web_page",
        version_normalization="passthrough",
        major_version_policy="web",
        supported_os=["windows"],
        supported_architectures=["unverified"],
        license_note=None,
        discovery_channels=["browser"],
        benchmark_page_versions=[
            {
                "version": "3.0",
                "url": "https://browserbench.org/JetStream3.0/",
                "status": "current",
                "note": "Current JetStream benchmark.",
            },
        ],
    ),
]


# Convenience lookup
DEFINITIONS_BY_ID: Dict[str, BenchmarkDefinition] = {
    d.id: d for d in BENCHMARK_DEFINITIONS
}

# ──────────────────────────────────────────────────────────────────────────────
# URL validation pattern — all web benchmark page URLs must match this
# ──────────────────────────────────────────────────────────────────────────────
import re as _re

WEB_BENCHMARK_PAGE_URLS_PATTERN = _re.compile(
    r"^https://browserbench\.org/[A-Za-z0-9._/]+$"
)


def get_default_page_version(defn: BenchmarkDefinition) -> Optional[dict]:
    """Return the default (current) page version dict for a web benchmark.

    Falls back to the first version if none is marked current.
    """
    if not defn.benchmark_page_versions:
        return None
    for v in defn.benchmark_page_versions:
        if v.get("status") == "current":
            return v
    return defn.benchmark_page_versions[0]


def get_all_page_urls(defn: BenchmarkDefinition) -> List[str]:
    """Return all configured page URLs for a web benchmark definition."""
    if not defn.benchmark_page_versions:
        return []
    return [v["url"] for v in defn.benchmark_page_versions]

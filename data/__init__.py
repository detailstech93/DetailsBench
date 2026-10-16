"""Benchmark definition data — data-driven discovery configuration."""

from data.benchmark_definitions import (
    BenchmarkDefinition,
    BENCHMARK_DEFINITIONS,
    DEFINITIONS_BY_ID,
    _derive_official_url,
    WEB_BENCHMARK_PAGE_URLS_PATTERN,
)

__all__ = [
    "BenchmarkDefinition",
    "BENCHMARK_DEFINITIONS",
    "DEFINITIONS_BY_ID",
    "_derive_official_url",
    "WEB_BENCHMARK_PAGE_URLS_PATTERN",
]

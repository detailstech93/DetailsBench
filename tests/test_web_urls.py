"""Tests for web benchmark URL configuration.

MOCKED — no network calls. Validates that:
 - Every configured URL matches the expected pattern.
 - No URL is hardcoded outside the definitions data file.
 - Speedometer 3.1 is the default (current) version.
 - Speedometer 3.0 is marked superseded.
 - JetStream 3.0 is at the official URL.
"""

import pytest
import re
import os
import glob

from data.benchmark_definitions import (
    BENCHMARK_DEFINITIONS,
    DEFINITIONS_BY_ID,
    get_default_page_version,
    get_all_page_urls,
    _derive_official_url,
    WEB_BENCHMARK_PAGE_URLS_PATTERN,
)


# ──────────────────────────────────────────────────────────────────────────────
# 1. URL pattern validation
# ──────────────────────────────────────────────────────────────────────────────

class TestWebBenchmarkURLPattern:
    """MOCKED: Every configured URL must match the allowed pattern."""

    def test_speedometer_urls_match_pattern(self):
        defn = DEFINITIONS_BY_ID["speedometer"]
        urls = get_all_page_urls(defn)
        assert len(urls) >= 2, "Speedometer should have at least 2 versions configured"
        for url in urls:
            assert WEB_BENCHMARK_PAGE_URLS_PATTERN.match(url), (
                f"Speedometer URL does not match pattern: {url}"
            )

    def test_jetstream_urls_match_pattern(self):
        defn = DEFINITIONS_BY_ID["jetstream"]
        urls = get_all_page_urls(defn)
        assert len(urls) >= 1, "JetStream should have at least 1 version configured"
        for url in urls:
            assert WEB_BENCHMARK_PAGE_URLS_PATTERN.match(url), (
                f"JetStream URL does not match pattern: {url}"
            )

    def test_no_404_speedometer3_url(self):
        """The old /Speedometer3/ URL returns 404 and must not be configured."""
        defn = DEFINITIONS_BY_ID["speedometer"]
        urls = get_all_page_urls(defn)
        for url in urls:
            assert "/Speedometer3/" not in url, (
                f"Old /Speedometer3/ URL (404) must not be configured: {url}"
            )

    def test_pattern_rejects_non_browserbench(self):
        """The pattern must reject URLs from other domains."""
        assert not WEB_BENCHMARK_PAGE_URLS_PATTERN.match("https://example.com/benchmark")
        assert not WEB_BENCHMARK_PAGE_URLS_PATTERN.match("http://browserbench.org/Speedometer3.1/")
        assert not WEB_BENCHMARK_PAGE_URLS_PATTERN.match("https://browserbench.org/evil<script>")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Version status validation
# ──────────────────────────────────────────────────────────────────────────────

class TestWebBenchmarkVersionStatus:
    """MOCKED: Version status and default selection."""

    def test_speedometer_default_is_3_1(self):
        defn = DEFINITIONS_BY_ID["speedometer"]
        default = get_default_page_version(defn)
        assert default is not None
        assert default["version"] == "3.1"
        assert default["status"] == "current"

    def test_speedometer_3_0_is_superseded(self):
        defn = DEFINITIONS_BY_ID["speedometer"]
        v30 = None
        for v in defn.benchmark_page_versions:
            if v["version"] == "3.0":
                v30 = v
                break
        assert v30 is not None, "Speedometer 3.0 should be configured"
        assert v30["status"] == "superseded"

    def test_speedometer_3_0_note_mentions_superseded(self):
        defn = DEFINITIONS_BY_ID["speedometer"]
        v30 = None
        for v in defn.benchmark_page_versions:
            if v["version"] == "3.0":
                v30 = v
                break
        assert v30 is not None
        assert "superseded" in v30["note"].lower() or "not comparable" in v30["note"].lower()

    def test_jetstream_default_is_3_0(self):
        defn = DEFINITIONS_BY_ID["jetstream"]
        default = get_default_page_version(defn)
        assert default is not None
        assert default["version"] == "3.0"
        assert default["status"] == "current"

    def test_jetstream_url_is_official(self):
        defn = DEFINITIONS_BY_ID["jetstream"]
        default = get_default_page_version(defn)
        assert "JetStream3.0" in default["url"]

    def test_derive_official_url_returns_current(self):
        """_derive_official_url must return the URL of the current version."""
        speed_defn = DEFINITIONS_BY_ID["speedometer"]
        url = _derive_official_url(speed_defn)
        assert "Speedometer3.1" in url

        jet_defn = DEFINITIONS_BY_ID["jetstream"]
        url = _derive_official_url(jet_defn)
        assert "JetStream3.0" in url


# ──────────────────────────────────────────────────────────────────────────────
# 3. No hardcoded URLs outside data file
# ──────────────────────────────────────────────────────────────────────────────

class TestNoHardcodedWebURLs:
    """MOCKED: No web benchmark URL should be hardcoded outside data/benchmark_definitions.py."""

    @pytest.fixture
    def web_urls_from_definitions(self):
        """Collect all web benchmark URLs from the definitions."""
        urls = set()
        for defn in BENCHMARK_DEFINITIONS:
            if defn.category == "web" and defn.benchmark_page_versions:
                for u in get_all_page_urls(defn):
                    urls.add(u)
        # Also include official_url values for web benchmarks
        for defn in BENCHMARK_DEFINITIONS:
            if defn.category == "web":
                urls.add(defn.official_url)
        return urls

    @pytest.fixture
    def source_files_to_check(self):
        """Find all Python source files in app/ and tests/ (not .venv)."""
        files = []
        for pattern in ["app/**/*.py", "tests/**/*.py"]:
            for f in glob.glob(pattern, recursive=True):
                # Exclude the definitions file itself
                if "benchmark_definitions.py" in f:
                    continue
                files.append(f)
        return files

    def test_no_browserbench_url_hardcoded_in_logic(self, web_urls_from_definitions, source_files_to_check):
        """No source file outside benchmark_definitions.py should contain a
        browserbench.org URL string that isn't in the definitions."""
        violations = []
        for filepath in source_files_to_check:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            for url in web_urls_from_definitions:
                if url in content:
                    violations.append(
                        f"{filepath} contains hardcoded URL: {url}"
                    )

            # Also check for the old 404 URL
            if "browserbench.org/Speedometer3/" in content:
                # But not Speedometer3.0 or Speedometer3.1
                if "Speedometer3.0" not in content and "Speedometer3.1" not in content:
                    violations.append(
                        f"{filepath} contains old /Speedometer3/ URL"
                    )

        assert not violations, (
            "Hardcoded web benchmark URLs found outside data file:\n" +
            "\n".join(violations)
        )

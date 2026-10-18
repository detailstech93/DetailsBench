"""
Verify web benchmark URLs configured in benchmark_definitions.py.

Manually run this script to check that every configured URL returns HTTP 200.
Does NOT run as part of pytest (uses real network calls).

Usage:
    python scripts/verify_web_urls.py
"""

import sys
import os
import urllib.request
import urllib.error

# Ensure the project root is on sys.path so data.benchmark_definitions is importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from data.benchmark_definitions import (
    BENCHMARK_DEFINITIONS,
    get_all_page_urls,
)


def check_url(url: str, timeout: int = 15) -> dict:
    """Make a real HTTP GET request and return status info."""
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "PC-Benchmark-Manager/1.0 (URL Verifier)")

        # Use default verifying context (TLS/certificate errors reported as failures)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return {
            "url": url,
            "status_code": resp.getcode(),
            "ok": resp.getcode() == 200,
            "error": None,
        }
    except urllib.error.HTTPError as e:
        return {
            "url": url,
            "status_code": e.code,
            "ok": False,
            "error": f"HTTP {e.code}: {e.reason}",
        }
    except urllib.error.URLError as e:
        return {
            "url": url,
            "status_code": None,
            "ok": False,
            "error": f"URL Error: {e.reason}",
        }
    except Exception as e:
        return {
            "url": url,
            "status_code": None,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }


def main():
    print("=" * 70)
    print("Web Benchmark URL Verification")
    print("=" * 70)

    # Find all web benchmarks
    web_benchmarks = [
        d for d in BENCHMARK_DEFINITIONS
        if d.category == "web" and d.benchmark_page_versions
    ]

    if not web_benchmarks:
        print("No web benchmarks with page versions configured.")
        return

    all_urls = []
    for defn in web_benchmarks:
        urls = get_all_page_urls(defn)
        for u in urls:
            all_urls.append((defn.display_name, defn.id, u))

    print(f"\nFound {len(all_urls)} URL(s) across {len(web_benchmarks)} web benchmark(s).\n")

    results = []
    for display_name, bm_id, url in all_urls:
        print(f"Checking [{display_name}] {url} ...", end=" ", flush=True)
        result = check_url(url)
        results.append(result)
        if result["ok"]:
            print(f"OK (HTTP {result['status_code']})")
        else:
            print(f"FAIL ({result['error']})")

    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)

    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed

    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        code = r["status_code"] if r["status_code"] else "N/A"
        print(f"  [{status}] HTTP {code}  {r['url']}")
        if r["error"]:
            print(f"         Error: {r['error']}")

    print(f"\nTotal: {passed} passed, {failed} failed out of {len(results)}")

    if failed:
        print("\nSome URLs failed. Check connectivity or verify the URLs are correct.")
        sys.exit(1)
    else:
        print("\nAll URLs verified successfully.")
        sys.exit(0)


if __name__ == "__main__":
    main()

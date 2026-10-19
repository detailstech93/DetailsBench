"""PugetBench for Creators (Puget Systems): Photoshop and Premiere Pro, run by the owner, the score typed in.

Puget's user guide and their December 2025 release post (read through search results on 2026-10-19; the site is
behind a bot check): the benchmarks are free for personal use, but command-line (CLI) runs, turning off the upload
of results and local result logging are features of a paid license. So the free version is not automated here:
pressing its buttons for the owner would go around that license (owner's choice, 2026-10-19: typed score).
The free version may upload its results to Puget's site; that is PugetBench's own behaviour, not the app's.

The owner starts PugetBench from the Tests screen, runs Photoshop or Premiere Pro in it, then types the Overall
Score. It is stored as a MANUAL result under one test per application (each its own tile in the report). A test that
waits for a typed score only runs alone, never inside a run of several tests (app/catalog.py).
"""

from typing import Optional

LIBRARY_ID = "pugetbench_creators"          # the scanned program (data/benchmark_definitions.py)
# test id, application shown, unit
TESTS = (("pugetbench_photoshop", "Photoshop", "points"), ("pugetbench_premiere_pro", "Premiere Pro", "points"))
USER_GUIDE_URL = "https://www.pugetsystems.com/pugetbench/creators/user-guide/"


def test_for(application: str) -> Optional[str]:
    return next((tid for tid, app, _ in TESTS if app == application), None)


def note_text(puget_version: Optional[str], app_version: str = "", preset: str = "", note: str = "") -> str:
    """"Key: value" parts joined by "; " (the report's details table reads them)."""
    parts = ["Entered by: you (typed from PugetBench's results screen)",
             f"PugetBench version: {puget_version or 'unknown'}"]
    if app_version.strip():
        parts.append(f"Application version: {app_version.strip()}")
    if preset.strip():
        parts.append(f"Preset: {preset.strip()}")
    if note.strip():
        parts.append(f"Note: {note.strip()}")
    return "; ".join(p.replace(";", ",") for p in parts)

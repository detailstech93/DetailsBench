"""Emulated tests are told apart (owner, 2026-10-23): their own colour on the Tests screen, "(emulated)" in the report."""

from types import SimpleNamespace

from app import emulation
from app.catalog import INSTALLED, MISSING, TestEntry, mark_emulated
from app.report_set import display_names
from app.ui.automation_panel import _arch_fields

ARM = {"native_arch": {"value": "arm64"}}
X64 = {"native_arch": {"value": "x64"}}


def _entry(test_id, ref, state=INSTALLED, source="workload"):
    return TestEntry(test_id, test_id, "cpu", "automatic", state, "", True, "", True, source=source, ref=ref)


class Wl:
    def __init__(self, path):
        self.path = path

    def executable(self):
        return self.path, "known folders"


def test_an_x64_or_x86_program_on_arm_is_emulated():
    assert emulation.is_emulated("arm64", "x64") and emulation.is_emulated("arm64", "x86")
    assert not emulation.is_emulated("arm64", "arm64") and not emulation.is_emulated("arm64", "unknown")
    assert not emulation.is_emulated("x64", "x86")         # x86 on x64 is not emulated (WOW64 runs it natively)


def test_program_arch_prefers_what_the_scan_read_then_the_workloads_executable():
    assert emulation.program_arch(SimpleNamespace(architecture="x64", executable_path="a.exe")) == "x64"
    read = {"fio.exe": "x64"}.get
    assert emulation.program_arch(Wl("fio.exe"), read) == "x64"
    assert emulation.program_arch(Wl(None), read) == "unknown"


def test_installed_tests_are_marked_on_arm_only():
    arches = {"fio.exe": "x64", "7z.exe": "arm64", "missing.exe": "x64"}
    entries = [_entry("fio", Wl("fio.exe")), _entry("sevenzip", Wl("7z.exe")),
               _entry("gone", Wl("missing.exe"), state=MISSING),
               _entry("cinebench", SimpleNamespace(architecture="x64", executable_path="c.exe"), source="library")]
    mark_emulated(entries, ARM, arches.get)
    assert [e.id for e in entries if e.emulated] == ["fio", "cinebench"]
    for e in entries:
        e.emulated = False
    mark_emulated(entries, X64, arches.get)
    assert not any(e.emulated for e in entries)
    mark_emulated(entries, None, arches.get)
    assert not any(e.emulated for e in entries)


def test_the_report_writes_emulated_next_to_the_name():
    manifest = {"benchmarks": [
        {"name": "fio", "display_name": "fio (storage, 4 tests)", **_arch_fields("arm64", "x64")},
        {"name": "sevenzip", "display_name": "7-Zip", **_arch_fields("arm64", "arm64")},
        {"name": "old", "display_name": "Old run"}]}          # a manifest from before: nothing added
    for b in manifest["benchmarks"]:                           # as app/manifest.py stores them
        b["comparable_to_native"] = (b.get("compatibility") or {}).get("comparable_to_native", True)
    names = display_names({"manifest": manifest})
    assert names == {"fio": "fio (storage, 4 tests) (emulated)", "sevenzip": "7-Zip", "old": "Old run"}


def test_the_manifest_keeps_the_architecture_and_the_emulation():
    from app.manifest import build_manifest
    m = build_manifest({"id": 1, "name": "s"}, benchmarks_used=[
        {"name": "fio", "display_name": "fio", **_arch_fields("arm64", "x64")}])
    b = m["benchmarks"][0]
    assert b["architecture"] == "x64" and b["comparable_to_native"] is False
    assert m["comparability"]["any_emulated"] is True

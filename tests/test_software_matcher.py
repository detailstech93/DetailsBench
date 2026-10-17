"""Tests for data/known_software.py and app/discovery/matcher.py (fake entries)."""

from app.discovery.matcher import match_software
from data.known_software import KNOWN_SOFTWARE, KnownSoftware


def e(name, version=None, location=None, source="registry HKLM"):
    return {"name": name, "version": version, "location": location, "source": source}


def ids(matched):
    return [m["id"] for m in matched]


def test_unmatched_software_is_never_returned():
    out = match_software([e("Some Private App"), e("Banking Tool 3"), e("Steam", "2.10.91.91")])
    assert ids(out) == ["steam"]
    assert all("Private" not in m["name"] and "Banking" not in m["name"] for m in out)


def test_geekbench_majors_stay_separate_and_ai_is_excluded():
    out = match_software([e("Geekbench 6", "6.7.1"), e("Geekbench 7"), e("Geekbench AI 1.2")])
    assert [m["name"] for m in out] == ["Geekbench 6", "Geekbench 7"]
    assert {m["kind"] for m in out} == {"benchmark"}


def test_fields_and_blank_values():
    out = match_software([e("7-Zip 24.08 (x64)", " 24.08 ", r"C:\Program Files\7-Zip\ ", "registry HKLM")])
    assert out == [{"id": "7zip", "display_name": "7-Zip", "kind": "tool", "name": "7-Zip 24.08 (x64)",
                    "version": "24.08", "path": r"C:\Program Files\7-Zip\ ".strip(), "source": "registry HKLM"}]
    assert match_software([e("Steam", "", "")])[0]["version"] is None


def test_anchors_avoid_false_positives():
    out = match_software([e("Portfolio fio helper"), e("Steam Link"), e("Opera GX")])
    assert out == []


def test_duplicates_from_two_hives_are_collapsed():
    out = match_software([e("Steam", location="C:\\Steam"), e("Steam", location="c:\\steam", source="HKCU")])
    assert len(out) == 1


def test_empty_patterns_never_match_everything():
    rule = KnownSoftware("x", "X", "tool", [])
    assert match_software([e("Anything")], known=[rule]) == []


def test_every_record_has_valid_kind_and_unique_id():
    assert {k.kind for k in KNOWN_SOFTWARE} <= {"benchmark", "browser", "tool", "launcher"}
    assert len({k.id for k in KNOWN_SOFTWARE}) == len(KNOWN_SOFTWARE)

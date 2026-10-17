"""Session export (JSON / CSV / HTML) from a REAL temporary database."""

import csv
import io
import json

import pytest

from app.database import Database
from app.export import build_session_export, default_filename, export_to_json, write_export

NOW = "2026-10-17T12:00:00"


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "e.db"))


@pytest.fixture
def session(db):
    sid = db.create_session("قبل التحديث, =run", description="before driver", is_manual=True)
    db.create_result(sid, "geekbench_7", "cpu", 2400.0, benchmark_version="7.1.0.0", is_manual=True,
                     is_warmup=True, run_index=0)
    for i, score in enumerate((2500.0, 2510.0, 2490.0), start=1):
        db.create_result(sid, "geekbench_7", "cpu", score, benchmark_version="7.1.0.0", is_manual=True,
                         run_index=i)
    db.create_result(sid, "geekbench_7", "cpu", None, status="failed", is_manual=True, run_index=4,
                     note='crashed, "twice"')
    db.save_session_manifest(sid, {"app": {"git_commit": "a" * 40}, "environment": None})
    return sid


def test_build_export_contents(db, session):
    export = build_session_export(db, session, now=NOW)
    assert export["format"] == "pc-benchmark-manager.session" and export["format_version"] == 1
    assert export["exported_at"] == NOW
    assert export["session"]["name"] == "قبل التحديث, =run" and export["session"]["is_manual"] is True
    assert export["app"]["git_commit"] == "a" * 40
    assert len(export["runs"]) == 5                               # warmup and failed runs are kept, flagged
    assert [r["status"] for r in export["runs"]].count("failed") == 1
    assert export["runs"][0]["is_warmup"] is True
    summary = export["summaries"]["geekbench_7"]
    assert summary["included"] == 3 and summary["stats"]["mean"] == pytest.approx(2500.0)
    assert summary["excluded"]["warmup"] == 1 and summary["excluded"]["not_completed"] == 1


def test_test_session_counts_its_own_runs(db):
    sid = db.create_session("Trial", is_test=True)
    db.create_result(sid, "cinebench", "cpu", 1000.0, is_test=True)
    export = build_session_export(db, sid, now=NOW)
    assert export["session"]["is_test"] is True
    assert export["summaries"]["cinebench"]["included"] == 1


def test_unknown_session(db):
    with pytest.raises(KeyError):
        build_session_export(db, 99)


def test_json_roundtrip_and_nan(db, session, tmp_path):
    export = build_session_export(db, session, now=NOW)
    export["extra"] = float("nan")
    path = write_export(export, tmp_path / "s.json", "json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["extra"] is None
    assert data["session"]["name"] == "قبل التحديث, =run"
    assert data["runs"][4]["score"] is None and data["runs"][4]["note"] == 'crashed, "twice"'
    assert export_to_json(export).endswith("}\n")


def test_csv_file(db, session, tmp_path):
    path = write_export(build_session_export(db, session, now=NOW), tmp_path / "s.csv", "csv")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                       # BOM so Excel reads UTF-8
    assert b"\r\r\n" not in raw
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    assert rows[0][:3] == ["session_id", "session_name", "benchmark_name"]
    assert len(rows) == 6
    assert rows[1][1] == "قبل التحديث, =run"                     # not at cell start: no guard
    assert rows[5][rows[0].index("score")] == "" and rows[5][rows[0].index("status")] == "failed"
    assert rows[5][rows[0].index("note")] == 'crashed, "twice"'


def test_html_file(db, session, tmp_path):
    path = write_export(build_session_export(db, session, now=NOW), tmp_path / "s.html", "html")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert "قبل التحديث" in text and "<script" not in text
    assert not list(tmp_path.glob("*.partial"))


def test_unknown_format(db, session, tmp_path):
    with pytest.raises(ValueError):
        write_export(build_session_export(db, session), tmp_path / "x.pdf", "pdf")


def test_default_filename_is_safe():
    name = default_filename({"session": {"id": 3, "name": 'a/b:c*?"<>| d'}}, "csv")
    assert name == "session_3_a_b_c_______d.csv"
    assert default_filename({}, "json") == "session_None_session.json"

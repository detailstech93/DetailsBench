"""Database backup on REAL temporary SQLite files."""

from datetime import datetime

import pytest

from app.backup import backup_database, default_backup_dir, list_backups, sha256_file, verify_backup
from app.database import Database

NOW = datetime(2026, 10, 17, 12, 30, 5)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "data" / "benchmark_manager.db"
    path.parent.mkdir()
    db = Database(str(path))
    sid = db.create_session("Real", is_manual=True)
    db.create_result(sid, "geekbench_7", "cpu", 2500.0, benchmark_version="7.1.0.0", is_manual=True)
    db.create_result(sid, "geekbench_7", "cpu", None, status="failed", is_manual=True)
    return path


def test_backup_copies_counts_and_writes_sidecar(db_path):
    before = sha256_file(db_path)
    info = backup_database(db_path, now=NOW)
    assert info["path"] == default_backup_dir(db_path) / "benchmark_manager_20261017_123005.db"
    assert info["integrity"] == "ok"
    assert info["counts"] == {"sessions": 1, "results": 2}
    assert info["sha256"] == sha256_file(info["path"])
    sidecar = info["path"].with_name(info["path"].name + ".sha256")
    assert sidecar.read_text(encoding="utf-8") == f"{info['sha256']}  {info['path'].name}\n"
    assert sha256_file(db_path) == before                       # source untouched
    assert not list(info["path"].parent.glob("*.partial"))


def test_backup_never_overwrites(db_path, tmp_path):
    out = tmp_path / "b"
    first = backup_database(db_path, out, now=NOW)["path"]
    first_hash = sha256_file(first)
    second = backup_database(db_path, out, now=NOW)["path"]
    third = backup_database(db_path, out, now=NOW)["path"]
    assert second.name == "benchmark_manager_20261017_123005_2.db"
    assert third.name == "benchmark_manager_20261017_123005_3.db"
    assert sha256_file(first) == first_hash
    assert list_backups(out) == [first, second, third]


def test_backup_restores_data(db_path, tmp_path):
    copy = backup_database(db_path, tmp_path / "b", now=NOW)["path"]
    runs = Database(str(copy)).get_runs(include_test=True)
    assert [(r["score"], r["status"]) for r in runs] == [(2500.0, "completed"), (None, "failed")]


def test_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "nope.db", tmp_path / "b")
    assert not (tmp_path / "b").exists()


def test_verify_backup_detects_tampering_and_missing_sidecar(db_path, tmp_path):
    path = backup_database(db_path, tmp_path / "b", now=NOW)["path"]
    assert verify_backup(path)["sha256_ok"] is True
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text("0" * 64 + "  x\n", encoding="utf-8")
    assert verify_backup(path)["sha256_ok"] is False
    sidecar.unlink()
    result = verify_backup(path)
    assert result["sha256_ok"] is None and result["integrity"] == "ok"


def test_list_backups_missing_dir(tmp_path):
    assert list_backups(tmp_path / "none") == []

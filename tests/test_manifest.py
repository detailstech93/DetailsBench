"""Tests for app/manifest.py and session manifest storage (temp databases only)."""

import json

import pytest

from app.database import Database
from app.manifest import DEFAULT_RUN_SETTINGS, build_manifest, git_commit

GB7 = {"name": "geekbench_7", "display_name": "Geekbench 7", "version": "7.1.0.0", "architecture": "x64",
       "executable_path": r"C:\Program Files\Geekbench 7\Geekbench 7.exe", "detection_method": "registry",
       "compatibility": {"status": "unverified", "reasons": [], "comparable_to_native": True},
       "scan_diagnostics": ["should not be copied"]}
CP = {"store": "steam", "app_id": "1091500", "name": "Cyberpunk 2077", "version": "123", "platform": "windows",
      "runtime": "native", "install_path": r"Z:\lib\common\Cyberpunk 2077", "size_on_disk": 1}


def test_manifest_contents():
    m = build_manifest({"id": 4, "name": "Laptop run", "is_test": 0, "is_manual": 1},
                       {"repetitions": 5}, {"os_name": {"value": "Windows 11"}}, (b for b in [GB7]), [CP],
                       commit="a" * 40, now="2026-10-17T20:00:00")
    assert m["schema_version"] == 1
    assert m["session"] == {"id": 4, "name": "Laptop run", "is_test": False, "is_manual": True}
    assert m["run_settings"] == dict(DEFAULT_RUN_SETTINGS, repetitions=5)
    assert m["app"]["git_commit"] == "a" * 40
    assert m["benchmarks"] == [{"name": "geekbench_7", "display_name": "Geekbench 7", "version": "7.1.0.0",
                                "architecture": "x64", "executable_path": GB7["executable_path"],
                                "detection_method": "registry", "compatibility": "unverified",
                                "comparable_to_native": True}]
    assert m["games"] == [{"store": "steam", "app_id": "1091500", "name": "Cyberpunk 2077", "version": "123",
                           "platform": "windows", "runtime": "native"}]
    assert m["comparability"] == {"runtimes": ["native"], "any_emulated": False}
    assert "install_path" not in json.dumps(m["games"])       # only identifying game fields, no paths
    json.dumps(m)   # serializable


def test_emulated_benchmark_flagged():
    emu = dict(GB7, compatibility={"status": "compatible", "comparable_to_native": False})
    assert build_manifest({"id": 1}, benchmarks_used=[emu])["comparability"]["any_emulated"] is True


def test_git_commit_of_this_repo_or_none(tmp_path):
    assert git_commit(str(tmp_path)) is None            # not a repository
    commit = git_commit()
    assert commit is None or len(commit) == 40


def test_store_once_and_read_back(tmp_path):
    db = Database(str(tmp_path / "m.db"))
    sid = db.create_session("s")
    m = build_manifest({"id": sid, "name": "s"}, now="t", commit=None)
    db.save_session_manifest(sid, m)
    assert db.get_session_manifest(sid) == m
    with pytest.raises(ValueError):
        db.save_session_manifest(sid, m)
    assert db.get_session_manifest(999) is None


def test_packaged_app_reads_the_commit_it_was_built_from(tmp_path):
    (tmp_path / "build_info.json").write_text('{"commit": "' + "a" * 40 + '"}', encoding="utf-8")
    assert git_commit(frozen_dir=str(tmp_path)) == "a" * 40
    (tmp_path / "build_info.json").write_text('{"commit": null}', encoding="utf-8")
    assert git_commit(frozen_dir=str(tmp_path)) is None
    assert git_commit(frozen_dir=str(tmp_path / "missing")) is None      # never falls back to git

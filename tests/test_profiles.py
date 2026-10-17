"""Tests for run profiles (app/profiles.py + Database storage, temp databases only)."""

import pytest

from app.database import Database
from app.manifest import build_manifest
from app.profiles import BUILTIN_PROFILES, RunProfile, validate_profile


def test_builtins_are_valid():
    for p in BUILTIN_PROFILES:
        assert validate_profile(p) == [] and p.builtin


@pytest.mark.parametrize("kwargs, problem", [
    ({"name": " "}, "name is empty"),
    ({"repetitions": 0}, "repetitions"),
    ({"warmup_runs": 6}, "warmup_runs"),
    ({"timeout_seconds": 5}, "timeout_seconds"),
    ({"cooldown_seconds": 3.5}, "cooldown_seconds"),
    ({"repetitions": True}, "repetitions"),
    ({"benchmarks": []}, "benchmarks"),
])
def test_invalid_profiles(kwargs, problem):
    args = dict(name="Mine")
    args.update(kwargs)
    assert any(problem in p for p in validate_profile(RunProfile(**args)))


def test_settings_for_manifest():
    p = RunProfile("Long soak", 5, 2, 1200, 60, ["cinebench"])
    assert p.settings() == {"profile": "Long soak", "repetitions": 5, "warmup_runs": 2,
                            "timeout_seconds": 1200, "cooldown_seconds": 60, "benchmarks": ["cinebench"]}
    assert build_manifest({"id": 1}, p.settings(), now="t")["run_settings"]["profile"] == "Long soak"


def test_store_list_replace_delete(tmp_path):
    db = Database(str(tmp_path / "p.db"))
    assert [p.name for p in db.list_run_profiles()] == [p.name for p in BUILTIN_PROFILES]
    db.save_run_profile(RunProfile("long soak", 5, 2, 1200, 60))
    db.save_run_profile(RunProfile("Another", 2, 0))
    db.save_run_profile(RunProfile("long soak", 6, 2, 1200, 60))      # replace by name
    names = [p.name for p in db.list_run_profiles()]
    assert names == [p.name for p in BUILTIN_PROFILES] + ["Another", "long soak"]
    soak = db.list_run_profiles()[-1]
    assert soak.repetitions == 6 and soak.builtin is False
    assert db.delete_run_profile("Another") is True
    assert db.delete_run_profile("Standard") is False                  # built-ins are not stored


def test_refuses_invalid_and_builtin_names(tmp_path):
    db = Database(str(tmp_path / "p.db"))
    with pytest.raises(ValueError, match="repetitions"):
        db.save_run_profile(RunProfile("Bad", repetitions=0))
    with pytest.raises(ValueError, match="built-in"):
        db.save_run_profile(RunProfile("standard"))

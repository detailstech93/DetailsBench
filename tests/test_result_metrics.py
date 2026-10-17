"""result_metrics table and the runner storing metrics (REAL temporary database)."""

import math

import pytest

from app.automation.runner import AutomationRunner, PlanItem
from app.automation.workload import Workload, WorkloadResult
from app.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "m.db"))
    return database, database.create_session("s")


def test_store_and_read(db):
    database, sid = db
    rid = database.create_result(sid, "game", "game", 120.0, unit="fps")
    assert database.save_result_metrics(rid, {"low_1pct_fps": (80.5, "fps"), "frames": (3600, "frames")}) == 2
    assert database.get_result_metrics(rid) == {"frames": (3600.0, "frames"), "low_1pct_fps": (80.5, "fps")}
    assert database.get_result_metrics(rid + 99) == {}


@pytest.mark.parametrize("value", [math.nan, math.inf, "12", True, None])
def test_non_finite_rejected_and_nothing_stored(db, value):
    database, sid = db
    rid = database.create_result(sid, "game", "game", 120.0, unit="fps")
    with pytest.raises(ValueError):
        database.save_result_metrics(rid, {"ok": (1.0, "fps"), "bad": (value, "fps")})
    assert database.get_result_metrics(rid) == {}


def test_failed_or_missing_run_gets_no_metrics(db):
    database, sid = db
    failed = database.create_result(sid, "game", "game", None, status="failed")
    with pytest.raises(ValueError, match="only completed"):
        database.save_result_metrics(failed, {"a": (1.0, None)})
    with pytest.raises(ValueError, match="does not exist"):
        database.save_result_metrics(9999, {"a": (1.0, None)})


def test_duplicate_name_rejected_atomically(db):
    database, sid = db
    rid = database.create_result(sid, "game", "game", 1.0)
    database.save_result_metrics(rid, {"a": (1.0, None)})
    with pytest.raises(ValueError):
        database.save_result_metrics(rid, {"b": (2.0, None), "a": (3.0, None)})
    assert database.get_result_metrics(rid) == {"a": (1.0, None)}


class Fixed(Workload):
    id = display_name = "fixed"

    def __init__(self, result):
        self.result = result

    def run(self, ctx):
        return self.result


def test_runner_stores_numeric_metrics_of_completed_runs(db, tmp_path):
    database, sid = db
    ok = WorkloadResult(status="completed", score=100.0, unit="fps",
                        metrics={"low_1pct_fps": (70.0, "fps"), "threads": 8, "label": "x", "bad": math.nan})
    AutomationRunner(database, sid, [PlanItem(Fixed(ok), 1, 0)], tmp_path / "o").run()
    rid = database.get_runs(sid)[0]["id"]
    assert database.get_result_metrics(rid) == {"low_1pct_fps": (70.0, "fps"), "threads": (8.0, None)}

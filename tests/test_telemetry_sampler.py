"""Telemetry sampler with fake sources, and its hook in the automation runner (REAL temporary database)."""

import json
import math

import pytest

from app.automation.runner import AutomationRunner, PlanItem
from app.automation.workload import Workload, WorkloadResult
from app.database import Database
from app.telemetry.sampler import TelemetryResult, TelemetrySampler, summarize, write_csv


class Src:
    def __init__(self, name, readings=(), ok=True, reason="", fail=False):
        self.name, self.readings, self.ok, self.reason, self.fail = name, list(readings), ok, reason, fail
        self.reads = 0

    def available(self):
        return self.ok, self.reason

    def read(self):
        self.reads += 1
        if self.fail:
            raise OSError("sensor gone")
        return self.readings.pop(0) if self.readings else {}


def test_unavailable_source_is_reported_and_never_read():
    off = Src("lhm", ok=False, reason="LibreHardwareMonitor web server not reachable")
    s = TelemetrySampler([off]).start()
    s.sample_once()
    res = s.stop()
    assert res.unavailable == {"lhm": "LibreHardwareMonitor web server not reachable"}
    assert off.reads == 0 and res.samples == []


def test_availability_check_that_raises_is_unavailable():
    class Bad(Src):
        def available(self):
            raise RuntimeError("boom")
    res = TelemetrySampler([Bad("x")]).start().stop()
    assert "boom" in res.unavailable["x"]


def test_samples_skip_missing_and_non_finite_values():
    import itertools
    t = itertools.count(0.0, 1.0)
    gpu = Src("nvidia", [{"gpu0_temperature": (60.0, "C"), "gpu0_power": (None, "W")},
                         {"gpu0_temperature": (float("nan"), "C"), "gpu0_power": (100.0, "W")},
                         {"gpu0_temperature": (True, "C")}])
    s = TelemetrySampler([gpu], interval_seconds=1, clock=lambda: next(t))
    s._active = [gpu]                   # driven by hand, no background thread
    for _ in range(3):
        s.sample_once()
    assert s.result.samples == [(0.0, {"gpu0_temperature": (60.0, "C")}), (1.0, {"gpu0_power": (100.0, "W")})]


def test_read_errors_are_counted_not_fatal():
    good = Src("a", [{"k": (1.0, "C")}] * 3)
    bad = Src("b", fail=True)
    s = TelemetrySampler([good, bad])
    s._active = [good, bad]
    s.sample_once()
    s.sample_once()
    assert s.result.errors == {"b": 2} and s.result.last_error == {"b": "sensor gone"}
    assert len(s.result.samples) == 2


def test_background_loop_collects_samples():
    src = Src("a", [{"k": (float(i), "W")} for i in range(1000)])
    s = TelemetrySampler([src], interval_seconds=0.1).start()
    import time
    time.sleep(0.35)
    res = s.stop()
    assert 2 <= len(res.samples) <= 6


def test_summarize_avg_and_max():
    res = TelemetryResult(samples=[(0.0, {"cpu_temp": (50.0, "C")}), (1.0, {"cpu_temp": (70.0, "C"),
                                                                            "gpu_power": (200.0, "W")})])
    out = summarize(res)
    assert out == {"tel_cpu_temp_avg": (60.0, "C"), "tel_cpu_temp_max": (70.0, "C"),
                   "tel_gpu_power_avg": (200.0, "W"), "tel_gpu_power_max": (200.0, "W")}
    assert summarize(TelemetryResult()) == {}


def test_write_csv(tmp_path):
    res = TelemetryResult(samples=[(0.0, {"a": (1.5, "C")}), (1.0, {"b": (2.0, "")})])
    path = write_csv(res, tmp_path / "t.csv")
    assert path.read_text(encoding="utf-8").splitlines() == ["seconds,a [C],b", "0.0,1.5,", "1.0,,2.0"]
    assert write_csv(TelemetryResult(), tmp_path / "none.csv") is None and not (tmp_path / "none.csv").exists()


class OneRun(Workload):
    def __init__(self, result):
        self.id = self.display_name = "tool"
        self.result = result

    def version(self):
        return "1"

    def run(self, ctx):
        return self.result


class FakeSampler:
    def __init__(self, result):
        self.res = result
        self.started = False

    def start(self):
        self.started = True
        return self

    def stop(self):
        return self.res


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    return d, d.create_session("tel")


def test_runner_stores_telemetry_with_workload_metrics(db, tmp_path):
    database, sid = db
    tel = TelemetryResult(samples=[(0.0, {"gpu0_temperature": (60.0, "C")}), (1.0, {"gpu0_temperature": (64.0, "C")})],
                          unavailable={"lhm": "not running"})
    wl = OneRun(WorkloadResult(status="completed", score=5.0, unit="fps", metrics={"low_1pct_fps": (3.0, "fps")}))
    summary = AutomationRunner(database, sid, [PlanItem(wl, 1, 0)], tmp_path / "out",
                               telemetry=lambda: FakeSampler(tel)).run()
    rec = summary.records[0]
    metrics = database.get_result_metrics(rec.result_id)
    assert metrics["tel_gpu0_temperature_max"] == (64.0, "C") and metrics["tel_gpu0_temperature_avg"] == (62.0, "C")
    assert metrics["low_1pct_fps"] == (3.0, "fps")
    assert (rec.run_dir / "telemetry.csv").is_file()
    info = json.loads((rec.run_dir / "telemetry.json").read_text(encoding="utf-8"))
    assert info["unavailable"] == {"lhm": "not running"} and info["samples"] == 2


def test_failed_run_keeps_telemetry_file_but_stores_no_metrics(db, tmp_path):
    database, sid = db
    tel = TelemetryResult(samples=[(0.0, {"cpu_load": (90.0, "%")})])
    wl = OneRun(WorkloadResult(status="failed", error="crash"))
    rec = AutomationRunner(database, sid, [PlanItem(wl, 1, 0)], tmp_path / "out",
                           telemetry=lambda: FakeSampler(tel)).run().records[0]
    assert database.get_result_metrics(rec.result_id) == {}
    assert (rec.run_dir / "telemetry.csv").is_file()
    assert database.get_runs(sid)[0]["score"] is None


def test_broken_telemetry_never_breaks_the_run(db, tmp_path):
    database, sid = db
    progress = []

    def factory():
        raise RuntimeError("no sampler")
    wl = OneRun(WorkloadResult(status="completed", score=1.0, unit="pts"))
    summary = AutomationRunner(database, sid, [PlanItem(wl, 1, 0)], tmp_path / "out",
                               on_progress=progress.append, telemetry=factory).run()
    assert summary.records[0].result.status == "completed"
    assert any("telemetry not started: no sampler" in p for p in progress)
    assert math.isclose(database.get_runs(sid)[0]["score"], 1.0)

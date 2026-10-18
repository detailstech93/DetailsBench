import threading

from app.automation.workload import RunContext
from app.report import render_session_report
from app.stress.workload import StressWorkload, stability


class FakePart:
    """A stress part: does `rate` units per tick (a list: one per tick), makes `errors`, may die at a tick."""

    def __init__(self, clock, rates, errors=0, dies_at=None, workers=None):
        self.clock, self.rates, self.err, self.dies_at = clock, rates, errors, dies_at
        self.started = self.stopped = False
        if workers:
            self.workers = workers

    def start(self):
        self.started = True
        return self

    def _tick(self):
        return int(self.clock.t)

    def read(self):
        done = sum(self.rates[:self._tick()])
        return done, self.err if self._tick() > 3 else 0

    def alive(self):
        dead = self.dies_at is not None and self._tick() >= self.dies_at
        return (0 if dead else self.workers) if hasattr(self, "workers") else not dead

    def stop(self):
        self.stopped = True


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TickEvent(threading.Event):
    """stop_event whose wait() moves the fake clock one second."""

    def __init__(self, clock, stop_at=None):
        super().__init__()
        self.clock, self.stop_at = clock, stop_at

    def wait(self, timeout=None):
        self.clock.t += 1
        return self.stop_at is not None and self.clock.t >= self.stop_at


def run(mode, tmp_path, cpu=None, gpu=None, minutes=1, stop_at=None):
    clock = Clock()
    parts = {"cpu": cpu(clock) if cpu else None, "gpu": gpu(clock) if gpu else None}
    wl = StressWorkload(mode, minutes=lambda: minutes, cpu_factory=lambda: parts["cpu"],
                        gpu_factory=lambda: parts["gpu"], clock=clock)
    result = wl.run(RunContext(output_dir=tmp_path, stop_event=TickEvent(clock, stop_at)))
    return result, parts


def test_stability_is_slowest_over_fastest_window():
    assert stability([100.0] * 60) == 100.0
    assert round(stability([50.0] * 10 + [100.0] * 30 + [90.0] * 20)) == 90      # ramp up skipped, then 10 % drop
    assert stability([1.0] * 5) is None


def test_steady_cpu_is_stable_with_charts(tmp_path):
    result, parts = run("cpu", tmp_path, cpu=lambda c: FakePart(c, [500] * 190, workers=8), minutes=3)
    assert result.status == "completed" and result.score == 100.0 and result.unit == "%"
    assert result.metrics == {"stress_minutes": (3.0, "min"), "cpu_errors": (0.0, "count"),
                              "cpu_stability": (100.0, "%")}
    unit, points = result.series["cpu_work"]
    assert unit == "% of best second" and len(points) == 180 and all(v == 100.0 for _, v in points)
    assert "CPU stable" in result.note and parts["cpu"].stopped


def test_throttling_and_wrong_answers_are_not_stable(tmp_path):
    rates = [100] * 90 + [80] * 100                              # 20 % slower once hot
    result, _ = run("both", tmp_path, cpu=lambda c: FakePart(c, [100] * 190, errors=3, workers=7),
                    gpu=lambda c: FakePart(c, rates), minutes=3)
    assert result.status == "completed" and round(result.score) == 80
    assert result.metrics["cpu_errors"] == (3.0, "count") and result.metrics["gpu_errors"] == (0.0, "count")
    assert "CPU NOT stable" in result.note and "GPU NOT stable" in result.note


def test_a_part_that_dies_fails_the_run_and_everything_stops(tmp_path):
    result, parts = run("both", tmp_path, cpu=lambda c: FakePart(c, [100] * 70, workers=4, dies_at=20),
                        gpu=lambda c: FakePart(c, [10] * 70))
    assert result.status == "failed" and result.score is None and "CPU stress stopped by itself" in result.error
    assert parts["cpu"].stopped and parts["gpu"].stopped


def test_stop_button_cancels(tmp_path):
    result, parts = run("gpu", tmp_path, gpu=lambda c: FakePart(c, [10] * 70), stop_at=5)
    assert result.status == "cancelled" and parts["gpu"].stopped


def test_a_part_that_cannot_start_fails(tmp_path):
    class Broken(FakePart):
        def start(self):
            raise RuntimeError("no GPU for compute")
    result, _ = run("gpu", tmp_path, gpu=lambda c: Broken(c, []))
    assert result.status == "failed" and "no GPU for compute" in result.error


def test_timeout_covers_the_minutes():
    assert StressWorkload("cpu", minutes=lambda: 10).timeout_seconds == 780


def _stress_export(metrics, series, status="completed"):
    run_ = {"benchmark_name": "stress_cpu_gpu", "benchmark_type": "stress", "status": status,
            "score": 99.0 if status == "completed" else None, "unit": "%", "run_index": 1, "is_warmup": False,
            "is_test": False, "note": "Stress: 10 min; CPU stable", "series": series,
            "metrics": {k: {"value": v, "unit": u} for k, (v, u) in metrics.items()}}
    summaries = {"tel_gpu0_power_avg": (150.4, "W"), "tel_gpu0_power_max": (171.0, "W")}
    return {"session": {"id": 1, "name": "s"}, "runs": [run_],
            "metric_summaries": {"stress_cpu_gpu": {k: {"unit": u, "count": 1, "mean": v, "min": v, "max": v,
                                                        "mixed_units": False} for k, (v, u) in summaries.items()}}}


def test_report_stress_verdict_numbers_and_charts():
    series = {"gpu_work": {"unit": "%", "points": [[1, 100], [2, 98], [3, 99]]},
              "tel_gpu0_power": {"unit": "W", "points": [[0.5, 120], [1.5, 160], [2.5, 171]]}}
    page = render_session_report(_stress_export({"cpu_stability": (99.2, "%"), "cpu_errors": (0, "count"),
                                                 "gpu_stability": (98.0, "%"), "gpu_errors": (0, "count")}, series))
    part = page[page.index("<h2>Stress tests"):]
    assert "CPU + GPU stress test" in part and ">Stable</div>" in part and "99 <span>%" in part
    assert "<td>GPU power</td><td>150 W</td><td>171 W</td>" in part
    assert part.count("<svg") == 2 and "Power&#58; GPU" in part
    for bad in ("<script", "http://", "https://", "src=", "@import"):
        assert bad not in page
    unstable = render_session_report(_stress_export({"cpu_stability": (99.0, "%"), "cpu_errors": (2, "count")}, {}))
    assert ">NOT stable</div>" in unstable and "No readings over time" in unstable
    failed = render_session_report(_stress_export({}, {}, status="failed"))
    assert "did not finish" in failed


def test_series_round_trip_only_for_completed_runs(tmp_path):
    import pytest
    from app.database import Database
    db = Database(str(tmp_path / "t.db"))
    sid = db.create_session("s")
    done = db.create_result(sid, "stress_cpu", "stress", 99.0, unit="%", status="completed")
    assert db.save_result_series(done, {"cpu_work": ("%", [(1, 100.0), (2, float("nan")), (3, 98.5)])}) == 1
    assert db.get_result_series(done) == {"cpu_work": {"unit": "%", "points": [[1.0, 100.0], [3.0, 98.5]]}}
    failed = db.create_result(sid, "stress_cpu", "stress", None, unit="%", status="failed")
    with pytest.raises(ValueError):
        db.save_result_series(failed, {"cpu_work": ("%", [(1, 1.0)])})


def test_longer_runs_leave_out_their_first_minute():
    from app.stress.workload import warm_up_seconds
    assert warm_up_seconds(60) == 10 and warm_up_seconds(180) == 60
    work = [50.0] * 60 + [100.0] * 120                        # power shared out in the first minute, then steady
    assert stability(work) == 100.0


def test_stress_tests_can_be_written_in_a_session_manifest():
    # the Tests screen asks every workload for its program when it creates the session
    from app.stress.workload import stress_workloads
    for wl in stress_workloads():
        assert wl.executable() == (None, "built in") and wl.version() is None


def test_a_stress_test_runs_once_without_warm_up_whatever_the_profile():
    from types import SimpleNamespace
    from app.ui.automation_panel import AutomationPanel
    plan = AutomationPanel.build_plan(None, [StressWorkload("cpu", minutes=lambda: 10)],
                                      SimpleNamespace(warmup_runs=1, repetitions=3))
    assert (plan[0].warmup_runs, plan[0].repetitions) == (0, 1)


def test_a_run_under_three_minutes_is_not_judged_unless_it_gave_wrong_answers(tmp_path):
    rates = [100] * 30 + [60] * 40                               # the laptop shares out its power: CPU slows down
    result, _ = run("cpu", tmp_path, cpu=lambda c: FakePart(c, rates, workers=8))
    assert result.metrics["stress_minutes"] == (1.0, "min") and "too short to judge" in result.note
    page = render_session_report(_stress_export({"cpu_stability": (60.0, "%"), "cpu_errors": (0, "count"),
                                                 "stress_minutes": (1.0, "min")}, {}))
    assert "Too short to judge" in page and ">NOT stable</div>" not in page
    wrong = render_session_report(_stress_export({"cpu_stability": (99.0, "%"), "cpu_errors": (1, "count"),
                                                  "stress_minutes": (1.0, "min")}, {}))
    assert ">NOT stable</div>" in wrong

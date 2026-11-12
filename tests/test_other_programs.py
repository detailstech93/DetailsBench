"""Trust fix 3a (owner, 2026-11-12): other programs using the CPU during a run are measured and the test is marked
"Run again" at the top of its report, in the Summary and at the end of the test."""

from types import SimpleNamespace

from app.telemetry.other_programs import OtherProgramsSource


class FakePs:
    def cpu_count(self):
        return 4

    def Process(self, pid):
        return SimpleNamespace(children=lambda recursive: [SimpleNamespace(pid=pid + 1)])


def test_only_programs_that_are_not_the_test_count():
    reads = iter([{0: ("Idle", 0.0), 10: ("app.exe", 0.0), 11: ("child.exe", 0.0), 20: ("game.exe", 0.0),
                   30: ("chrome.exe", 0.0), 31: ("PresentMon-2.exe", 0.0), 32: ("tiny.exe", 0.0)},
                  {0: ("Idle", 2.0), 10: ("app.exe", 1.0), 11: ("child.exe", 1.0), 20: ("game.exe", 3.0),
                   30: ("chrome.exe", 0.4), 31: ("PresentMon-2.exe", 1.0), 32: ("tiny.exe", 0.04)}])
    clock = iter([0.0, 1.0])
    src = OtherProgramsSource(psutil_module=FakePs(), times=lambda: next(reads), foreground=lambda: 20,
                              own_pid=10, clock=lambda: next(clock))
    assert src.available()[0]
    # 4 cores over 1 s: chrome 0.4 s = 10%, tiny 0.04 s = 1% (not named); the app, its child, the game, PresentMon not
    assert src.read() == {"cpu_other": (11.0, "%"), "cpu_other_chrome.exe": (10.0, "%")}


def _run(index, other, named=None, warmup=False):
    metrics = {"tel_cpu_other_avg": {"value": other, "unit": "%"}}
    metrics.update({f"tel_cpu_other_{n}_avg": {"value": v, "unit": "%"} for n, v in (named or {}).items()})
    return {"benchmark_name": "frames_bl3", "run_index": index, "is_warmup": warmup, "status": "completed",
            "score": 113.0, "unit": "FPS", "metrics": metrics}


def _export():
    return {"session": {"id": 1, "name": "s", "created_at": "2026-11-12T10:00:00"},
            "runs": [_run(0, 30.0, warmup=True), _run(1, 1.2), _run(2, 14.3, {"chrome.exe": 9.1, "x.exe": 2.2})]}


def test_a_busy_run_is_named_with_its_programs_and_a_warmup_is_not():
    from app.report_set import busy_runs, run_again_text, test_outcome
    assert busy_runs(_export(), "frames_bl3") == ["run 2: 14% (chrome.exe 9%, x.exe 2%)"]
    assert test_outcome(_export(), "frames_bl3")["busy"] == ["run 2: 14% (chrome.exe 9%, x.exe 2%)"]
    assert run_again_text(_export()).startswith("Run again: other programs were using the CPU")
    clean = dict(_export(), runs=_export()["runs"][:2])
    assert busy_runs(clean, "frames_bl3") == [] and run_again_text(clean) == ""


def test_the_report_and_the_summary_say_run_again_at_the_top():
    from app.report import _header_section, render_summary_report
    assert "Run again: other programs were using the CPU" in _header_section(_export())
    rows = [{"title": "Borderlands 3", "passed": True, "result": "113 FPS", "busy": ["run 2: 14%"]},
            {"title": "Far Cry 6", "passed": True, "result": "131 FPS", "busy": []}]
    html = render_summary_report(_export(), rows)
    top = html[:html.index("</section>")]
    assert "Run again (other programs were using the CPU): Borderlands 3" in top and "Far Cry 6" not in top

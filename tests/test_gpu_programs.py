"""Plan "GPU per program" steps 1-2 (owner, 2026-11-27): other programs' share of the test's graphics card, Windows'
own share kept apart, and a GPU-busy run flagged "Run again"."""

from types import SimpleNamespace as NS

from app.telemetry.gpu_programs import GpuProgramsSource, ModelMemorySource, per_program

REAL_AVAILABLE = ModelMemorySource.available        # taken at import: conftest stubs it for every test

CARD = (0, 0xD1E6)


def _inst(pid, eng, kind, luid="0x00000000_0x0000D1E6"):
    return f"pid_{pid}_luid_{luid}_phys_0_eng_{eng}_engtype_{kind}"


def test_per_program_sums_each_engine_type_and_keeps_the_busiest():
    values = {_inst(20, 0, "3D"): 40.0, _inst(20, 1, "3D"): 30.0, _inst(20, 5, "Copy"): 50.0,
              _inst(30, 0, "VideoDecode"): 4.0, _inst(30, 0, "3D", "0x00000000_0x00001111"): 90.0}  # another card
    assert per_program(values, CARD) == {20: 70.0, 30: 4.0}


class FakeOther:
    def __init__(self):
        self._last = (1.0, {4: ("System", 0), 20: ("game.exe", 0), 30: ("msedge.exe", 0), 31: ("dwm.exe", 0),
                            32: ("tiny.exe", 0), 33: ("PresentMon-2.exe", 0)})
        self._test = {0, 20}

    def _test_pids(self):
        return self._test


def test_the_test_and_windows_own_programs_are_kept_apart():
    reads = iter([{"engine": {}}, {"engine": {_inst(20, 0, "3D"): 95.0, _inst(30, 0, "3D"): 7.5,
                                              _inst(32, 0, "3D"): 1.0, _inst(4, 5, "Copy"): 1.5,
                                              _inst(31, 0, "3D"): 0.1, _inst(33, 0, "3D"): 2.0}}])
    src = GpuProgramsSource(FakeOther(), platform_name="win32",
                            adapters=lambda: [NS(luid=CARD, name="RTX", vendor_id=0x10DE, dedicated_total=8)],
                            counters=lambda: NS(read=lambda: next(reads)))
    assert src.available() == (True, "other programs' share of RTX")
    assert src.read() == {"gpu_other": (8.5, "%"), "gpu_windows": (1.6, "%"), "gpu_other_msedge.exe": (7.5, "%")}
    assert GpuProgramsSource(FakeOther(), platform_name="linux").available()[0] is False


def _run(index, gpu, named=None):
    metrics = {"tel_cpu_other_avg": {"value": 1.0, "unit": "%"}, "tel_gpu_other_avg": {"value": gpu, "unit": "%"},
               "tel_gpu_windows_avg": {"value": 30.0, "unit": "%"}}
    metrics.update({f"tel_gpu_other_{n}_avg": {"value": v, "unit": "%"} for n, v in (named or {}).items()})
    return {"benchmark_name": "frames_bl3", "run_index": index, "is_warmup": False, "status": "completed",
            "score": 113.0, "unit": "FPS", "metrics": metrics}


def test_a_run_with_the_graphics_card_busy_is_run_again():
    from app.report_set import busy_runs, busy_what, run_again_text
    export = {"session": {"id": 1, "name": "s", "created_at": "2026-11-27T10:00:00"},
              "runs": [_run(1, 0.5), _run(2, 9.4, {"msedge.exe": 7.2}), _run(3, 0.6)]}
    assert busy_runs(export, "frames_bl3") == ["run 2: graphics card 9% (msedge.exe 7%), the other runs 1%"]
    assert run_again_text(export).startswith("Run again: other programs were using the graphics card")
    assert busy_what(["run 1: 12%", "run 2: graphics card 9%"]) == "the CPU and the graphics card"
    calm = dict(export, runs=[_run(1, 0.5), _run(2, 4.0), _run(3, 0.6)])     # under GPU_BUSY_PERCENT: not flagged
    assert busy_runs(calm, "frames_bl3") == []
    assert busy_runs(dict(export, runs=[_run(1, 6.0)]), "frames_bl3") == ["run 1: graphics card 6%"]


def test_the_model_program_is_the_candidate_with_the_most_dedicated_memory(monkeypatch):
    from app.telemetry.gpu_programs import model_memory
    monkeypatch.setattr(ModelMemorySource, "available", REAL_AVAILABLE)
    gib = 2 ** 30
    mem = lambda pid, luid="0x00000000_0x0000D1E6": f"pid_{pid}_luid_{luid}_phys_0"
    values = {"dedicated": {mem(10980): 17.08 * gib, mem(1796): 0.55 * gib, mem(50): 1.0 * gib,
                            mem(10980, "0x00000000_0x00001111"): 5 * gib},          # another card
              "shared": {mem(10980): 1.55 * gib}}
    assert model_memory(values, CARD, {10980, 50}) == {"dedicated": 17.08, "shared": 1.55}
    assert model_memory(values, CARD, {7}) == {}                                     # no model program: nothing

    procs = {10980: "llama-server.exe", 1796: "dwm.exe", 50: "chrome.exe"}
    ps = NS(Process=lambda pid: NS(name=lambda: procs[pid], children=lambda recursive: [NS(pid=50)] if pid == 1 else []))
    src = ModelMemorySource(platform_name="win32", own_pid=1, psutil_module=ps,
                            adapters=lambda: [NS(luid=CARD, name="RTX", vendor_id=0x10DE, dedicated_total=8)],
                            counters=lambda: NS(read=lambda: values))
    assert src.available()[0]
    # LM Studio's llama-server is not this app's child, yet it is the model program; chrome is (here) but holds less
    assert src.read() == {"gpu_model_dedicated": (17.08, "GiB"), "gpu_model_shared": (1.55, "GiB")}
    assert ModelMemorySource(platform_name="linux").available()[0] is False


def test_an_ai_test_adds_the_model_reading_and_the_report_shows_it():
    from app.automation.runner import AutomationRunner
    from app.report import model_memory_text
    sampler = NS(sources=[], start=lambda: sampler)
    runner = AutomationRunner.__new__(AutomationRunner)
    runner.telemetry, runner.on_progress = (lambda: sampler), print
    runner._start_telemetry(NS(benchmark_type="ai"))
    assert [s.name for s in sampler.sources] == ["AI model program memory"]
    runner._start_telemetry(NS(benchmark_type="game"))
    assert len(sampler.sources) == 1                                               # games do not get it
    metrics = {"tel_gpu_model_dedicated_max": {"mean": 17.08}, "tel_gpu_model_shared_max": {"mean": 1.55}}
    assert model_memory_text(metrics) == "17.08 GiB on the graphics card, 1.55 GiB in the PC's memory (shared)"
    assert model_memory_text({}) == ""


def test_lm_studios_llama_server_is_the_ai_test_not_another_program():
    # owner 2026-11-27, s162: llama-server was flagged "Run again" (CPU 22%, graphics card 43%) in its own AI test
    other = FakeOther()
    other._last[1][40] = ("llama-server.exe", 0)
    reads = iter([{"engine": {}}, {"engine": {_inst(40, 0, "Compute_0"): 54.0, _inst(30, 0, "3D"): 1.0}}])
    src = GpuProgramsSource(other, platform_name="win32",
                            adapters=lambda: [NS(luid=CARD, name="RTX", vendor_id=0x10DE, dedicated_total=8)],
                            counters=lambda: NS(read=lambda: next(reads)))
    src.available()
    assert src.read()["gpu_other"] == (1.0, "%")
    from app.telemetry.other_programs import OtherProgramsSource
    from tests.test_other_programs import FakePs
    times = iter([{40: ("llama-server.exe", 0.0), 30: ("chrome.exe", 0.0)},
                  {40: ("llama-server.exe", 1.0), 30: ("chrome.exe", 0.4)}])
    clock = iter([0.0, 1.0])
    cpu = OtherProgramsSource(psutil_module=FakePs(), times=lambda: next(times), foreground=lambda: None, own_pid=10,
                              clock=lambda: next(clock), platform_name="win32")
    cpu.available()
    assert cpu.read() == {"cpu_other": (10.0, "%"), "cpu_other_chrome.exe": (10.0, "%")}

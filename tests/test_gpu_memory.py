"""Memory of every GPU and the RAM in AI tests (owner, 2026-10-20), with fakes (nothing read from this PC)."""

import threading

from app.ai.llama_benchy import LlamaBenchyWorkload
from app.automation.runner import AutomationRunner, PlanItem
from app.automation.workload import Workload, WorkloadResult
from app.database import Database
from app.telemetry import gpu_memory as gm
from app.telemetry import sources
from app.telemetry.sampler import TelemetrySampler

GiB = 2 ** 30
NVIDIA = gm.Adapter((0, 50114), "NVIDIA GeForce RTX 5090 Laptop GPU", 0x10DE, 24 * GiB, 76 * GiB)
INTEL = gm.Adapter((0, 55635), "Intel(R) Graphics", 0x8086, 128 * 2 ** 20, 76 * GiB)
BASIC = gm.Adapter((0, 9), "Microsoft Basic Render Driver", 0x1414, 0, 8 * GiB)


def test_instance_names_give_the_luid():
    assert gm.instance_luid("luid_0x00000000_0x0000D953_phys_0") == (0, 0xD953)
    assert gm.instance_luid("luid_0x00000001_0x0000c3c2_phys_1") == (1, 0xC3C2)
    assert gm.instance_luid("_Total") is None and gm.instance_luid("luid_zz_0x1_phys_0") is None


def test_the_biggest_graphics_memory_comes_first_and_the_software_adapter_is_left_out():
    dedicated = {"luid_0x00000000_0x0000D953_phys_0": 0.0, "luid_0x00000000_0x0000C3C2_phys_0": 6 * GiB,
                 "luid_0x00000000_0x00000009_phys_0": 1 * GiB}
    shared = {"luid_0x00000000_0x0000D953_phys_0": 1.5 * GiB, "luid_0x00000000_0x0000C3C2_phys_0": 0.5 * GiB}
    r = gm.windows_reading([INTEL, BASIC, NVIDIA], dedicated, shared)
    assert r["gpumem0_dedicated"] == (6.0, "GiB") and r["gpumem0_dedicated_pct"] == (25.0, "%")
    assert r["gpumem0_shared"] == (0.5, "GiB")
    assert r["gpumem1_dedicated"] == (0.0, "GiB") and r["gpumem1_shared"] == (1.5, "GiB")   # the integrated GPU
    assert not any(k.startswith("gpumem2") for k in r)


def test_an_integrated_gpu_alone_is_gpumem0():
    r = gm.windows_reading([INTEL], {}, {"luid_0x00000000_0x0000D953_phys_0": 2 * GiB})
    assert r == {"gpumem0_shared": (2.0, "GiB")}


def test_linux_amdgpu_files():
    files = {"/d/card0/device/mem_info_vram_total": 16 * GiB, "/d/card0/device/mem_info_vram_used": 4 * GiB,
             "/d/card0/device/mem_info_gtt_used": GiB // 2,
             "/d/card1/device/mem_info_vram_total": 512 * 2 ** 20, "/d/card1/device/mem_info_vram_used": 0}
    r = gm.linux_reading("/d", listdir=lambda root: ["card1", "card0-DP-1", "card0", "renderD128"],
                         read_int=files.get)
    assert r["gpumem0_dedicated"] == (4.0, "GiB") and r["gpumem0_dedicated_pct"] == (25.0, "%")
    assert r["gpumem0_shared"] == (0.5, "GiB") and r["gpumem1_dedicated"] == (0.0, "GiB")
    assert "gpumem1_shared" not in r
    assert gm.linux_reading("/none", listdir=lambda root: (_ for _ in ()).throw(OSError())) == {}


class FakeCounters:
    def read(self):
        return {"dedicated": {"luid_0x00000000_0x0000C3C2_phys_0": 3 * GiB}, "shared": {}}


def test_windows_source_names_the_adapters():
    src = gm.GpuMemorySource("win32", adapters=lambda: [INTEL, NVIDIA, BASIC], counters=FakeCounters)
    ok, what = src.available()
    assert ok and what.startswith("gpumem0: NVIDIA GeForce RTX 5090") and "gpumem1: Intel(R) Graphics" in what
    assert src.read()["gpumem0_dedicated"] == (3.0, "GiB")


def test_sources_say_why_not():
    def broken():
        raise OSError("the counter is not on this PC")
    assert not gm.GpuMemorySource("win32", adapters=lambda: [NVIDIA], counters=broken).available()[0]
    assert not gm.GpuMemorySource("win32", adapters=lambda: [BASIC], counters=FakeCounters).available()[0]
    ok, why = gm.GpuMemorySource("linux", linux=lambda: {}).available()
    assert not ok and "RAM" in why
    ok, why = gm.GpuMemorySource("darwin").available()
    assert not ok and "unified" in why


def test_every_gpu_is_in_the_default_sources():
    import inspect
    assert "GpuMemorySource(platform_name)" in inspect.getsource(sources)     # conftest stubs it


class Mem:
    name = "mem"

    def available(self):
        return True, "fake RAM"

    def read(self):
        return {"ram_used_gib": (12.5, "GiB")}


class Ai(Workload):
    id = display_name = "ai_test"
    benchmark_type = "ai"

    def version(self):
        return "1"

    def run(self, ctx):
        ctx.stop_event.wait(0.3)
        return WorkloadResult(status="completed", score=5.0, unit="tok/s")


class Cpu(Ai):
    id = display_name = "cpu_test"
    benchmark_type = "cpu"


def test_an_ai_test_records_the_memory_with_telemetry_off(tmp_path, monkeypatch):
    started = []

    def sampler(interval_seconds=1.0):
        started.append(1)
        return TelemetrySampler([Mem()], 0.1)
    monkeypatch.setattr(sources, "new_memory_sampler", sampler)
    db = Database(str(tmp_path / "r.db"))
    plan = [PlanItem(Ai(), repetitions=1, warmup_runs=0), PlanItem(Cpu(), repetitions=1, warmup_runs=0)]
    runner = AutomationRunner(db, db.create_session("auto"), plan, tmp_path / "out", telemetry=None,
                              stop_event=threading.Event())
    summary = runner.run()
    assert started == [1]                      # the AI test only
    ai_dir = summary.records[0].run_dir
    assert (ai_dir / "telemetry.csv").read_text(encoding="utf-8").startswith("seconds,ram_used_gib [GiB]")
    assert not (summary.records[1].run_dir / "telemetry.csv").exists()


def test_llama_benchy_is_an_ai_test():
    assert LlamaBenchyWorkload.benchmark_type == "ai"


def test_the_report_charts_every_gpu_and_the_ram():
    from app import report
    names = [n for _, series, _ in report.AI_CHARTS for n in series]
    for key in ("tel_gpu0_vram_used", "tel_gpumem0_dedicated", "tel_gpumem0_shared", "tel_gpumem1_shared",
                "tel_ram_used_gib"):
        assert key in names
    run = {"series": {"tel_gpu0_vram_used": {"points": [[0, 1.0], [1, 2.0]]},
                      "tel_gpumem0_dedicated": {"points": [[0, 1.0], [1, 2.0]]},
                      "tel_gpumem1_dedicated": {"points": [[0, 0.0], [1, 0.0]]},
                      "tel_gpumem1_shared": {"points": [[0, 0.4], [1, 0.6]]},
                      "tel_ram_used_gib": {"points": [[0, 20.0], [1, 21.0]]}}}
    charts = "".join(report._series_charts(run, report.AI_CHARTS))
    assert charts.count('class="chart"') == 3     # VRAM once (not twice), the second GPU's shared memory, RAM
    rows = "".join(report._readings_rows({"tel_ram_used_gib_avg": {"mean": 21.43}, "tel_ram_used_gib_max": {"max": 22.0},
                                          "tel_gpumem0_shared_avg": {"mean": 0.4}, "tel_gpumem0_shared_max": {"max": 0.6}}))
    assert "21.4 GiB" in rows and "0.4 GiB" in rows

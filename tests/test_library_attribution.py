"""Which part each result is filed under (app/library/attribution.py), from made-up parts."""

import pytest

from app.library import attribution as at
from data.test_catalog import TEST_PARTS

LAPTOP = {
    "device": {"maker": "CWL", "model": "CWL", "kind": "laptop", "bios": "1.05", "placeholder": True},
    "cpu": {"name": "Intel(R) Core(TM) Ultra 9 275HX", "cores": 24, "threads": 24},
    "gpus": [{"name": "Intel(R) Graphics", "vendor_id": "8086", "device_id": "7d67", "driver": "32.0.101.9150"},
             {"name": "NVIDIA GeForce RTX 5090 Laptop GPU", "vendor_id": "10de", "device_id": "2c18",
              "subsys_id": "810d1b61", "vram_mb": 24463, "driver": "617.42", "power_limit_w": 175.0}],
    "ram": {"total_gb": 96.0, "modules": [{"size_gb": 48.0, "speed_mts": 5600, "configured_mts": 5600}] * 2},
    "drives": [{"model": "FORESEE XP2300F002T", "size_gb": 2048, "firmware": "X0828BT", "holds_app_data": True},
               {"model": "KINGSTON SNV3S1000G", "size_gb": 1000, "firmware": "S", "holds_app_data": False}],
    "os": {"name": "Microsoft Windows 11 Pro", "build": "26300"},
    "unit_id": "c502abe010156c02",
}
FEDORA = {
    "device": {"maker": "Star Labs", "model": "StarLite", "kind": "laptop", "placeholder": False},
    "cpu": {"name": "Intel(R) Core(TM) 3 N350", "cores": 8, "threads": 8},
    "gpus": [{"name": "Intel Corporation Alder Lake-N [Intel Graphics]", "vendor_id": "8086", "device_id": "46d3",
              "driver_name": "i915", "driver": None}],
    "ram": {"total_gb": 15.4, "modules": []}, "drives": [],
    "os": {"name": "Fedora Linux 44", "kernel": "7.2.11-200.fc44.x86_64"},
}


def test_cpu_test():
    a = at.attribute("cinebench_2026", "cpu", LAPTOP)
    assert (a["part"], a["part_name"], a["library"], a["ranked"]) == ("cpu", "Intel Core Ultra 9 275HX", True, True)
    assert a["part_key"] == "cpu:intel-core-ultra-9-275hx:24c24t"
    assert a["context"] == {"bios": "1.05", "os": "Microsoft Windows 11 Pro 26300"}


def test_gpu_test_takes_the_discrete_card_and_says_so():
    a = at.attribute("blender_benchmark_gpu", "gpu", LAPTOP)
    assert a["part_key"] == "gpu:10de:2c18:810d1b61:24gb" and a["context"]["driver"] == "617.42"
    assert a["variant"] == "NVIDIA GeForce RTX 5090 Laptop GPU @ 175 W"         # laptop GPUs differ by power
    assert "discrete" in a["guessed"][0]


def test_a_cuda_test_needs_no_guess():
    a = at.attribute("vray_benchmark_rtx", "gpu", LAPTOP)
    assert a["part_key"].startswith("gpu:10de") and a["guessed"] == []


def test_in_kernel_linux_driver_is_the_kernel():
    a = at.attribute("glmark2", "gpu", FEDORA)
    assert a["context"]["driver"] == "i915 (kernel 7.2.11-200.fc44.x86_64)" and a["variant"] is None
    assert a["context"]["os"] == "Fedora Linux 44 7.2.11-200.fc44.x86_64"


def test_memory_and_its_linked_cpu():
    a = at.attribute("passmark_memory", "cpu", LAPTOP)
    assert a["part"] == "memory" and a["part_name"] == "96 GB 5600 MT/s (2 x 48 GB)"
    assert [x["part"] for x in a["linked"]] == ["cpu"]
    assert at.attribute("passmark_memory", "cpu", FEDORA)["part_key"] == "memory:15gb:?:?"


def test_storage_is_the_drive_measured():
    a = at.attribute("diskspd", "storage", LAPTOP)
    assert a["part_name"] == "FORESEE XP2300F002T 2048 GB" and a["context"]["firmware"] == "X0828BT"
    b = at.attribute("fio", "storage", LAPTOP, drive=LAPTOP["drives"][1])
    assert b["part_key"] == "storage:kingston-snv3s1000g:1000gb"
    c = at.attribute("fio", "storage", FEDORA)
    assert not c["library"] and c["why_not"] == "no storage found"


def test_system_test_and_an_unnamed_device():
    a = at.attribute("pugetbench_premiere_pro", "gpu", LAPTOP)
    assert a["part"] == "system" and a["part_name"] == "CWL laptop (not named yet)"
    assert a["part_key"].endswith("c502abe010156c02")             # unnamed: this unit only, not every "CWL"
    assert [x["part"] for x in a["linked"]] == ["cpu", "gpu"]
    assert at.attribute("pugetbench_premiere_pro", "gpu", FEDORA)["part_name"] == "Star Labs StarLite"


def test_web_test_keeps_the_browser():
    a = at.attribute("speedometer", "web", LAPTOP, note="Browser: Chrome 155.0.7712.3; Page: x")
    assert a["part"] == "cpu" and a["context"]["browser"] == "Chrome 155.0.7712.3" and a["ranked"]
    b = at.attribute("jetstream", "web", LAPTOP, note="Browser: unknown; Page: x")
    assert b["context"]["browser"] is None and not b["ranked"]


def test_games_wait_for_the_games_section():
    a = at.attribute("cyberpunk2077_builtin_benchmark", "game", LAPTOP)
    assert a["part"] == "gpu" and not a["library"] and "Games section" in a["why_not"]
    assert [x["part"] for x in a["linked"]] == ["cpu"]


@pytest.mark.parametrize("tid,group,why", [("stress_other", "stress", "no known part"), ("selftest_python", "cpu", "own check"),
                                           ("something", "other", "not a test of one part")])
def test_kept_with_the_session(tid, group, why):
    a = at.attribute(tid, group, LAPTOP)
    assert not a["library"] and not a["ranked"] and why in a["why_not"]


def test_stress_tests_go_with_their_part_and_the_device():
    """Owner, 2026-10-24: under load, how much it draws, whether it stays stable, how hot it gets."""
    cpu = at.attribute("stress_cpu", "stress", LAPTOP)
    gpu = at.attribute("stress_gpu", "stress", LAPTOP)
    both = at.attribute("stress_cpu_gpu", "stress", LAPTOP)
    assert (cpu["part"], gpu["part"], both["part"]) == ("cpu", "gpu", "system")
    assert all(a["library"] and not a["ranked"] and a["kind"] == "stress" for a in (cpu, gpu, both))
    assert [x["part"] for x in cpu["linked"]] == ["system"]          # the device's cooling
    assert [x["part"] for x in both["linked"]] == ["cpu", "gpu"]


def test_a_build_is_named_by_its_card_processor_and_date():
    build = dict(LAPTOP, captured_at="2026-10-24T15:00:00",
                 device={"maker": "ASUS", "model": "System Product Name", "kind": "desktop", "placeholder": True})
    a = at.attribute("stress_cpu_gpu", "stress", build)
    assert a["part_name"] == "Build: NVIDIA GeForce RTX 5090 Laptop GPU + Intel Core Ultra 9 275HX (2026-10-24)"
    other = dict(build, cpu={"name": "AMD Ryzen 9 9950X", "cores": 16, "threads": 32})
    assert at.attribute("stress_cpu_gpu", "stress", other)["part_key"] != a["part_key"]   # new processor: new build
    mini = dict(build, device={"maker": "Beelink", "model": "SER9", "kind": "mini PC", "placeholder": False})
    assert at.attribute("stress_cpu_gpu", "stress", mini)["part_name"] == "Beelink SER9"


def test_no_parts_read():
    a = at.attribute("cinebench_2026", "cpu", None)
    assert not a["library"] and "not read" in a["why_not"]


def test_ai_where_it_ran():
    full = at.attribute("llama_benchy_qwen35_4b", "ai", LAPTOP,
                        ai_run={"backend": "llama.cpp-win-x86_64-nvidia-cuda12-avx2@2.6.0"})
    assert full["part"] == "gpu" and full["ai"]["offload"] == "full" and full["ranked"] and full["guessed"] == []
    assert full["context"]["ai_runtime"].startswith("llama.cpp")
    free = at.attribute("llama_benchy", "ai", LAPTOP, ai_run={"backend": "llama.cpp-win-x86_64-vulkan-avx2@2.6.0"})
    assert free["part"] == "gpu" and not free["ranked"] and free["flags"] == ["GPU offload not known"]
    cpu = at.attribute("llama_benchy", "ai", LAPTOP, ai_run={"backend": "llama.cpp-win-x86_64-avx2@2.6.0"})
    assert cpu["part"] == "cpu" and cpu["ranked"]
    npu = at.attribute("mlperf_client", "ai", LAPTOP, ai_run={"tool": "MLPerf Client", "device_type": "NPU"})
    assert npu["part"] == "cpu" and "NPU" in npu["flags"][0]
    gpu = at.attribute("mlperf_client", "ai", LAPTOP, ai_run={"tool": "MLPerf Client", "device_type": "GPU",
                                                              "backend": "ORT GenAI DML"})
    assert gpu["part"] == "gpu" and gpu["ai"]["offload"] == "full"
    none = at.attribute("llama_benchy", "ai", LAPTOP)
    assert not none["library"] and "not recorded" in none["why_not"]


def test_flags_stop_the_ranking():
    env = {"is_wsl": {"value": True}, "is_virtual_machine": {"value": False}}
    cond = {"items": [{"name": "Power", "value": "on battery (80%)"}]}
    flags = at.flags_of(env, cond, emulated=True)
    assert flags == ["WSL", "emulated", "on battery"]
    a = at.attribute("cinebench_2026", "cpu", LAPTOP, flags=flags)
    assert a["library"] and not a["ranked"] and a["flags"] == flags


def test_every_named_test_has_a_known_part():
    assert all(v["part"] in (None, "cpu", "gpu", "memory", "storage", "system") for v in TEST_PARTS.values())

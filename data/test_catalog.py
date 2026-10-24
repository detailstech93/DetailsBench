"""What the Tests screen shows about each test besides what its own source knows.

Keyed by test id (tool spec id, workload id, simulation script id or library definition id).
- group: cpu | gpu | game | storage | ai | web | other
- official_url: the page where the owner downloads the tool; nothing is downloaded from here by the app
- supported_os: OS families ("windows", "linux"); missing = not limited
- supported_architectures: native architecture -> "native" | "emulated" | "unsupported" | "unverified"
- expected_minutes: rough duration of one run, shown as a hint only

A test with no entry here is still listed, with its group from its own benchmark_type and no download link.
"""

from typing import Dict

TEST_INFO: Dict[str, dict] = {
    "sevenzip_benchmark": {
        "group": "cpu", "official_url": "https://www.7-zip.org/download.html",
        "supported_os": ["windows", "linux"],
        "supported_architectures": {"x64": "native", "x86": "native", "arm64": "native"},
        "expected_minutes": 1,
    },
    "sevenzip_fm_benchmark": {
        "group": "cpu", "official_url": "https://www.7-zip.org/download.html",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "x86": "native", "arm64": "native"},
        "expected_minutes": 1,
    },
    "diskspd": {
        "group": "storage", "official_url": "https://github.com/microsoft/diskspd/releases",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "x86": "native", "arm64": "native"},
        "expected_minutes": 3,
    },
    "fio": {
        "group": "storage", "official_url": "https://github.com/axboe/fio/releases",
        "supported_os": ["windows", "linux"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 3,
    },
    "llama_benchy": {
        "group": "ai", "official_url": "https://pypi.org/project/llama-benchy/",
        "expected_minutes": 5,
    },
    "llama_benchy_qwen35_4b": {
        "group": "ai", "official_url": "https://lmstudio.ai/models/qwen3.5",
        "supported_os": ["windows", "linux"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 5,
    },
    "llama_benchy_gemma4_12b": {
        "group": "ai", "official_url": "https://lmstudio.ai/models/gemma-4",
        "supported_os": ["windows", "linux"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 6,
    },
    "llama_benchy_qwen38_27b": {
        "group": "ai", "official_url": "https://lmstudio.ai/models/qwen3.8",
        "supported_os": ["windows", "linux"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 8,
    },
    "mlperf_client": {
        "group": "ai", "official_url": "https://github.com/mlcommons/mlperf_client/releases",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 15,
    },
    "mlperf_client_llama31": {
        "group": "ai", "official_url": "https://github.com/mlcommons/mlperf_client/releases",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 20,
    },
    "mlperf_client_phi4reason": {
        "group": "ai", "official_url": "https://github.com/mlcommons/mlperf_client/releases",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 45,
    },
    "mlperf_client_qwen3": {
        "group": "ai", "official_url": "https://github.com/mlcommons/mlperf_client/releases",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 20,
    },
    "blender_benchmark": {
        "group": "cpu", "official_url": "https://opendata.blender.org/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 10,
    },
    "blender_benchmark_gpu": {
        "group": "gpu", "official_url": "https://opendata.blender.org/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 5,
    },
    "vray_benchmark": {
        "group": "cpu", "official_url": "https://www.chaos.com/vray/benchmark",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 3,
    },
    "vray_benchmark_gpu": {
        "group": "gpu", "official_url": "https://www.chaos.com/vray/benchmark",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 3,
    },
    "vray_benchmark_rtx": {
        "group": "gpu", "official_url": "https://www.chaos.com/vray/benchmark",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 3,
    },
    "passmark_cpu": {
        "group": "cpu", "official_url": "https://www.passmark.com/products/performancetest/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 3,
    },
    "passmark_2d": {
        "group": "gpu", "official_url": "https://www.passmark.com/products/performancetest/",
        "supported_os": ["windows"], "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 3,
    },
    "passmark_3d": {
        "group": "gpu", "official_url": "https://www.passmark.com/products/performancetest/",
        "supported_os": ["windows"], "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 6,
    },
    "passmark_memory": {
        "group": "cpu", "official_url": "https://www.passmark.com/products/performancetest/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 3,
    },
    "cinebench_2026": {
        "group": "cpu", "official_url": "https://www.maxon.net/en/cinebench",
        "supported_os": ["windows"], "supported_architectures": {"x64": "native", "arm64": "native"},
        "expected_minutes": 12,
    },
    "cinebench_2026_single": {
        "group": "cpu", "official_url": "https://www.maxon.net/en/cinebench",
        "supported_os": ["windows"], "supported_architectures": {"x64": "native", "arm64": "native"},
        "expected_minutes": 12,
    },
    "ycruncher_pi_1b": {
        "group": "cpu", "official_url": "https://www.numberworld.org/y-cruncher/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 3,
    },
    "ycruncher_pi_2_5b": {
        "group": "cpu", "official_url": "https://www.numberworld.org/y-cruncher/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 8,
    },
    "gravitymark_vulkan": {
        "group": "gpu", "official_url": "https://gravitymark.tellusim.com/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 4,
    },
    "gravitymark_opengl": {
        "group": "gpu", "official_url": "https://gravitymark.tellusim.com/",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 4,
    },
    "gravitymark_d3d12": {
        "group": "gpu", "official_url": "https://gravitymark.tellusim.com/",
        "supported_os": ["windows"], "supported_architectures": {"x64": "native", "arm64": "unsupported"},
        "expected_minutes": 4,
    },
    "vkmark": {
        "group": "gpu", "official_url": "https://github.com/vkmark/vkmark",
        "supported_os": ["linux"], "supported_architectures": {"x64": "native", "arm64": "native"},
        "expected_minutes": 2,
    },
    "glmark2": {
        "group": "gpu", "official_url": "https://github.com/glmark2/glmark2",
        "supported_os": ["linux"], "supported_architectures": {"x64": "native", "arm64": "native"},
        "expected_minutes": 3,
    },
    "handbrake_x264_1080p": {
        "group": "cpu", "official_url": "https://handbrake.fr/downloads2.php",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "native"},
        "expected_minutes": 4,
    },
    "handbrake_x265_1080p": {
        "group": "cpu", "official_url": "https://handbrake.fr/downloads2.php",
        "supported_os": ["windows", "linux"], "supported_architectures": {"x64": "native", "arm64": "native"},
        "expected_minutes": 12,
    },
    "cyberpunk2077_builtin_benchmark": {
        "group": "game", "official_url": "https://store.steampowered.com/app/1091500/",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 5,
    },
}

STEAM_STORE_URL = "https://store.steampowered.com/app/{app_id}/"


# Which part a result is filed under in the hardware results library (owner, 2026-10-21; app/library/attribution.py).
# A test not named here takes its group's part: cpu -> cpu, gpu -> gpu, storage -> storage, web -> cpu (the browser
# is its "driver"), ai -> decided per run (where the model ran), game -> gpu, stress -> kept with the session only.
# - linked: other parts that change the score, shown next to it (a game is CPU-bound at low settings)
# - api "cuda": runs on an NVIDIA card only, so with two cards that one is meant
TEST_PARTS: Dict[str, dict] = {
    "passmark_memory": {"part": "memory", "linked": ["cpu"]},       # the CPU's memory controller matters
    "pugetbench_photoshop": {"part": "cpu", "linked": ["gpu"]},
    "pugetbench_premiere_pro": {"part": "system", "linked": ["cpu", "gpu"]},   # CPU and GPU together
    "vray_benchmark_gpu": {"part": "gpu", "api": "cuda"},
    "vray_benchmark_rtx": {"part": "gpu", "api": "cuda"},
    "geekbench7_gpu_cuda": {"part": "gpu", "api": "cuda"},
    "d3dmark": {"part": "gpu"},
    "selftest_python": {"part": None},                              # the app's own check, never filed
}
GROUP_PARTS = {"cpu": "cpu", "gpu": "gpu", "storage": "storage", "web": "cpu", "game": "gpu", "ai": "ai",
               "stress": None, "other": None}

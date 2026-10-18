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
    "mlperf_client": {
        "group": "ai", "official_url": "https://github.com/mlcommons/mlperf_client/releases",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 15,
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
    "cyberpunk2077_builtin_benchmark": {
        "group": "game", "official_url": "https://store.steampowered.com/app/1091500/",
        "supported_os": ["windows"],
        "supported_architectures": {"x64": "native", "arm64": "unverified"},
        "expected_minutes": 5,
    },
}

STEAM_STORE_URL = "https://store.steampowered.com/app/{app_id}/"

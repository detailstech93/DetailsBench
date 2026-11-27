"""Owner 2026-11-19: each library package keeps what it ran with: screen, power plan, GPU drivers, AI backend."""
from app.environment import conditions as c
from app.library.compare import run_notes
from app.library.package import run_context, work_curves

REAL_TIMED_CHECK = c.timed_check        # taken at import: conftest stubs it for every test


def test_run_context_from_the_manifest_and_ai_run():
    manifest = {"conditions": {"items": [{"name": "Power plan", "value": "Balanced", "ok": True}],
                               "screen": {"width": 1920, "height": 1080, "refresh_hz": 60.0, "name": "HDMI-A-1"},
                               "other_cpu_idle": 0.4}}
    hw = {"gpus": [{"name": "AMD Radeon(TM) Graphics", "driver": "32.0.21025.10016"},
                   {"name": None, "driver_name": "amdgpu", "driver": None}]}
    ai = {"model": "qwen3.5-4b", "quantization": "Q4_K_M", "backend": "llama.cpp-vulkan@2.73.0", "pp": 512,
          "secret_path": "C:/x"}
    run = run_context(manifest, hw, ai)
    assert run == {"screen": {"width": 1920, "height": 1080, "refresh_hz": 60.0, "name": "HDMI-A-1"},
                   "power_plan": "Balanced", "gpu_drivers": ["AMD Radeon(TM) Graphics 32.0.21025.10016", "amdgpu"],
                   "other_cpu_idle": 0.4,
                   "ai": {"model": "qwen3.5-4b", "quantization": "Q4_K_M", "backend": "llama.cpp-vulkan@2.73.0",
                          "pp": 512}, "cpu_power_limit": None, "mesa": None, "file_system": None}
    # an older session: not recorded, never guessed
    assert run_context({}, {}, None) == {"screen": None, "power_plan": None, "gpu_drivers": None,
                                         "other_cpu_idle": None, "ai": None, "cpu_power_limit": None,
                                         "mesa": None, "file_system": None}


def test_stress_work_curve_goes_into_the_package():
    runs = [{"series": {"cpu_work": {"unit": "% of best second", "points": [[1.0, 99.94], [2.0, 77.21]]},
                        "tel_cpu_load": {"unit": "%", "points": [[1.0, 99.0]]}}}, {"series": {}}]
    assert work_curves(runs) == [{"cpu_work": [[1.0, 99.9], [2.0, 77.2]]}]
    assert work_curves([{"series": {}}]) is None


def test_the_screen_is_read_with_the_conditions():
    timed_check = REAL_TIMED_CHECK
    now = timed_check(idle=lambda: None, screen=lambda: {"width": 2560, "height": 1440}, platform="none",
                        battery=lambda: None, sample=lambda: (1.0, []), temps=lambda: {})
    assert now["screen"] == {"width": 2560, "height": 1440}
    assert "screen" not in timed_check(idle=lambda: None, screen=lambda: None, platform="none",
                                         battery=lambda: None, sample=lambda: (1.0, []), temps=lambda: {})
    assert c.screen_now() is None or "width" in c.screen_now()       # no Qt app in the tests: None


def _pkg(group, screen=None, plan=None, ai=None):
    return {"test": {"group": group}, "run": {"screen": screen, "power_plan": plan, "ai": ai}}


def test_comparison_says_what_ran_differently():
    fhd, qhd = {"width": 1920, "height": 1080}, {"width": 2560, "height": 1440}
    assert run_notes([_pkg("gpu", fhd, "Balanced"), _pkg("gpu", qhd, "Balanced")]) == \
        ["another screen: 1920x1080 / 2560x1440"]
    assert run_notes([_pkg("gpu", fhd), {"test": {"group": "gpu"}}]) == \
        ["screen not recorded for some (older results)"]
    assert run_notes([_pkg("cpu", fhd, "Balanced"), _pkg("cpu", qhd, "High performance")]) == \
        ["another power plan: Balanced / High performance"]
    assert run_notes([_pkg("ai", ai={"backend": "vulkan", "quantization": "Q4_K_M"}),
                      _pkg("ai", ai={"backend": "cpu", "quantization": "Q4_K_M"})]) == \
        ["another AI backend and quantization: cpu Q4_K_M / vulkan Q4_K_M"]
    assert run_notes([{"test": {"group": "gpu"}}, {"test": {"group": "gpu"}}]) == []


def test_the_library_row_shows_what_the_test_ran_with():
    from app.library import view
    pkg = {"attribution": {"part": "gpu", "context": {"driver": "581.80"}},
           "run": {"screen": {"width": 1920, "height": 1080, "refresh_hz": 60.0, "name": "X"},
                   "power_plan": "Balanced", "gpu_drivers": ["RTX 581.80"], "other_cpu_idle": 1.234,
                   "ai": {"backend": "CUDA", "quantization": "Q4_K_M", "model": "m"}}}
    assert view.software(pkg) == ("Driver 581.80; screen 1920x1080 60 Hz; power plan Balanced; "
                                  "other programs 1.2% CPU at rest; backend CUDA; quant Q4_K_M")
    cpu = dict(pkg, attribution={"part": "cpu", "context": {}}, run={"screen": {"width": 1920, "height": 1080}})
    assert view.software(cpu) == "-"                                  # a CPU test: its screen is not shown
    old = {"attribution": {"part": "gpu", "context": {}}, "old_session": True}
    assert view.software(old) == "driver unknown"                     # no run block: nothing guessed


def test_cpu_power_limit_file_system_and_mesa_are_read_without_the_host():
    from types import SimpleNamespace as NS
    from app.environment import conditions as c
    files = {"/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw": "55000000",
             "/sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw": "157000000"}
    def read(path):
        if path not in files:
            raise OSError(path)
        return files[path]
    assert c.cpu_power_limit("linux", read=read) == {"long_w": 55.0, "short_w": 157.0}
    assert c.cpu_power_limit("linux", read=lambda p: (_ for _ in ()).throw(OSError())) is None   # AMD: no RAPL
    query = ("Power Setting GUID: bc5038f7-23e0-4960-96da-33abaf5935ec  (Maximum processor state)\n"
             "Minimum Possible Setting: 0x00000000\nMaximum Possible Setting: 0x00000064\n"
             "Current AC Power Setting Index: 0x00000064\nCurrent DC Power Setting Index: 0x00000050\n")
    assert c.cpu_power_limit("win32", run=lambda: query) == {"max_state_ac": 100, "max_state_dc": 80}
    parts = [NS(mountpoint="/", fstype="btrfs"), NS(mountpoint="/home", fstype="ext4")]
    assert c.file_system("/home/u/.local/share/app", parts) == "ext4"
    assert c.file_system("C:\\Users\\x", [NS(mountpoint="C:\\", fstype="NTFS")]) == "NTFS"
    assert c.mesa_version("linux", run=lambda cmd: "OpenGL version string: 4.6 (Compatibility Profile) Mesa 24.2.8\n"
                          if cmd[0] == "glxinfo" else "") == "24.2.8"
    assert c.mesa_version("linux", run=lambda cmd: "driverInfo = Mesa 25.0.1" if cmd[0] == "vulkaninfo"
                          else (_ for _ in ()).throw(FileNotFoundError())) == "25.0.1"
    assert c.mesa_version("win32") is None
    out = REAL_TIMED_CHECK(idle=lambda: None, screen=lambda: None, power_limit=lambda: {"long_w": 55.0},
                           fs=lambda: "NTFS", mesa=lambda: None, temps=lambda: {})
    assert out["cpu_power_limit"] == {"long_w": 55.0} and out["data_file_system"] == "NTFS" and "mesa" not in out


def test_the_new_fields_show_in_the_library_and_comparisons():
    from app.library import compare, view
    gpu = {"attribution": {"part": "gpu", "context": {}}, "test": {"group": "gpu"},
           "run": {"cpu_power_limit": {"long_w": 55.0, "short_w": 157.0}, "mesa": "24.2.8"}}
    assert view.software(gpu) == "CPU limit 55 W / 157 W; Mesa 24.2.8"
    win = {"attribution": {"part": "cpu", "context": {}}, "test": {"group": "cpu"},
           "run": {"cpu_power_limit": {"max_state_ac": 100, "max_state_dc": 80}}}
    assert view.software(win) == "max processor state 100% (battery 80%)"
    disk = {"attribution": {"part": "drive", "context": {}}, "test": {"group": "storage"},
            "run": {"file_system": "NTFS"}}
    assert view.software(disk) == "file system NTFS"
    other = dict(gpu, run={"cpu_power_limit": {"long_w": 45.0}, "mesa": "25.0.1"})
    notes = compare.run_notes([gpu, other])
    assert "another Mesa: 24.2.8 / 25.0.1" in notes and any(n.startswith("another CPU power limit") for n in notes)
    assert compare.run_notes([dict(disk), dict(disk, run={"file_system": "ext4"})]) == ["another file system: NTFS / ext4"]

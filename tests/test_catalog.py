"""Test catalog for the Tests screen: fake workloads, scan entries and games (never the host)."""

from types import SimpleNamespace

from app.benchmark_manager import BenchmarkInfo, BenchmarkStatus, BenchmarkType
from app.catalog import (ASSISTED, AUTOMATIC, INSTALLED, MANUAL, MISSING, UNUSABLE, build_catalog,
                         check_platform, counts, steam_entries)

WIN_X64 = {"os_family": {"value": "windows", "status": "detected"},
           "native_arch": {"value": "x64", "status": "detected"}}
WIN_ARM = {"os_family": {"value": "windows"}, "native_arch": {"value": "arm64"}}
LINUX = {"os_family": {"value": "linux"}, "native_arch": {"value": "x64"}}


class FakeWorkload:
    def __init__(self, id, available=True, reason="found", benchmark_type="cpu", verified=True,
                 script=None, is_test=False, broken=False):
        self.id, self.display_name, self.benchmark_type = id, id.replace("_", " "), benchmark_type
        self._avail, self.is_test, self.broken = (available, reason), is_test, broken
        if script is None:
            self.spec = SimpleNamespace(verified=verified)
        else:
            self.script = script

    def availability(self):
        if self.broken:
            raise OSError("registry unreadable")
        return self._avail


def by_id(entries):
    return {e.id: e for e in entries}


def test_workloads_states_modes_and_links():
    wls = [FakeWorkload("sevenzip_benchmark"),
           FakeWorkload("diskspd", False, "not found (PCBM_DISKSPD or PATH)", "storage"),
           FakeWorkload("llama_benchy", False, "needs Python, which the packaged app does not include", "ai"),
           FakeWorkload("selftest_python", is_test=True),
           FakeWorkload("cyberpunk2077_builtin_benchmark", benchmark_type="game",
                        script={"verified": False, "steam_app": 1091500, "steps": [{"do": "launch_steam"}]}),
           FakeWorkload("sevenzip_fm_benchmark", script={"verified": True, "steps": [{"do": "keys"}]}),
           FakeWorkload("broken_tool", broken=True)]
    e = by_id(build_catalog(wls, [], [], WIN_X64))
    assert "selftest_python" not in e                       # self-tests are not offered
    assert e["sevenzip_benchmark"].state == INSTALLED and e["sevenzip_benchmark"].can_run()
    assert e["sevenzip_benchmark"].official_url == "https://www.7-zip.org/download.html"
    assert e["diskspd"].state == MISSING and e["diskspd"].group == "storage" and not e["diskspd"].can_run()
    assert e["llama_benchy"].state == UNUSABLE and "needs Python" in e["llama_benchy"].state_reason
    assert e["cyberpunk2077_builtin_benchmark"].mode == ASSISTED       # no keys: the owner starts it
    assert e["cyberpunk2077_builtin_benchmark"].verified is False
    assert e["sevenzip_fm_benchmark"].mode == AUTOMATIC
    assert e["broken_tool"].state == UNUSABLE and "registry unreadable" in e["broken_tool"].state_reason
    assert e["broken_tool"].group == "cpu" and e["broken_tool"].official_url is None


def test_platform_filter():
    assert check_platform({"supported_os": ["windows"]}, LINUX) == (False, "runs on windows only")
    assert check_platform({"supported_architectures": {"arm64": "unsupported"}}, WIN_ARM) == (False, "no arm64 build")
    assert check_platform({"supported_architectures": {"x64": "native"}}, WIN_ARM) == (True, "not checked on arm64")
    assert check_platform({"supported_architectures": {"arm64": "emulated"}}, WIN_ARM)[1] == "runs emulated on arm64"
    assert check_platform({"supported_os": ["windows"]}, None) == (True, "")      # unknown never hides a test
    assert check_platform({}, WIN_X64) == (True, "")
    e = by_id(build_catalog([FakeWorkload("diskspd", benchmark_type="storage")], [], [], LINUX))
    assert e["diskspd"].compatible is False and counts(list(e.values()))["incompatible"] == 1


def test_library_entries():
    bms = [BenchmarkInfo("cinebench", "Cinebench", BenchmarkType.CPU, BenchmarkStatus.READY_MANUAL,
                         executable_path="C:/x/Cinebench.exe", official_url="https://www.maxon.net/en/cinebench"),
           BenchmarkInfo("d3dmark", "3DMark", BenchmarkType.GPU, BenchmarkStatus.NOT_INSTALLED,
                         reason="not found", official_url="https://www.3dmark.com/"),
           BenchmarkInfo("glmark2", "glmark2", BenchmarkType.GPU, BenchmarkStatus.UNSUPPORTED,
                         reason="Linux only"),
           BenchmarkInfo("speedometer", "Speedometer", BenchmarkType.WEB, BenchmarkStatus.READY_MANUAL),
           BenchmarkInfo("geekbench", "Geekbench", BenchmarkType.CPU, BenchmarkStatus.COMPATIBILITY_UNVERIFIED,
                         reason="arm64 not checked")]
    e = by_id(build_catalog([], bms, [], WIN_X64))
    assert e["cinebench"].state == INSTALLED and e["cinebench"].mode == MANUAL
    assert e["d3dmark"].state == MISSING and e["d3dmark"].official_url == "https://www.3dmark.com/"
    assert e["glmark2"].compatible is False and e["glmark2"].compatibility_reason == "Linux only"
    assert e["speedometer"].group == "web" and e["speedometer"].state == INSTALLED
    assert e["geekbench"].state == MISSING and e["geekbench"].compatibility_reason == "arm64 not checked"


def test_steam_games_installed_missing_and_covered_by_a_script():
    games = [{"store": "steam", "app_id": "750920", "name": "SotTR", "installed": True},
             {"store": "steam", "app_id": "397540", "name": "BL3", "installed": False},
             {"store": "steam", "app_id": "1091500", "name": "Cyberpunk", "installed": True}]
    script = FakeWorkload("cyberpunk2077_builtin_benchmark", benchmark_type="game",
                          script={"steam_app": 1091500, "steps": []})
    entries = build_catalog([script], [], games, WIN_X64)
    e = by_id(entries)
    assert e["steam_750920"].state == INSTALLED and e["steam_750920"].mode == ASSISTED
    assert e["steam_397540"].state == MISSING
    assert e["steam_397540"].official_url == "https://store.steampowered.com/app/397540/"
    assert "steam_1091500" not in e                         # the script covers it
    assert "steam_223850" not in e                          # 3DMark on Steam is a tool, not a game
    assert len({x.id for x in entries}) == len(entries)


def test_a_script_can_say_it_is_automatic():
    started = FakeWorkload("cyberpunk2077_builtin_benchmark", benchmark_type="game",
                           script={"mode": "automatic", "steam_app": 1091500, "steps": [{"do": "launch_steam"}]})
    odd = FakeWorkload("odd_script", script={"mode": "sometimes", "steps": [{"do": "keys"}]})
    e = by_id(build_catalog([started, odd], [], [], WIN_X64))
    assert e["cyberpunk2077_builtin_benchmark"].mode == AUTOMATIC     # starts its benchmark by itself
    assert e["odd_script"].mode == AUTOMATIC                          # unknown mode: decided by its steps


def test_games_without_a_built_in_benchmark_are_hidden_for_now():
    games = [{"store": "steam", "app_id": "1593500", "name": "God of War", "installed": True, "kind": "game"},
             {"store": "steam", "app_id": "286690", "name": "Metro 2033 Redux", "installed": True, "kind": "game"}]
    e = by_id(build_catalog([], [], games, WIN_X64))
    assert "steam_1593500" not in e
    assert e["steam_286690"].name == "Metro 2033 Redux (built-in benchmark)" and e["steam_286690"].state == INSTALLED


def test_installed_games_without_a_benchmark_are_gameplay_captures():
    games = [{"store": "steam", "app_id": "1593500", "name": "God of War", "installed": True, "kind": "game"},
             {"store": "steam", "app_id": "292030", "name": "The Witcher 3", "installed": False, "kind": "game"},
             {"store": "steam", "app_id": "1493710", "name": "Proton Experimental", "installed": True,
              "kind": "tool"},
             {"store": "steam", "app_id": "750920", "name": "SotTR", "installed": True, "kind": "game"},
             {"store": "steam", "app_id": "1091500", "name": "Cyberpunk", "installed": True, "kind": "game"}]
    script = FakeWorkload("cyberpunk2077_builtin_benchmark", benchmark_type="game",
                          script={"steam_app": 1091500, "steps": []})
    entries = [x for x in build_catalog([script], [], games, WIN_X64) if x.source != "steam"]
    entries += steam_entries(games, WIN_X64, [1091500], gameplay_captures=True)
    e = by_id(entries)
    gow = e["steam_1593500"]
    assert gow.name == "God of War (gameplay capture)" and gow.group == "game" and gow.mode == ASSISTED
    assert gow.state == INSTALLED and gow.verified is False and gow.source == "steam" and gow.ref is games[0]
    assert "steam_292030" not in e                          # not installed: nothing to capture
    assert "steam_1493710" not in e                         # a Steam tool, never launched
    assert e["steam_750920"].name.endswith("(built-in benchmark)")
    assert "steam_1091500" not in e
    assert len({x.id for x in entries}) == len(entries)


def test_order_is_by_group_then_name():
    wls = [FakeWorkload("fio", benchmark_type="storage"), FakeWorkload("b_cpu"), FakeWorkload("a_cpu"),
           FakeWorkload("mlperf_client", benchmark_type="ai")]
    ids = [x.id for x in build_catalog(wls, [], [], WIN_X64) if x.source == "workload"]
    assert ids == ["a_cpu", "b_cpu", "fio", "mlperf_client"]


def test_a_program_the_app_already_runs_is_not_listed_again_as_typed():
    bms = [BenchmarkInfo("geekbench_7", "Geekbench 7", BenchmarkType.CPU, BenchmarkStatus.READY_MANUAL,
                         executable_path="C:/gb7.exe"),
           BenchmarkInfo("cinebench", "Cinebench", BenchmarkType.CPU, BenchmarkStatus.READY_MANUAL,
                         executable_path="C:/cb.exe", version="2026.1.3.0"),
           BenchmarkInfo("geekbench_6", "Geekbench 6", BenchmarkType.CPU, BenchmarkStatus.READY_MANUAL,
                         executable_path="C:/gb6.exe")]
    wls = [FakeWorkload("geekbench7_cpu"), FakeWorkload("cinebench_2026", available=False, reason="not installed")]
    ids = set(by_id(build_catalog(wls, bms, [], WIN_X64)))
    assert "geekbench_7" not in ids and "cinebench" not in ids and "geekbench_6" in ids
    # without the automated test, or another Cinebench (R23), the typed one stays
    bms[1].version = "23.2.0"
    assert "cinebench" in by_id(build_catalog(wls, bms, [], WIN_X64))
    assert "geekbench_7" in by_id(build_catalog([], bms, [], WIN_X64))

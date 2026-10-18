"""Blender Benchmark workload: parsing the launcher's real --json output and running scenes (no real launcher)."""

import json
from pathlib import Path

from app.automation.process import ProcessOutcome
from app.automation.workload import RunContext
from app.pro import blender_benchmark as bb

DATA = Path(__file__).parent / "data"
# benchmark-launcher-cli 3.3.0, Blender 5.2.0, monster on OPTIX (RTX 5090 Laptop), 2026-10-18
REAL = (DATA / "blender_optix_monster.txt").read_text(encoding="utf-8")


def _cache(tmp_path, version="5.2.0", scenes=bb.SCENES):
    root = tmp_path / "cache" / bb.CACHE_NAME
    for folder, name in [("blender-versions", f"blender-{version}-windows-x64")] + [("scenes", s) for s in scenes]:
        d = root / folder / ("h" + name)
        (d / name).mkdir(parents=True)
        (d / "valid.launcher").write_text("", encoding="utf-8")
    return tmp_path / "cache"


def _exe(tmp_path):
    exe = tmp_path / "tool" / bb.EXE_NAME
    exe.parent.mkdir()
    exe.write_bytes(b"")
    return exe


def test_real_output_is_parsed_past_the_progress_bar():
    spm, metrics, record, error = bb.parse_scene(REAL, "monster")
    assert error is None
    assert spm == 3599.945605507102
    assert metrics["samples_per_minute@monster"] == (3599.946, "samples/min")
    assert metrics["render_time_s@monster"] == (34.998, "s")
    assert record == {"scene": "monster", "blender": "5.2.0 LTS", "launcher": "3.3.0", "device_type": "OPTIX",
                      "devices": "NVIDIA GeForce RTX 5090 Laptop GPU"}


def test_output_without_a_result_is_an_error_not_a_score():
    for text in ("", "10 / 100 [-----] 10.00%", "[{}]", '[{"stats": {"samples_per_minute": 0}}]', "[ {broken"):
        spm, _, _, error = bb.parse_scene(text, "junkshop")
        assert spm is None and error.startswith("junkshop:")


def test_command_is_the_documented_one_and_never_submits():
    cmd = bb.build_command("x.exe", "OPTIX", "classroom")
    assert cmd == ["x.exe", "benchmark", "--blender-version", "5.2.0", "--device-type", "OPTIX", "--json",
                   "classroom"]
    assert "--submit" not in cmd


def test_unavailable_until_blender_and_every_scene_are_downloaded(tmp_path):
    cache = _cache(tmp_path, scenes=("monster", "junkshop"))
    w = bb.BlenderBenchmarkWorkload("CPU", exe=_exe(tmp_path), cache=cache)
    ok, why = w.availability()
    assert not ok and "classroom" in why and "Blender" not in why.split(":")[-1]
    assert bb.missing_downloads(parent=tmp_path / "nothing") == ["Blender 5.2.0", *bb.SCENES]
    assert not bb.BlenderBenchmarkWorkload("CPU", exe=None, cache=cache).availability()[0]


def test_run_sums_the_scenes_and_passes_the_cache_folder(tmp_path, monkeypatch):
    cache, exe, calls = _cache(tmp_path), _exe(tmp_path), []
    rates = {"monster": 100.0, "junkshop": 50.5, "classroom": 20.25}

    def fake(command, out, err, timeout, stop, cwd=None, env=None):
        calls.append((command, cwd, env["LOCALAPPDATA"]))
        scene = command[-1]
        out.write_text("1 / 1 [--] " + json.dumps([{"scene": {"label": scene},
                                                    "stats": {"samples_per_minute": rates[scene]}}]),
                       encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return ProcessOutcome(0, False, False, 2.0)

    monkeypatch.setattr(bb, "run_process", fake)
    w = bb.BlenderBenchmarkWorkload("OPTIX", exe=exe, cache=cache)
    result = w.run(RunContext(output_dir=tmp_path / "run", timeout_seconds=100))
    assert result.status == "completed" and result.score == 170.75 and result.unit == "samples/min"
    assert [c[0][-1] for c in calls] == list(bb.SCENES)
    assert all(c[1] == str(exe.parent) and c[2] == str(cache) for c in calls)
    assert w.id == "blender_benchmark_gpu" and w.benchmark_type == "gpu"
    saved = json.loads((tmp_path / "run" / "blender_run.json").read_text(encoding="utf-8"))
    assert saved["device_type"] == "OPTIX" and len(saved["scenes"]) == 3


def test_a_failed_scene_fails_the_run_without_a_score(tmp_path, monkeypatch):
    cache, exe = _cache(tmp_path), _exe(tmp_path)

    def fake(command, out, err, timeout, stop, cwd=None, env=None):
        out.write_text("Blender not found", encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return ProcessOutcome(1 if command[-1] == "junkshop" else 0, False, False, 1.0)

    monkeypatch.setattr(bb, "run_process", fake)
    result = bb.BlenderBenchmarkWorkload("CPU", exe=exe, cache=cache).run(RunContext(output_dir=tmp_path / "r"))
    assert result.status == "invalid" and result.score is None and "monster" in result.error


def test_launcher_env_keeps_the_rest_of_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH_CHECK", "kept")
    env = bb.launcher_env(tmp_path)
    assert env["LOCALAPPDATA"] == str(tmp_path) and env["PATH_CHECK"] == "kept"

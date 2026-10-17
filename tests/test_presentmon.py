"""FrameCaptureWorkload with a fake elevated runner (no UAC, no PresentMon) and the REAL CSV parser and stats."""

import json

import pytest

from app.automation.workload import RunContext
from app.frames.elevated import ElevatedOutcome
from app.frames.presentmon import (
    CSV_NAME, FrameCaptureWorkload, capture_args, check_presentmon_path, workload_id_for,
)
from app.paths import load_settings, save_setting

HEADER = "Application,ProcessID,SwapChainAddress,PresentRuntime,FrameTime\n"


def csv_rows(app="game.exe", pid=100, times=(10.0,) * 200):
    return HEADER + "".join(f"{app},{pid},0x1,DXGI,{t}\n" for t in times)


@pytest.fixture
def exe(tmp_path):
    p = tmp_path / "PresentMon-2.3.0-x64.exe"
    p.write_bytes(b"MZ")
    return str(p)


def fake_runner(outcome, csv_text=None, calls=None):
    def run(executable, args, cwd, timeout, stop_event):
        if calls is not None:
            calls.append((executable, args, cwd, timeout))
        if csv_text is not None:
            out = args[args.index("--output_file") + 1]
            with open(out, "w", encoding="utf-8") as f:
                f.write(csv_text)
        return outcome
    return run


def capture(tmp_path, exe, outcome, csv_text=None, calls=None, **kw):
    wl = FrameCaptureWorkload(exe, "game.exe", seconds=20, elevated_runner=fake_runner(outcome, csv_text, calls),
                              version_reader=lambda p: "2.3.0", **kw)
    return wl.run(RunContext(output_dir=tmp_path))


def test_completed_capture(tmp_path, exe):
    calls = []
    times = [10.0] * 198 + [50.0, 30.0]
    result = capture(tmp_path, exe, ElevatedOutcome("exited", exit_code=0, duration_seconds=21.0),
                     csv_rows(times=times), calls)
    assert result.status == "completed" and result.unit == "fps"
    assert result.score == pytest.approx(1000 * 200 / sum(times))
    assert result.metrics["low_1pct_fps"] == (pytest.approx(25.0), "fps")
    executable, args, cwd, timeout = calls[0]
    assert executable == exe and timeout == 50 and cwd == str(tmp_path)
    assert args == ["--process_name", "game.exe", "--output_file", str(tmp_path / CSV_NAME), "--timed", "20",
                    "--terminate_after_timed", "--session_name", "PCBenchmarkManager"]


def test_pid_target_filters_rows(tmp_path, exe):
    text = csv_rows(pid=1) + csv_rows(pid=2, times=(20.0,) * 150).split("\n", 1)[1]
    result = capture(tmp_path, exe, ElevatedOutcome("exited", exit_code=0), text, process_id=2)
    assert result.status == "completed" and result.score == pytest.approx(50.0)
    assert "--process_id" in result.command and "--process_name" not in result.command


@pytest.mark.parametrize("outcome,status,error", [
    (ElevatedOutcome("uac_declined"), "cancelled", "UAC prompt declined"),
    (ElevatedOutcome("start_failed", error="could not start (winerror 2)"), "failed", "winerror 2"),
    (ElevatedOutcome("stopped", exit_code=1, terminated=True), "cancelled", "stopped by user"),
    (ElevatedOutcome("timed_out", terminated=False, error="could not terminate"), "timed_out", "could not terminate"),
    (ElevatedOutcome("exited", exit_code=3), "failed", "exit code 3"),
])
def test_failures_never_get_a_score(tmp_path, exe, outcome, status, error):
    result = capture(tmp_path, exe, outcome, csv_rows())
    assert result.status == status and result.score is None and error in result.error


def test_missing_or_unusable_capture(tmp_path, exe):
    ok = ElevatedOutcome("exited", exit_code=0)
    assert capture(tmp_path, exe, ok).error == "PresentMon wrote no capture file"
    short = capture(tmp_path, exe, ok, csv_rows(times=(10.0,) * 20))
    assert short.status == "invalid" and "too few frames" in short.error and short.score is None
    other = capture(tmp_path, exe, ok, csv_rows(app="other.exe"))
    assert other.status == "invalid" and other.score is None


def test_path_checks(tmp_path, exe):
    assert check_presentmon_path(exe) is None
    assert check_presentmon_path(None) == "PresentMon not selected"
    assert check_presentmon_path(str(tmp_path / "nope.exe")).startswith("file not found")
    bundled = tmp_path / "NVIDIA Corporation" / "FrameView" / "bin" / "PresentMon_x64.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    assert "FrameView" in check_presentmon_path(str(bundled))
    result = FrameCaptureWorkload(str(bundled), "game.exe").run(RunContext(output_dir=tmp_path))
    assert result.status == "skipped"


def test_args_and_ids():
    with pytest.raises(ValueError):
        capture_args("x.csv", 10)
    assert workload_id_for("Cyberpunk2077.exe") == "frames_cyberpunk2077"
    assert workload_id_for("My Game (DX12).EXE") == "frames_my_game_dx12"


def test_save_setting_keeps_other_keys(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"database_path": "D:/x.db"}), encoding="utf-8")
    save_setting(tmp_path, "presentmon_path", "C:/Tools/PresentMon.exe")
    assert load_settings(tmp_path)[0] == {"database_path": "D:/x.db", "presentmon_path": "C:/Tools/PresentMon.exe"}
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        save_setting(tmp_path, "presentmon_path", "x")
    assert (tmp_path / "settings.json").read_text(encoding="utf-8") == "{broken"

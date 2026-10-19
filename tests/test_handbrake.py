"""HandBrake encode tests: the clip + HandBrakeCLI install plans, the command, the speed line, a faked run and the
report (no download, no real HandBrake)."""

import threading
from pathlib import Path
from types import SimpleNamespace

from app.automation.workload import RunContext
from app.install import direct, plans
from app.pro import handbrake as hb
from app.report import _handbrake_section
from data.installers import HANDBRAKE_CLI

# the end of a HandBrakeCLI run (stderr): libhb's work loop prints the job's average speed
LOG = ("HandBrake 1.11.2 (2026060700) - MINGW - x86_64 - https://handbrake.fr\n"
       "[14:40:01] work: average encoding speed for job is 148.214562 fps\n"
       "[14:40:01] libhb: work result = 0\n\nEncode done!\n")


def test_one_install_gets_the_clip_then_handbrakecli():
    for host in (plans.Host("windows", "x64"), plans.Host("windows", "arm64"), plans.Host("linux", "arm64", "arch")):
        clip = plans.plan_for("handbrake_x265_1080p", host=host)
        assert clip["url"].startswith("https://download.blender.org/demo/movies/BBB/") and clip["then"] == "handbrake_cli"
        assert clip["exe"] == "bbb_sunflower_1080p_30fps_normal.mp4" and direct.problem(clip) is None
    for arch, plan in HANDBRAKE_CLI["windows"].items():
        assert direct.problem(plan) is None and plan["exe"] == "HandBrakeCLI.exe" and plan["version"] == "1.11.2"
        assert plan["url"].startswith("https://github.com/HandBrake/HandBrake/releases/download/1.11.2/")
    assert HANDBRAKE_CLI["linux"] == {"debian": "handbrake-cli", "arch": "handbrake-cli"}


def test_linux_cli_comes_from_the_package(monkeypatch):
    monkeypatch.setattr(hb, "_os_key", lambda: "linux")
    assert hb.cli_path(which=lambda n: "/usr/bin/HandBrakeCLI" if n == "HandBrakeCLI" else None) == \
        Path("/usr/bin/HandBrakeCLI")
    monkeypatch.setattr(hb, "cli_path", lambda *a, **k: None)
    ran = []
    commands = lambda family, package: ([["/usr/bin/pkexec", "/usr/bin/pacman", "-S", package]], None)
    run = lambda argv: (ran.append(argv), "done")[1]
    out = hb.install_cli(None, commands=commands, run=run, family="arch")
    assert out.ok and ran == [[["/usr/bin/pkexec", "/usr/bin/pacman", "-S", "handbrake-cli"]]]
    out = hb.install_cli(None, commands=commands, run=run, family="fedora")
    assert not out.ok and "install it yourself" in out.message


def test_the_command_and_the_speed_line():
    assert hb.build_command("HandBrakeCLI.exe", "clip.mp4", "out.mkv", "H.265 MKV 1080p30") == \
        ["HandBrakeCLI.exe", "-i", "clip.mp4", "-o", "out.mkv", "--preset", "H.265 MKV 1080p30"]
    assert hb.parse_speed(LOG) == (148.214562, None)
    assert hb.parse_speed("Encoding: task 1 of 1, 5.00 %\nNo title found.\n")[1] == \
        "no 'average encoding speed' line in HandBrake's output (it ended with: No title found.)"


def _ctx(tmp_path, name="run"):
    return RunContext(output_dir=str(tmp_path / name), timeout_seconds=60, stop_event=threading.Event())


def _fake(log, writes=True, exit_code=0):
    seen = {}

    def run(command, out, err, timeout, stop, cwd=None, **k):
        seen["command"] = command
        Path(out).write_text("Encoding: task 1 of 1, 100.00 %\n", encoding="utf-8")
        Path(err).write_text(log, encoding="utf-8")
        if writes:
            Path(command[command.index("-o") + 1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(exit_code=exit_code, timed_out=False, cancelled=False, duration_seconds=130.0,
                               start_error=None)
    return run, seen


def test_a_run_reads_the_speed_and_deletes_its_own_encode(tmp_path, monkeypatch):
    cli, clip = tmp_path / "HandBrakeCLI.exe", tmp_path / "bbb.mp4"
    cli.write_bytes(b"")
    clip.write_bytes(b"")
    run, seen = _fake(LOG)
    monkeypatch.setattr(hb, "run_process", run)
    w = hb.HandBrakeWorkload("handbrake_x265_1080p", cli=cli, clip=clip)
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 148.21 and r.unit == "fps" and not w.verified
    assert seen["command"][-1] == "H.265 MKV 1080p30" and seen["command"][2] == str(clip)
    assert not (tmp_path / "run" / "encode_x265.mkv").exists() and "HandBrake: 1.11.2" in r.note
    assert "deleted after the run" in r.note and clip.exists()


def test_no_speed_no_file_or_nothing_installed_store_no_score(tmp_path, monkeypatch):
    cli, clip = tmp_path / "HandBrakeCLI.exe", tmp_path / "bbb.mp4"
    cli.write_bytes(b"")
    clip.write_bytes(b"")
    run, _ = _fake("No title found.\n", writes=False, exit_code=3)
    monkeypatch.setattr(hb, "run_process", run)
    r = hb.HandBrakeWorkload(cli=cli, clip=clip).run(_ctx(tmp_path, "a"))
    assert r.status == "invalid" and r.score is None and "exit code 3" in r.error
    run, _ = _fake(LOG, writes=False)
    monkeypatch.setattr(hb, "run_process", run)
    r = hb.HandBrakeWorkload(cli=cli, clip=clip).run(_ctx(tmp_path, "b"))
    assert r.status == "invalid" and "no video file" in r.error
    w = hb.HandBrakeWorkload(cli=None, clip=None)
    assert w.run(_ctx(tmp_path, "c")).status == "skipped" and "clip" in w.availability()[1]
    assert "HandBrakeCLI" in hb.HandBrakeWorkload(cli=None, clip=clip).availability()[1]


def test_its_own_report_section():
    export = {"session": {}, "runs": [
        {"benchmark_name": "handbrake_x264_1080p", "status": "completed", "is_warmup": False, "unit": "fps",
         "note": "HandBrake: 1.11.2; Preset: Fast 1080p30"}],
        "summaries": {"handbrake_x264_1080p": {"stats": {"mean": 148.214}}}}
    html = _handbrake_section(export)
    assert "<h2>HandBrake</h2>" in html and "148.2 <span>fps</span>" in html and "Fast 1080p30" in html
    assert _handbrake_section({"runs": []}) == ""

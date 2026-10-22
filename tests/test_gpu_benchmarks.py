"""GravityMark (Windows + Linux), vkmark and glmark2 (Linux): commands, parsing, installs, system checks.
Nothing here starts a real program or downloads anything."""

import io
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace

from app import catalog
from app.automation.workload import RunContext
from app.install import direct, plans
from app.pro import gravitymark as gm
from app.pro import linux_gl
from app.report import _gpu_bench_section

LINUX = {"os_family": "linux", "native_arch": "x64"}
WINDOWS = {"os_family": "windows", "native_arch": "x64"}


def _ctx(tmp_path):
    return RunContext(output_dir=str(tmp_path / "run"), timeout_seconds=60, stop_event=threading.Event())


def _fake(write, exit_code=0):
    seen = {}

    def run(command, out, err, timeout, stop, cwd=None, env=None):
        seen.update(command=command, cwd=cwd, env=env)
        write(Path(out).parent, Path(out))
        return SimpleNamespace(exit_code=exit_code, timed_out=False, cancelled=False, duration_seconds=180.0,
                               start_error=None)
    return run, seen


# GravityMark

def test_gravitymark_reads_the_last_fps_line_or_the_frame_times():
    fps, details, why = gm.parse_result("loading\nM:  2:49.430: FPS: 151.3\nM:  2:50.000: FPS: 152.8 Score: 25500\n")
    assert fps == 152.8 and why is None and details["GravityMark score"] == "25500"
    times = "0 0.0 0.0 0\n1 0.5 0.5 0\n2 1.0 0.5 0\n"
    fps, details, why = gm.parse_result("no result here", times)
    assert fps == 2.0 and "frame times" in details["Read from"]
    fps, details, why = gm.parse_result("Vulkan: can't create device\n", "")
    assert fps is None and details == {} and "can't create device" in why
    assert gm.parse_result("FPS: 0\n")[0] is None


def test_gravitymark_command_matches_the_vendor_and_phoronix_options(tmp_path):
    cmd = gm.build_command("GravityMark.exe", "-vulkan", str(tmp_path))
    assert cmd[0] == "GravityMark.exe" and cmd[-1] == "-vulkan"
    for flag, value in (("-vsync", "0"), ("-benchmark", "1"), ("-close", "1"), ("-fullscreen", "1"), ("-status", "1")):
        assert cmd[cmd.index(flag) + 1] == value
    assert cmd[cmd.index("-times") + 1] == str(tmp_path / "times.txt")


def test_gravitymark_runs_only_after_its_license_and_stores_only_a_real_result(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "GravityMark.x64"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    w = gm.GravityMarkWorkload("gravitymark_vulkan", exe=exe, data_dir=tmp_path, platform="linux")
    r = w.run(_ctx(tmp_path))
    assert r.status == "skipped" and "license" in r.error
    w.accept_license()
    assert w.license_ok()
    run, seen = _fake(lambda folder, out: out.write_text("M:  2:49.430: FPS: 151.3\n", encoding="utf-8"))
    monkeypatch.setattr(gm, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 151.3 and r.unit == "FPS" and not w.verified
    assert seen["cwd"] == str(exe.parent) and seen["env"]["LD_LIBRARY_PATH"].startswith(str(exe.parent))
    assert "Vulkan" in r.note
    run, _ = _fake(lambda folder, out: out.write_text("closed\n", encoding="utf-8"), exit_code=1)
    monkeypatch.setattr(gm, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "failed" and r.score is None
    run, _ = _fake(lambda folder, out: None, exit_code=-1073741515)
    monkeypatch.setattr(gm, "run_process", run)
    r = gm.GravityMarkWorkload("gravitymark_d3d12", exe=exe, data_dir=tmp_path, platform="win32").run(_ctx(tmp_path))
    assert r.status == "failed" and "Visual C++" in r.error
    run, _ = _fake(lambda folder, out: out.write_text("nothing useful\n", encoding="utf-8"))
    monkeypatch.setattr(gm, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "invalid" and r.score is None


def test_gravitymark_d3d12_is_hidden_on_linux_and_the_others_are_not(tmp_path):
    exe = tmp_path / "GravityMark.x64"
    on_linux = [gm.GravityMarkWorkload(t, exe=exe, data_dir=tmp_path, platform="linux") for t in gm.MODES]
    entries = {e.id: e for e in catalog.workload_entries(on_linux, LINUX)}
    assert not entries["gravitymark_d3d12"].compatible
    assert entries["gravitymark_vulkan"].compatible and entries["gravitymark_opengl"].compatible
    on_windows = [gm.GravityMarkWorkload(t, exe=None, data_dir=tmp_path, platform="win32") for t in gm.MODES]
    entries = {e.id: e for e in catalog.workload_entries(on_windows, WINDOWS)}
    assert all(e.compatible and e.state == catalog.MISSING for e in entries.values())
    arm = catalog.workload_entries(on_windows, {"os_family": "windows", "native_arch": "arm64"})
    assert not any(e.compatible for e in arm)


def test_gravitymark_installs_from_its_pinned_files_on_x64_only():
    win = plans.plan_for("gravitymark_d3d12", host=plans.Host("windows", "x64"))
    lin = plans.plan_for("gravitymark_vulkan", host=plans.Host("linux", "x64", "debian"))
    assert win["kind"] == lin["kind"] == "zip"
    assert win["url"].endswith(".msi") and win["exe"].endswith("GravityMark.exe") and len(win["sha256"]) == 64
    assert lin["url"].endswith(".run") and lin["exe"] == "bin/GravityMark.x64" and lin["hosts"] == ["tellusim.com"]
    assert plans.plan_for("gravitymark_vulkan", host=plans.Host("linux", "arm64", "debian")) is None
    assert direct.archive_suffix(win["url"]) == ".msi" and direct.archive_suffix(lin["url"]) == ".run"


# the two vendor formats, unpacked and never run

def _makeself(tmp_path, payload: bytes, lines=4) -> Path:
    header = (b'#!/bin/sh\n# This script was generated using Makeself\nfilesizes="%d"\noffset=`head -n %d "$1" | wc -c`\n'
              % (len(payload), lines))
    path = tmp_path / "Tool_1.0.run"
    path.write_bytes(header + payload)
    return path


def test_a_makeself_run_file_is_unpacked_from_its_header_without_running_it(tmp_path):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        body = b"ELF"
        info = tarfile.TarInfo("./bin/Tool.x64")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    run = _makeself(tmp_path, data.getvalue())
    offset, size = direct.makeself_payload(run)
    assert size == len(data.getvalue()) and offset == run.stat().st_size - size
    dest = tmp_path / "tool-1.0"
    assert direct.extract(run, dest, "bin/Tool.x64") is None
    assert (dest / "bin" / "Tool.x64").read_bytes() == b"ELF"
    assert not (tmp_path / "Tool_1.0.run.tar.gz").exists()


def test_a_run_file_whose_header_does_not_match_is_refused(tmp_path):
    run = _makeself(tmp_path, b"x" * 10)
    run.write_bytes(run.read_bytes() + b"extra")
    try:
        direct.makeself_payload(run)
    except ValueError as e:
        assert "size" in str(e)
    else:
        raise AssertionError("a wrong size was accepted")


def test_an_msi_becomes_an_administrative_image_with_windows_msiexec(tmp_path):
    seen = {}

    def run(command, **kw):
        seen["command"], seen["kw"] = command, kw
        (tmp_path / "image" / "Tool").mkdir(parents=True)
        (tmp_path / "image" / "Tool.msi").write_bytes(b"copy")
        return SimpleNamespace(returncode=0)
    (tmp_path / "image").mkdir()
    direct.msi_image(tmp_path / "Tool.msi", tmp_path / "image", run=run)
    assert seen["command"][0].lower().endswith("msiexec.exe") and seen["command"][1:2] == ["/a"]
    assert "/qn" in seen["command"] and seen["command"][-1].startswith("TARGETDIR=") and seen["kw"]["timeout"]
    assert not (tmp_path / "image" / "Tool.msi").exists() and (tmp_path / "image" / "Tool").is_dir()
    try:
        direct.msi_image(tmp_path / "Tool.msi", tmp_path / "image", run=lambda c, **kw: SimpleNamespace(returncode=1603))
    except ValueError as e:
        assert "1603" in str(e)
    else:
        raise AssertionError("a failed msiexec was accepted")


# vkmark and glmark2

def test_vkmark_and_glmark2_read_their_own_score_line():
    assert linux_gl.parse_score("glmark2", "[build] use-vbo=false: FPS: 3000\n" + " " * 30 + "glmark2 Score: 4567\n") == (4567, None)
    assert linux_gl.parse_score("vkmark", "=====\n  vkmark Score: 9876\n=====\n") == (9876, None)
    score, why = linux_gl.parse_score("vkmark", "Error: No suitable Vulkan physical devices found\n")
    assert score is None and "No suitable Vulkan" in why
    assert linux_gl.parse_score("glmark2", "glmark2 Score: 0\n")[0] is None


def test_vkmark_and_glmark2_run_from_path_and_only_on_linux(tmp_path, monkeypatch):
    w = linux_gl.LinuxGpuWorkload("vkmark", platform="linux", which=lambda name: f"/usr/bin/{name}")
    run, seen = _fake(lambda folder, out: out.write_text("  vkmark Score: 9876\n", encoding="utf-8"))
    monkeypatch.setattr(linux_gl, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "completed" and r.score == 9876 and r.unit == "points" and seen["command"] == ["/usr/bin/vkmark"]
    run, _ = _fake(lambda folder, out: None, exit_code=1)
    monkeypatch.setattr(linux_gl, "run_process", run)
    assert w.run(_ctx(tmp_path)).status == "failed"
    missing = linux_gl.LinuxGpuWorkload("glmark2", platform="linux", which=lambda name: None)
    assert missing.availability() == (False, "glmark2 is not installed (Install button)")
    assert missing.run(_ctx(tmp_path)).status == "skipped"
    on_windows = [linux_gl.LinuxGpuWorkload(t, platform="win32") for t in linux_gl.PROGRAMS]
    assert not any(e.compatible for e in catalog.workload_entries(on_windows, WINDOWS))
    on_linux = [linux_gl.LinuxGpuWorkload(t, platform="linux", which=lambda n: None, adreno=lambda: False)
                for t in linux_gl.PROGRAMS]
    entries = catalog.workload_entries(on_linux, LINUX)
    assert all(e.compatible and e.state == catalog.MISSING and e.group == "gpu" for e in entries)


def test_glmark2_uses_its_wayland_build_on_a_wayland_desktop():
    # Radxa Dragon Q8B, 2026-10-21: the X11 glmark2 failed through Xwayland, glmark2-wayland ran
    both = lambda name: f"/usr/bin/{name}"
    wayland = linux_gl.LinuxGpuWorkload("glmark2", platform="linux", which=both, env={"WAYLAND_DISPLAY": "wayland-0"})
    assert wayland.executable()[0] == "/usr/bin/glmark2-wayland"
    x11 = linux_gl.LinuxGpuWorkload("glmark2", platform="linux", which=both, env={"DISPLAY": ":0"})
    assert x11.executable()[0] == "/usr/bin/glmark2"
    only_x11 = lambda name: "/usr/bin/glmark2" if name == "glmark2" else None
    fallback = linux_gl.LinuxGpuWorkload("glmark2", platform="linux", which=only_x11, env={"XDG_SESSION_TYPE": "wayland"})
    assert fallback.executable()[0] == "/usr/bin/glmark2"
    vk = linux_gl.LinuxGpuWorkload("vkmark", platform="linux", which=both, env={"WAYLAND_DISPLAY": "wayland-0"})
    assert vk.executable()[0] == "/usr/bin/vkmark"


def test_vkmark_and_glmark2_install_with_the_distribution_packages():
    host = plans.Host("linux", "x64", "debian")
    assert plans.plan_for("vkmark", host=host)["package"] == "vkmark"
    assert plans.plan_for("glmark2", host=host)["package"] == "glmark2-x11"
    assert plans.plan_for("glmark2", host=plans.Host("linux", "x64", "fedora")) is None


def test_the_report_has_one_gpu_benchmarks_section():
    export = {"session": {}, "runs": [
        {"benchmark_name": n, "status": "completed", "is_warmup": False, "unit": u, "note": ""}
        for n, u in (("gravitymark_vulkan", "FPS"), ("glmark2", "points"))],
        "summaries": {"gravitymark_vulkan": {"stats": {"mean": 151.34}}, "glmark2": {"stats": {"mean": 4567}}}}
    html = _gpu_bench_section(export)
    assert "<h2>GPU benchmarks</h2>" in html and "151.3 <span>FPS</span>" in html and "GravityMark, Vulkan" in html
    assert "glmark2 (OpenGL)" in html and "Higher is faster" in html
    assert _gpu_bench_section({"runs": []}) == ""


def test_a_software_renderer_is_never_a_gpu_score(tmp_path, monkeypatch):
    # full run on Linux (WSL), 2026-10-21: vkmark and glmark2 "completed" on Mesa's llvmpipe, GravityMark crashed on it
    from app.pro import software_gpu
    assert software_gpu.software_renderer("GL_RENDERER:    llvmpipe (LLVM 20.1.2, 256 bits)") == "llvmpipe"
    assert software_gpu.software_renderer("Device: NVIDIA GeForce RTX 5090 Laptop GPU") is None
    w = linux_gl.LinuxGpuWorkload("glmark2", platform="linux", which=lambda name: f"/usr/bin/{name}")
    run, _ = _fake(lambda folder, out: out.write_text(
        "    GL_RENDERER:    llvmpipe (LLVM 20.1.2, 256 bits)\n  glmark2 Score: 1075\n", encoding="utf-8"))
    monkeypatch.setattr(linux_gl, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "invalid" and r.score is None and "llvmpipe" in r.error and "OpenGL" in r.error
    exe = tmp_path / "bin" / "GravityMark.x64"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    g = gm.GravityMarkWorkload("gravitymark_vulkan", exe=exe, data_dir=tmp_path, platform="linux")
    g.accept_license()
    run, _ = _fake(lambda folder, out: out.write_text(
        "M:  37.60 ms: GPU: NVIDIA GeForce RTX 5090 Laptop GPU\nM: 298.81 ms: Device: llvmpipe (LLVM 20.1.2, 256 bits)\n",
        encoding="utf-8"), exit_code=-11)
    monkeypatch.setattr(gm, "run_process", run)
    r = g.run(_ctx(tmp_path))
    assert r.status == "invalid" and "llvmpipe" in r.error and "Vulkan" in r.error


def test_vkmark_is_not_offered_on_a_qualcomm_adreno_gpu():
    # Radxa Dragon Q8B, 2026-10-21: vkmark crashed twice (exit -11) and the board went off
    links = {"/sys/class/drm/card1/device/driver": "/sys/bus/platform/drivers/msm_dpu",
             "/sys/class/drm/renderD128/device/driver": "/sys/bus/platform/drivers/msm_dpu"}
    listdir = lambda root: ["card1", "card1-DSI-1", "renderD128", "version"]
    assert linux_gl.qualcomm_gpu(listdir=listdir, realpath=lambda p: links.get(p, p))
    nvidia = lambda p: "/sys/bus/pci/drivers/nvidia"
    assert not linux_gl.qualcomm_gpu(listdir=lambda root: ["card0"], realpath=nvidia)
    assert not linux_gl.qualcomm_gpu(listdir=lambda root: (_ for _ in ()).throw(OSError()))
    vk = linux_gl.LinuxGpuWorkload("vkmark", platform="linux", which=lambda n: "/usr/bin/vkmark", adreno=lambda: True)
    assert "Adreno" in vk.system_blocks()
    gl = linux_gl.LinuxGpuWorkload("glmark2", platform="linux", which=lambda n: "/usr/bin/glmark2", adreno=lambda: True)
    assert gl.system_blocks() is None


def test_a_crash_says_which_signal(tmp_path, monkeypatch):
    w = linux_gl.LinuxGpuWorkload("glmark2", platform="linux", which=lambda name: f"/usr/bin/{name}", env={})
    run, _ = _fake(lambda folder, out: None, exit_code=-11)
    monkeypatch.setattr(linux_gl, "run_process", run)
    r = w.run(_ctx(tmp_path))
    assert r.status == "failed" and r.error == "crashed (SIGSEGV, exit code -11)"
    run, _ = _fake(lambda folder, out: None, exit_code=1)
    monkeypatch.setattr(linux_gl, "run_process", run)
    assert w.run(_ctx(tmp_path)).error.startswith("exit code 1 (no OpenGL")

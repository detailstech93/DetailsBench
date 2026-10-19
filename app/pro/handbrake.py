"""HandBrake video encoding as Workloads: one standard clip encoded with two of HandBrake's own presets (H.264 x264
and H.265 x265, 1080p30), on the CPU. The score is HandBrake's own average encoding speed in frames per second.

What is downloaded, all by the same Install button (data/installers.py), after the owner saw it:
- the clip: Big Buck Bunny "sunflower" 1080p 30 fps (bbb_sunflower_1080p_30fps_normal.mp4, 10:34 min, H.264;
  Blender Foundation, CC BY 3.0) from Blender's own server, download.blender.org/demo/movies/BBB/. Blender
  publishes no checksum: size and SHA-256 were read from a download here on 2026-10-19 and pinned. Phoronix Test
  Suite's video encoding tests use the same file.
- HandBrakeCLI (GPL, handbrake.fr): on Windows the zip from HandBrake's own GitHub release (x64 and ARM64; size and
  SHA-256 as GitHub lists them, checked on a download here), on Linux the distribution's package through pkexec
  (Debian/Ubuntu and Arch have one; Fedora and openSUSE only in third-party repositories, which the app does not
  add). The Linux version is the distribution's, so it can differ from Windows' (the run notes it).

Command (HandBrakeCLI --help): HandBrakeCLI -i <clip> -o <run folder>/<file> --preset "<preset>". At the end
HandBrake prints "average encoding speed for job is <fps> fps" (its libhb work loop): that is the score, higher is
faster. The encoded file is the app's own and is deleted after its size is noted (a full encode is hundreds of MB).
Not checked on a real run here yet, so the tests are not marked verified. Nothing is uploaded.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Tuple

from app.automation.process import run_process
from app.automation.workload import RunContext, Workload, WorkloadResult

# test id: (title, HandBrake preset, output file)
MODES = {
    "handbrake_x264_1080p": ("H.264 (x264), 1080p", "Fast 1080p30", "encode_x264.mp4"),
    "handbrake_x265_1080p": ("H.265 (x265), 1080p", "H.265 MKV 1080p30", "encode_x265.mkv"),
}
UNIT = "fps"
SPEED = re.compile(r"average encoding speed for job is\s+([0-9]+(?:\.[0-9]+)?)\s*fps", re.I)
VERSION = re.compile(r"\bHandBrake\s+(\d+\.\d+\.\d+)")
CLIP = "Big Buck Bunny 1080p 30 fps (10:34)"


def _os_key() -> str:
    return "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def clip_plan() -> Optional[dict]:
    from app.install.plans import plan_for
    plan = plan_for("handbrake_x264_1080p")
    return plan if plan and plan.get("kind") == "zip" else None


def clip_path(data_dir: Optional[Path] = None) -> Optional[Path]:
    from app.install import direct
    plan = clip_plan()
    if not plan:
        return None
    path = direct.target_folder(plan, data_dir) / plan["exe"]
    return path if path.is_file() else None


def cli_plan(arch: Optional[str] = None) -> Optional[dict]:
    """Windows: HandBrake's CLI zip for this architecture."""
    from data.installers import HANDBRAKE_CLI
    if _os_key() != "windows":
        return None
    if arch is None:
        from app.frames.provision import native_arch
        arch = native_arch()
    return HANDBRAKE_CLI["windows"].get(arch)


def cli_path(data_dir: Optional[Path] = None, which: Callable = shutil.which) -> Optional[Path]:
    """Windows: the CLI in the app's tools folder. Linux: HandBrakeCLI from the distribution's package."""
    if _os_key() == "linux":
        found = which("HandBrakeCLI")
        return Path(found) if found else None
    plan = cli_plan()
    if not plan:
        return None
    from app.install import direct
    exe = direct.target_folder(plan, data_dir) / plan["exe"]
    return exe if exe.is_file() else None


def cli_install_text() -> str:
    """What the Install button does after the clip (shown before the owner says Yes)."""
    if cli_path():
        return "HandBrakeCLI is already installed."
    if _os_key() == "windows":
        from app.install import direct
        from app.install.winget import size_text
        plan = cli_plan()
        if not plan:
            return "HandBrake publishes no HandBrakeCLI for this processor."
        return (f"Then HandBrakeCLI {plan['version']} from HandBrake's own GitHub release: {plan['url']} "
                f"({size_text(plan['size'])}, {direct.checksum_text(plan)}), extracted to "
                f"{direct.target_folder(plan)}.")
    from app.install.plans import this_host
    from data.installers import HANDBRAKE_CLI
    package = HANDBRAKE_CLI["linux"].get(this_host().family or "")
    if package:
        return (f"Then the {package} package with your distribution's package manager; the system asks for your "
                "password (pkexec).")
    return ("HandBrakeCLI is not in this distribution's own repositories: install it yourself (HandBrake's site "
            "lists the ways), then press 'I installed something: check again'.")


def install_cli(clip, progress=lambda text: None, commands=None, run=None, family=None):
    """After the clip: HandBrakeCLI (winget.Outcome). Windows: HandBrake's zip; Linux: the package via pkexec."""
    from app.install import winget
    if cli_path():
        return winget.Outcome(True, "The test clip and HandBrakeCLI are ready.")
    if _os_key() == "windows":
        from app.install import direct
        plan = cli_plan()
        if not plan:
            return winget.Outcome(False, "The test clip was downloaded, but HandBrake has no HandBrakeCLI for "
                                         "this processor.")
        progress(f"Downloading HandBrakeCLI {plan['version']}...")
        outcome = direct.install("HandBrakeCLI", plan)
        return winget.Outcome(outcome.ok, ("The test clip and HandBrakeCLI are ready." if outcome.ok else
                                           "The test clip was downloaded; " + outcome.message))
    from app.install.plans import this_host
    from data.installers import HANDBRAKE_CLI
    family = family or this_host().family or ""
    package = HANDBRAKE_CLI["linux"].get(family)
    if not package:
        return winget.Outcome(False, "The test clip was downloaded, but HandBrakeCLI is not in this "
                                     "distribution's own repositories: install it yourself.")
    if commands is None:
        from app.install.plans import linux_commands as commands
    if run is None:
        from app.ui.frame_capture_panel import run_commands as run
    argv, problem = commands(family, package)
    if problem:
        return winget.Outcome(False, f"The test clip was downloaded; {package} was not installed: {problem}")
    progress(f"Installing {package} (enter your password in the system prompt)...")
    text = run(argv)
    if text != "done":
        return winget.Outcome(False, f"The test clip was downloaded; {package} was not installed: {text}")
    return winget.Outcome(True, f"The test clip and {package} are ready.")


def build_command(cli: str, clip: str, output: str, preset: str) -> list:
    return [cli, "-i", clip, "-o", output, "--preset", preset]


def parse_speed(text: str) -> Tuple[Optional[float], Optional[str]]:
    """(fps, None) from HandBrake's last "average encoding speed for job is <fps> fps", or (None, why)."""
    found = SPEED.findall(text)
    if not found:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return None, ("no 'average encoding speed' line in HandBrake's output"
                      + (f" (it ended with: {lines[-1][:200]})" if lines else " (it printed nothing)"))
    fps = float(found[-1])
    if fps <= 0:
        return None, f"the speed {found[-1]} fps is not a result"
    return fps, None


class HandBrakeWorkload(Workload):
    timeout_seconds = 3 * 3600      # x265 on a slow CPU: the clip has 19,000 frames

    def __init__(self, test_id: str = "handbrake_x264_1080p", cli: Optional[Path] = None, clip: Optional[Path] = None):
        self.id = test_id
        self.verified = False       # HandBrake's output has not been seen on a real run here yet
        self.title, self.preset, self.output = MODES[test_id]
        self.cli = cli if cli is not None else cli_path()
        self.clip = clip if clip is not None else clip_path()
        self.display_name = f"HandBrake ({self.title})"
        self.benchmark_type = "cpu"

    def availability(self) -> Tuple[bool, str]:
        if _os_key() not in ("windows", "linux"):
            return False, "HandBrake runs here on Windows and Linux only"
        if not self.clip:
            return False, "the test clip is not installed (Install button)"
        if not self.cli:
            return False, "HandBrakeCLI is not installed (Install button)"
        return True, str(self.cli)

    def executable(self) -> Tuple[Optional[str], str]:
        return (str(self.cli) if self.cli else None), ("the app's tools folder" if _os_key() == "windows"
                                                       else "the distribution's package")

    def version(self) -> Optional[str]:
        plan = cli_plan()
        return plan["version"] if plan and self.cli else None

    def describe_command(self) -> Optional[str]:
        if not (self.cli and self.clip):
            return None
        return subprocess.list2cmdline(build_command(str(self.cli), str(self.clip), "<run folder>/" + self.output,
                                                     self.preset))

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"HandBrake: {why}")
        folder = Path(ctx.output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / self.output
        command = build_command(str(self.cli), str(self.clip), str(output), self.preset)
        out, err = folder / "stdout_handbrake.txt", folder / "stderr_handbrake.txt"
        outcome = run_process(command, out, err, ctx.timeout_seconds, ctx.stop_event, cwd=str(folder))
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3), "stdout_path": str(out),
                  "stderr_path": str(err)}
        encoded = None
        try:
            if output.is_file():
                encoded = output.stat().st_size
                output.unlink()             # the app's own encode: only its size is kept
        except OSError:
            pass
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {ctx.timeout_seconds:g} s", **common)
        text = (err.read_text(encoding="utf-8", errors="replace") + "\n"
                + out.read_text(encoding="utf-8", errors="replace"))
        fps, why = parse_speed(text)
        if fps is None:
            return WorkloadResult(status="invalid", error=why + (f"; exit code {outcome.exit_code}"
                                                                 if outcome.exit_code else ""), **common)
        if outcome.exit_code != 0:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}", **common)
        if not encoded:
            return WorkloadResult(status="invalid", error="HandBrake reported a speed but wrote no video file",
                                  **common)
        version = VERSION.search(text)
        parts = [("HandBrake", version.group(1) if version else self.version()), ("Preset", self.preset),
                 ("Clip", CLIP), ("Encoded file", f"{encoded / 1024 ** 2:.1f} MB (deleted after the run)")]
        note = "; ".join(f"{k}: {v}" for k, v in parts if v)
        return WorkloadResult(status="completed", score=round(fps, 2), unit=UNIT, metrics={}, note=note, **common)


def handbrake_workloads() -> list:
    return [HandBrakeWorkload(t) for t in MODES]

"""Linux game capture with MangoHud: the app starts Steam itself with MangoHud switched on for that Steam only.

The owner chose this on 2026-10-19 (asked once, then automatic): nothing in Steam's settings is changed. MangoHud's
README: "MANGOHUD=1" in the environment turns it on for Vulkan programs (its way to enable it globally, here only
for the Steam the app starts and the games Steam starts from it), and "MANGOHUD_CONFIGFILE=/path/to/config" points
it at a config file (here one in the run folder). MANGOHUD_CONFIG would take priority over that file, so it is
removed from Steam's environment. The config (README option names): output_folder (required for logging),
log_interval=0 (one row per frame; MangoHud's src/overlay.cpp), autostart_log=1 (logging starts 1 s after MangoHud
starts in the game), log_duration (seconds; MangoHud then stops logging and writes "<log>_summary.csv", which
tells the app the measuring is over: src/logging.cpp), no_display (the HUD is not drawn).

Steam is started with "steam -applaunch <appid> [args]" (Valve's Steam command-line options). A Steam that is
already open would get only the request, not the environment, so the app asks the owner to close Steam first and
never closes it itself. Vulkan only: Proton games (DXVK / VKD3D-Proton) and native Vulkan games. A native OpenGL
game writes no log; that is reported. The Flatpak Steam is not supported yet (MangoHud is a Flatpak extension
there). None of this has run on a real Linux PC yet.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from app.frames.mangohud_csv import find_new_logs, parse_mangohud_log

CONFIG_NAME = "MangoHud.conf"
LOGS_FOLDER = "mangohud_logs"
STEAM_NAMES = {"steam"}             # the Steam client's process name on Linux
LAUNCH_TIMEOUT_SECONDS = 300.0      # Steam starts first (and may update) before the game
SKIP_PROGRAMS = ("fossilize",)      # Steam's shader pre-compiling also uses Vulkan: not the game
CONSENT_KEY = "mangohud_start_steam"


def config_text(output_folder: Path, log_duration: int) -> str:
    return "\n".join([
        "# written by PC Benchmark Manager for one game run",
        f"output_folder={output_folder}",
        "log_interval=0",
        "autostart_log=1",
        f"log_duration={int(log_duration)}",
        "no_display",
        ""])


def environment(config_path: Path, base: Optional[dict] = None) -> dict:
    env = dict(os.environ if base is None else base)
    env.pop("MANGOHUD_CONFIG", None)
    env["MANGOHUD"] = "1"
    env["MANGOHUD_CONFIGFILE"] = str(config_path)
    return env


def steam_command(app_id, args: Sequence[str] = (), which: Callable = shutil.which) -> Optional[List[str]]:
    steam = which("steam")
    if not steam:
        return None
    return [steam, "-applaunch", str(int(app_id))] + [str(a) for a in args]


def steam_running(iter_names: Optional[Callable[[], Iterable[str]]] = None) -> bool:
    if iter_names is None:
        import psutil

        def iter_names():
            for p in psutil.process_iter(["name"]):
                yield p.info.get("name") or ""
    return any((name or "").lower() in STEAM_NAMES for name in iter_names())


def mangohud_installed(which: Callable = shutil.which) -> bool:
    return bool(which("mangohud"))


def start_detached(command: List[str], env: dict) -> None:
    """Steam keeps running after the benchmark: its own session, not a child the app ends."""
    subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def logging_done(folder: Path) -> bool:
    try:
        return any(p.name.endswith("_summary.csv") for p in Path(folder).iterdir())
    except OSError:
        return False


def best_log(folder: Path, since: float) -> Optional[Tuple[Path, object]]:
    """The new log with the most frames (a launcher or splash screen may write its own), or None."""
    best = None
    for path in find_new_logs(folder, since):
        if path.name.lower().startswith(SKIP_PROGRAMS):
            continue
        try:
            parsed = parse_mangohud_log(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if parsed.error is None and (best is None or len(parsed.frametimes_ms) > len(best[1].frametimes_ms)):
            best = (path, parsed)
    return best


def program_name(log: Path) -> str:
    from app.frames.mangohud_csv import LOG_NAME
    m = LOG_NAME.match(log.name)
    name = m.group("program") if m else log.stem
    return name[5:] if name.startswith("wine-") else name


class Hooks:
    """What the run needs from the system (tests pass fakes)."""

    def __init__(self, which: Callable = shutil.which, steam_names=None, start: Callable = start_detached):
        self.which, self._steam_names, self.start = which, steam_names, start

    def mangohud_installed(self) -> bool:
        return mangohud_installed(self.which)

    def steam_running(self) -> bool:
        return steam_running(self._steam_names)

    def command(self, app_id, args) -> Optional[List[str]]:
        return steam_command(app_id, args, self.which)

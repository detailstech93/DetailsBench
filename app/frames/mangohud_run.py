"""Linux game capture with MangoHud: the app runs Steam itself with MangoHud switched on for that Steam only.

The owner chose this on 2026-10-19 (asked once, then automatic): nothing in Steam's settings is changed. MangoHud's
README: "MANGOHUD=1" in the environment turns it on for Vulkan programs (its way to enable it globally, here only
for the Steam the app starts and the games Steam starts from it), and "MANGOHUD_CONFIGFILE=/path/to/config" points
it at a config file. MANGOHUD_CONFIG would take priority over that file, so it is removed from Steam's environment.
The config (README option names): output_folder (required for logging), log_interval=0 (one row per frame;
MangoHud's src/overlay.cpp), autostart_log=1 (logging starts 1 s after MangoHud starts in the game), log_duration
(seconds; MangoHud then stops logging and writes "<log>_summary.csv", which tells the app the measuring is over:
src/logging.cpp), no_display (the HUD is not drawn).

Several games in a row (the owner's wish, 2026-10-19): the config file and the log folder have ONE fixed place in
the app's data folder (mangohud/), and the config is written again before each game; MangoHud reads it when the
game starts. So the Steam the app started stays usable for every next game: "steam -applaunch <appid> [args]"
(Valve's Steam command-line options) hands the next game to it. A Steam that is open WITHOUT the app's MangoHud
setting (started by the owner) would only get the request, not the environment: the app then asks it to exit with
"steam -shutdown" (Valve's wiki and the steam(6) man page: shuts down Steam), Steam's own clean exit, waits until
it is gone and starts it again with MangoHud. That is part of the permission asked once. Nothing is killed.
Whether a Steam is the app's is read from its environment (/proc/<pid>/environ, the owner's own process).

Vulkan only: Proton games (DXVK / VKD3D-Proton) and native Vulkan games. A native OpenGL game writes no log;
that is reported. The Flatpak Steam is not supported yet (MangoHud is a Flatpak extension there). None of this
has run on a real Linux PC yet.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from app.frames.mangohud_csv import find_new_logs, parse_mangohud_log

FOLDER = "mangohud"                 # in the app's data folder: the config and the logs, for every game
CONFIG_NAME = "MangoHud.conf"
LOGS_FOLDER = "mangohud_logs"
STEAM_NAMES = {"steam"}             # the Steam client's process name on Linux
LAUNCH_TIMEOUT_SECONDS = 300.0      # Steam starts first (and may update) before the game
SHUTDOWN_WAIT_SECONDS = 120.0       # Steam's own exit may take a while (it saves and syncs)
SKIP_PROGRAMS = ("fossilize",)      # Steam's shader pre-compiling also uses Vulkan: not the game
CONSENT_KEY = "mangohud_start_steam"


def shared_folder(data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        from app.paths import get_data_dir
        data_dir = get_data_dir()
    return Path(data_dir) / FOLDER


def config_text(output_folder: Path, log_duration: int) -> str:
    return "\n".join([
        "# written by PC Benchmark Manager before each game run",
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


def _steam_procs():
    import psutil
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() in STEAM_NAMES:
            yield p


def steam_running(iter_names: Optional[Callable[[], Iterable[str]]] = None) -> bool:
    if iter_names is None:
        return any(True for _ in _steam_procs())
    return any((name or "").lower() in STEAM_NAMES for name in iter_names())


def steam_state(config_path: Path, envs: Optional[Callable[[], Iterable[Optional[dict]]]] = None) -> str:
    """"closed", "ours" (every Steam process has MANGOHUD=1 and this config file) or "other"."""
    if envs is None:
        def envs():
            for p in _steam_procs():
                try:
                    yield p.environ()
                except Exception:          # cannot read it: not known to be ours
                    yield None
    found = list(envs())
    if not found:
        return "closed"
    ours = all(e is not None and e.get("MANGOHUD") == "1" and e.get("MANGOHUD_CONFIGFILE") == str(config_path)
               for e in found)
    return "ours" if ours else "other"


def shutdown_steam(which: Callable = shutil.which, running: Callable[[], bool] = steam_running,
                   start: Optional[Callable] = None, sleep: Callable = time.sleep,
                   clock: Callable = time.monotonic, wait: float = SHUTDOWN_WAIT_SECONDS) -> Optional[str]:
    """Ask Steam to exit by itself (steam -shutdown) and wait until it is gone; None when it is, else why not."""
    steam = which("steam")
    if not steam:
        return "the steam command was not found"
    try:
        (start or (lambda c: start_detached(c, dict(os.environ))))([steam, "-shutdown"])
    except Exception as e:
        return f"steam -shutdown could not be started: {e}"
    deadline = clock() + wait
    while clock() < deadline:
        sleep(1.0)
        if not running():
            sleep(3.0)                  # its last processes end right after
            return None
    return f"Steam was still open {wait:g} s after steam -shutdown (a game or a Steam window may be waiting)"


def mangohud_installed(which: Callable = shutil.which) -> bool:
    return bool(which("mangohud"))


def start_detached(command: List[str], env: dict) -> None:
    """Steam keeps running after the benchmark: its own session, not a child the app ends."""
    subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def logging_done(folder: Path, since: float) -> bool:
    try:
        return any(p.name.endswith("_summary.csv") and p.stat().st_mtime >= since for p in Path(folder).iterdir())
    except OSError:
        return False


def move_new_logs(source: Path, dest: Path, since: float) -> List[Path]:
    """This run's logs (and summaries), written by MangoHud in the shared folder, into the run folder."""
    moved = []
    try:
        items = [p for p in Path(source).iterdir() if p.is_file() and p.suffix == ".csv"
                 and p.stat().st_mtime >= since]
    except OSError:
        return moved
    dest.mkdir(parents=True, exist_ok=True)
    for p in items:
        try:
            target = dest / p.name
            if not target.exists():
                shutil.move(str(p), str(target))
                moved.append(target)
        except OSError:
            pass
    return moved


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

    def __init__(self, which: Callable = shutil.which, start: Callable = start_detached,
                 data_dir: Optional[Path] = None):
        self.which, self.start, self._data_dir = which, start, data_dir

    def folder(self) -> Path:
        return shared_folder(self._data_dir)

    def mangohud_installed(self) -> bool:
        return mangohud_installed(self.which)

    def steam_state(self, config_path: Path) -> str:
        return steam_state(config_path)

    def shutdown(self) -> Optional[str]:
        return shutdown_steam(self.which)

    def command(self, app_id, args) -> Optional[List[str]]:
        return steam_command(app_id, args, self.which)

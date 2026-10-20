"""Close the game this app launched for a benchmark, once the benchmark is over.

Only processes that were NOT running before the launch and whose executable lies inside the game's
install folder are touched; each is remembered by pid AND start time, so a reused pid is never hit.
First a polite close: WM_CLOSE to their top-level windows, like clicking the window's X. Whatever is
still running after the grace time is ended by force (TerminateProcess). Steam itself and everything
outside the game's folder are never touched. A game running as administrator cannot be ended by this
non-elevated app; that is reported, not retried.
"""

import logging
import ntpath
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

GRACE_SECONDS = 30.0
WM_CLOSE = 0x0010


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    create_time: float
    exe: str


def _inside(path: Optional[str], folder: str) -> bool:
    """Also true for the share spelling of a mapped network drive (app/steam/launch.py folder_roots)."""
    from app.steam.launch import folder_roots, inside
    return inside(path, folder_roots(folder))


def unix_path(arg: str) -> str:
    """A Wine path on Wine's Z: drive (the whole Linux file system) as the Linux path: Z:\\home\\a -> /home/a."""
    if len(arg) > 2 and arg[0] in "Zz" and arg[1] == ":" and arg[2] in "\\/":
        return arg[2:].replace("\\", "/")
    return arg


def _in_command_line(cmdline, folder: str) -> Optional[str]:
    """Linux: a game under Proton runs as Wine (its executable is Proton's), with the game's .exe in its command
    line; the first argument inside the folder names it."""
    for arg in cmdline or []:
        path = unix_path(str(arg))
        if path.startswith("/") and _inside(path, folder):
            return path
    return None


def processes_in_folder(folder: str, iter_procs: Optional[Callable[[], Iterable]] = None,
                        linux: Optional[bool] = None) -> Dict[int, ProcInfo]:
    """{pid: ProcInfo} of running processes whose executable is inside folder (Linux: or whose command line names
    a file inside it, for Proton)."""
    linux = sys.platform.startswith("linux") if linux is None else linux
    if iter_procs is None:
        import psutil
        attrs = ["pid", "exe", "create_time"] + (["cmdline"] if linux else [])
        iter_procs = lambda: psutil.process_iter(attrs)
    found = {}
    try:
        for proc in iter_procs():
            info = getattr(proc, "info", proc)
            exe = info.get("exe")
            if linux and not _inside(exe, folder):
                exe = _in_command_line(info.get("cmdline"), folder) or exe
            if _inside(exe, folder) and info.get("pid") is not None and info.get("create_time") is not None:
                found[int(info["pid"])] = ProcInfo(int(info["pid"]), float(info["create_time"]), exe)
    except Exception:
        logger.exception("Could not list the game's processes")
    return found


def is_alive(info: ProcInfo) -> bool:
    """Running, and still the same process (same start time, so not a reused pid)."""
    import psutil
    try:
        proc = psutil.Process(info.pid)
        return proc.is_running() and abs(proc.create_time() - info.create_time) < 0.01
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True


def post_close(pids) -> int:
    """WM_CLOSE to every visible top-level window of these processes (Windows); returns how many."""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wanted, windows = set(pids), []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in wanted and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True
    user32.EnumWindows(proc_type(each), 0)
    for hwnd in windows:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return len(windows)


def terminate(info: ProcInfo) -> Optional[str]:
    """None when ended, else why not."""
    import psutil
    try:
        proc = psutil.Process(info.pid)
        if abs(proc.create_time() - info.create_time) >= 0.01:
            return None                    # that process is gone; the pid now belongs to another one
        proc.terminate()
        proc.wait(10)
        return None
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        return "access denied (the game runs as administrator?)"
    except Exception as e:
        return str(e)


def close_game(started: Dict[int, ProcInfo], grace_seconds: float = GRACE_SECONDS,
               alive: Callable[[ProcInfo], bool] = is_alive, polite: Callable = post_close,
               force: Callable[[ProcInfo], Optional[str]] = terminate,
               sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> str:
    """Close what the launch started; returns one line for the status."""
    running = [i for i in started.values() if alive(i)]
    if not running:
        return "the game was already closed"
    names = ", ".join(sorted({ntpath.basename(i.exe) for i in running}))    # ntpath: \ and / both
    polite([i.pid for i in running])
    deadline = clock() + grace_seconds
    while clock() < deadline:
        running = [i for i in running if alive(i)]
        if not running:
            return f"game closed ({names})"
        sleep(1.0)
    problems = []
    for info in running:
        problem = force(info)
        if problem:
            problems.append(f"{ntpath.basename(info.exe)}: {problem}")
    if problems:
        return f"the game could not be closed ({'; '.join(problems)}); close it yourself"
    return f"game closed by force after {grace_seconds:g} s ({names})"

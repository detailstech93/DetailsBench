"""Run one external command with a timeout and an emergency stop.

Output goes straight to files (no pipes, so a chatty tool can never block).
On timeout or stop, only the process this module started and its own child
processes are terminated (then killed after a grace period); nothing else
on the machine is touched. No shell is used.
"""

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import psutil

GRACE_SECONDS = 5.0


@dataclass
class ProcessOutcome:
    exit_code: Optional[int]
    timed_out: bool
    cancelled: bool
    duration_seconds: float
    start_error: Optional[str] = None
    terminated: List[int] = None


def terminate_tree(pid: int, grace: float = GRACE_SECONDS) -> List[int]:
    """Terminate pid and its descendants (started by it); kill what is left after grace. Returns the pids."""
    try:
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return []
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=grace)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=grace)
    return [p.pid for p in procs]


def run_process(command: List[str], stdout_path: Path, stderr_path: Path,
                timeout_seconds: Optional[float], stop_event: threading.Event,
                cwd: Optional[str] = None, poll_seconds: float = 0.1,
                env: Optional[dict] = None, stdin_path: Optional[Path] = None,
                hidden_console: bool = False) -> ProcessOutcome:
    """stdin_path: a file given to the program as its input (e.g. the answer to a question it asks).
    hidden_console (Windows): give the program a console of its own that is never shown, instead of none at all,
    for programs that print only into the console they were started from (Cinebench's "parentconsole")."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    startup = None
    if os.name == "nt" and hidden_console:
        flags = subprocess.CREATE_NEW_CONSOLE
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0                         # SW_HIDE
    start = time.monotonic()
    feed_path = stdin_path or os.devnull
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err, open(feed_path, "rb") as feed:
        try:
            proc = subprocess.Popen(command, stdout=out, stderr=err, stdin=feed,
                                    cwd=cwd, env=env, shell=False, creationflags=flags, startupinfo=startup)
        except OSError as e:
            return ProcessOutcome(None, False, False, time.monotonic() - start, start_error=str(e))
        timed_out = cancelled = False
        terminated: List[int] = []
        while proc.poll() is None:
            if stop_event.is_set():
                cancelled = True
            elif timeout_seconds is not None and time.monotonic() - start > timeout_seconds:
                timed_out = True
            if cancelled or timed_out:
                terminated = terminate_tree(proc.pid)
                proc.wait()
                break
            stop_event.wait(poll_seconds)
    return ProcessOutcome(proc.returncode, timed_out, cancelled, time.monotonic() - start,
                          terminated=terminated)

"""Keep the screen on during a run of tests, and tell when the screen is locked.

Found on the overnight run of 2026-10-20 (session 18): with the PC left alone the screen went off / was locked, and
the tests that draw on the screen could not: PassMark's 3D tests never rendered (GPU 0% for 3 x 30 min) and the
7-Zip File Manager window could not be brought to the front. Tests that only compute (CPU, GPU compute, disks) ran.

- keep_awake(): for the length of a run the app asks the system not to sleep and not to turn the screen off
  (so it does not lock after the idle time). It is a request held by the app, never a change of a setting: it ends
  with the run, or when the app ends. Windows: SetThreadExecutionState (on the thread that runs the tests); Linux:
  systemd-inhibit (idle and sleep) while the run lasts; macOS: caffeinate -d -i. Where none is there, nothing.
- screen_locked(): True when the screen is locked (Windows: the input desktop is not the user's "Default" desktop;
  Linux: logind's LockedHint), False when not, None when it cannot be told. A test with needs_desktop is not
  started on a locked screen (app/automation/runner.py): it would wait for its timeout and give nothing.
"""

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from typing import Optional

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2

LOCKED_ERROR = ("the screen was locked: this test draws on the screen and cannot run while it is locked "
                "(Windows+L, or the lock after the screen went off); leave the PC unlocked and run it again")


def _helper_command(platform: str) -> Optional[list]:
    if platform.startswith("linux") and shutil.which("systemd-inhibit"):
        return ["systemd-inhibit", "--what=idle:sleep", "--who=PC Benchmark Manager", "--why=benchmark run",
                "--mode=block", "sleep", "infinity"]
    if platform == "darwin" and shutil.which("caffeinate"):
        return ["caffeinate", "-d", "-i", "-w", str(os.getpid())]
    return None


@contextmanager
def keep_awake(platform: Optional[str] = None, kernel32=None, popen=subprocess.Popen):
    platform = platform or sys.platform
    proc = None
    held = False
    try:
        if platform == "win32":
            try:
                if kernel32 is None:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                held = bool(kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED))
            except (OSError, AttributeError):
                held = False
        else:
            command = _helper_command(platform)
            if command:
                try:
                    proc = popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                except OSError:
                    proc = None
        yield held or proc is not None
    finally:
        if held:
            try:
                kernel32.SetThreadExecutionState(ES_CONTINUOUS)     # back to normal for this thread
            except (OSError, AttributeError):
                pass
        if proc is not None:
            try:
                proc.terminate()                                    # our own child only
                proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass


def _windows_locked(user32=None) -> Optional[bool]:
    try:
        import ctypes
        from ctypes import wintypes
        user32 = user32 or ctypes.windll.user32
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        desk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not desk:
            return True                 # the lock screen (Winlogon's desktop) cannot be opened by the user
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD(0)
            if not user32.GetUserObjectInformationW(wintypes.HANDLE(desk), UOI_NAME, name, ctypes.sizeof(name),
                                                    ctypes.byref(needed)):
                return None
            return name.value.lower() != "default"
        finally:
            user32.CloseDesktop(wintypes.HANDLE(desk))
    except (OSError, AttributeError, ValueError):
        return None


def _linux_locked(run=subprocess.run) -> Optional[bool]:
    session = os.environ.get("XDG_SESSION_ID")
    if not session or not shutil.which("loginctl"):
        return None
    try:
        done = run(["loginctl", "show-session", session, "-p", "LockedHint"], capture_output=True, text=True,
                   timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (done.stdout or "").strip().partition("=")[2].lower()
    return {"yes": True, "no": False}.get(value)


def screen_locked(platform: Optional[str] = None) -> Optional[bool]:
    platform = platform or sys.platform
    if platform == "win32":
        return _windows_locked()
    if platform.startswith("linux"):
        return _linux_locked()
    return None

"""Run one command elevated through the Windows UAC prompt and wait for it.

The app itself stays non-elevated. ShellExecuteExW with the "runas" verb shows
the OS consent prompt; nothing bypasses or suppresses it. We keep the process
handle so we can wait for the exit code, enforce a timeout and try to end ONLY
that process on stop. Whether a non-elevated process may terminate the
elevated process through this handle is checked by scripts/verify_frames.py
(unverified until then); when it cannot, the outcome says so and the tool ends
by itself (PresentMon is always started with a fixed capture time).

A declined prompt is ERROR_CANCELLED (1223, unverified on this machine) and
is reported as "uac_declined", a neutral outcome rather than a failure.
"""

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

ERROR_CANCELLED = 1223
SW_SHOWMINNOACTIVE = 7     # console window appears minimized: visible, never steals focus

OUTCOMES = ("exited", "uac_declined", "start_failed", "timed_out", "stopped")


@dataclass
class ElevatedOutcome:
    status: str                        # one of OUTCOMES
    exit_code: Optional[int] = None
    error: Optional[str] = None
    terminated: Optional[bool] = None  # timed_out / stopped: did TerminateProcess succeed
    duration_seconds: float = 0.0


class WinApi:
    """Thin ctypes wrapper (Windows only). Tests use a fake with the same methods."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                        ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                        ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
                        ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                        ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

        self.Info = SHELLEXECUTEINFOW
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
        self.shell32.ShellExecuteExW.restype = wintypes.BOOL
        k = self.kernel32
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

    def shell_execute(self, file: str, params: str, cwd: Optional[str], show: int):
        """(handle, None) or (None, winerror)."""
        info = self.Info()
        info.cbSize = self.ctypes.sizeof(self.Info)
        info.fMask = 0x00000040 | 0x00000100 | 0x00000400   # NOCLOSEPROCESS | NOASYNC | FLAG_NO_UI
        info.lpVerb, info.lpFile, info.lpParameters, info.lpDirectory = "runas", file, params, cwd
        info.nShow = show
        if not self.shell32.ShellExecuteExW(self.ctypes.byref(info)):
            return None, self.ctypes.get_last_error()
        if not info.hProcess:
            return None, -1
        return info.hProcess, None

    def wait(self, handle, ms: int) -> bool:
        return self.kernel32.WaitForSingleObject(handle, ms) == 0      # WAIT_OBJECT_0

    def exit_code(self, handle) -> Optional[int]:
        code = self.wintypes.DWORD()
        return code.value if self.kernel32.GetExitCodeProcess(handle, self.ctypes.byref(code)) else None

    def terminate(self, handle):
        """(ok, winerror)."""
        ok = bool(self.kernel32.TerminateProcess(handle, 1))
        return ok, None if ok else self.ctypes.get_last_error()

    def close(self, handle):
        self.kernel32.CloseHandle(handle)


def run_elevated(executable: str, args: List[str], cwd: Optional[str], timeout_seconds: float,
                 stop_event: Optional[threading.Event] = None, api=None,
                 poll_seconds: float = 0.2, show: int = SW_SHOWMINNOACTIVE,
                 on_started: Optional[Callable[[], None]] = None) -> ElevatedOutcome:
    if api is None:
        if sys.platform != "win32":
            return ElevatedOutcome("start_failed", error="elevated launch is only implemented on Windows")
        api = WinApi()
    stop_event = stop_event or threading.Event()
    started = time.monotonic()
    handle, err = api.shell_execute(executable, subprocess.list2cmdline(args), cwd, show)
    if handle is None:
        if err == ERROR_CANCELLED:
            return ElevatedOutcome("uac_declined", error="the UAC prompt was declined")
        return ElevatedOutcome("start_failed", error=f"could not start (winerror {err})")
    if on_started is not None:
        on_started()                       # the UAC prompt was accepted and the process runs
    try:
        deadline = started + timeout_seconds
        while True:
            if api.wait(handle, int(poll_seconds * 1000)):
                return ElevatedOutcome("exited", exit_code=api.exit_code(handle),
                                       duration_seconds=time.monotonic() - started)
            if stop_event.is_set() or time.monotonic() >= deadline:
                status = "stopped" if stop_event.is_set() else "timed_out"
                ok, term_err = api.terminate(handle)
                if ok:
                    api.wait(handle, 5000)
                error = None if ok else f"could not terminate the elevated process (winerror {term_err})"
                return ElevatedOutcome(status, exit_code=api.exit_code(handle) if ok else None, error=error,
                                       terminated=ok, duration_seconds=time.monotonic() - started)
    finally:
        api.close(handle)

"""The Windows desktop as a simulation script sees it: our processes, their windows, keys, close.

Only processes started by the script (the launched one and its descendants, collected while it runs)
are ever looked at, sent keys or closed. Keys go through SendInput, which types into whatever window
has the focus, so every key chord is sent only after checking that the focused window belongs to one of
those processes; otherwise nothing is sent (the caller fails the run). Windows' focus rules may refuse
to bring our window to the front; then too no key is sent.
"""

import ctypes
import os
import subprocess
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import psutil

from app.automation.process import terminate_tree

WM_CLOSE = 0x0010
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

VK = {"alt": 0x12, "ctrl": 0x11, "shift": 0x10, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20,
      "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "backspace": 0x08, "home": 0x24, "end": 0x23}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
MODIFIERS = ("ctrl", "alt", "shift")


def parse_chord(chord: str) -> Optional[List[int]]:
    """'alt+t' -> [VK_MENU, 'T']: modifiers first. None for a name not in the table (nothing guessed)."""
    codes = []
    for part in chord.lower().split("+"):
        if part in VK:
            codes.append(VK[part])
        elif len(part) == 1 and (part.isascii() and part.isalnum()):
            codes.append(ord(part.upper()))
        else:
            return None
    return codes or None


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    cls: str
    title: str


class Win32Desktop:
    """The real desktop (Windows only)."""

    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._enum = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = w.DWORD
        self.user32.GetForegroundWindow.restype = w.HWND
        self.user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]

    # processes
    def start(self, command: List[str], cwd: Optional[str]) -> psutil.Process:
        proc = subprocess.Popen(command, cwd=cwd, shell=False, stdin=subprocess.DEVNULL)
        return psutil.Process(proc.pid)

    def tree(self, root: psutil.Process) -> List[psutil.Process]:
        try:
            return [root] + root.children(recursive=True)
        except psutil.Error:
            return []

    def terminate(self, procs: Iterable[psutil.Process]) -> List[int]:
        done = []
        for p in procs:
            try:
                if p.is_running():        # psutil also checks the pid was not reused
                    done += terminate_tree(p.pid, grace=5)
            except psutil.Error:
                pass
        return done

    # windows
    def _pid_of(self, hwnd) -> Tuple[int, int]:
        pid = self.w.DWORD()
        thread = self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value, thread

    def _text(self, hwnd) -> str:
        n = self.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        self.user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _class(self, hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def windows(self, pids: Set[int]) -> List[Window]:
        out = []

        def each(hwnd, _):
            pid, _thread = self._pid_of(hwnd)
            if pid in pids and self.user32.IsWindowVisible(hwnd):
                out.append(Window(int(hwnd), pid, self._class(hwnd), self._text(hwnd)))
            return True
        self.user32.EnumWindows(self._enum(each), 0)
        return out

    def controls(self, hwnd: int) -> Dict[int, str]:
        """Control id -> text of a window's child controls (standard Win32 dialogs; id 0 left out)."""
        out: Dict[int, str] = {}

        def each(child, _):
            cid = self.user32.GetDlgCtrlID(child)
            if cid:
                out.setdefault(cid, self._text(child))
            return True
        self.user32.EnumChildWindows(hwnd, self._enum(each), 0)
        return out

    def foreground_pid(self) -> Optional[int]:
        hwnd = self.user32.GetForegroundWindow()
        return self._pid_of(hwnd)[0] if hwnd else None

    def focus(self, hwnd: int) -> None:
        """Ask Windows to bring the window to the front (it may refuse; the caller checks)."""
        fg = self.user32.GetForegroundWindow()
        mine = self.kernel32.GetCurrentThreadId()
        other = self._pid_of(fg)[1] if fg else 0
        attached = bool(other and other != mine and self.user32.AttachThreadInput(mine, other, True))
        try:
            self.user32.ShowWindow(hwnd, 9)           # SW_RESTORE
            self.user32.BringWindowToTop(hwnd)
            self.user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                self.user32.AttachThreadInput(mine, other, False)

    def press(self, codes: List[int]) -> None:
        """Press the keys in order, release them in reverse (one SendInput call: nothing can come between)."""
        w = self.w

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("u", _U)]

        events = [(c, 0) for c in codes] + [(c, KEYEVENTF_KEYUP) for c in reversed(codes)]
        arr = (INPUT * len(events))()
        for i, (code, flags) in enumerate(events):
            arr[i].type = INPUT_KEYBOARD
            arr[i].u.ki = KEYBDINPUT(code, self.user32.MapVirtualKeyW(code, 0), flags, 0, 0)
        self.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))

    def close(self, hwnd: int) -> None:
        self.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def default_desktop():
    return Win32Desktop() if os.name == "nt" else None

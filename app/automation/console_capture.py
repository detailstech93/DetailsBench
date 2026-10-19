"""Run a command in a console and hand back what it printed into that console (Windows).

Some programs write only into the console window they were started from, never into a redirected output: Cinebench
2026 with Maxon's `start /b /wait "parentconsole"` printed nothing into the app's file in a full 12-minute run on the
owner's PC (2026-10-19, session 15; exit code 0). Its text is in the console's screen buffer, which is gone when the
console closes. So the app starts this helper in a hidden console of its own (run_process(hidden_console=True)); the
helper runs the command in that same console, waits for it, reads the screen buffer and prints it to its own output,
which the app has sent to the run's file. Nothing is shown, nothing else is read.
"""

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import List

FLAG = "--console-capture"
ROWS = 9999                     # lines the console keeps (Cinebench prints a few dozen)


class _Coord(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _Rect(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short), ("Right", ctypes.c_short),
                ("Bottom", ctypes.c_short)]


class _BufferInfo(ctypes.Structure):
    _fields_ = [("dwSize", _Coord), ("dwCursorPosition", _Coord), ("wAttributes", ctypes.c_ushort),
                ("srWindow", _Rect), ("dwMaximumWindowSize", _Coord)]


def capture_command(command: str) -> List[str]:
    """The command line that runs `command` through the helper: the packaged app, or Python with scripts/main.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, FLAG, command]
    main = Path(__file__).resolve().parents[2] / "scripts" / "main.py"
    return [sys.executable, "-I", str(main), FLAG, command]


def _console():
    """kernel32 and a handle on this process's console screen buffer (a hidden one is made if there is none)."""
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetConsoleWindow.restype = wintypes.HWND
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                              wintypes.DWORD, wintypes.HANDLE]
    for name in ("GetConsoleScreenBufferInfo", "SetConsoleScreenBufferSize", "ReadConsoleOutputCharacterW",
                 "CloseHandle"):
        getattr(k, name).restype = wintypes.BOOL
    if not k.GetConsoleWindow():
        # the packaged app has no console of its own even when started with one asked for
        if not k.AllocConsole():
            raise OSError(f"no console (error {ctypes.get_last_error()})")
        ctypes.WinDLL("user32").ShowWindow(k.GetConsoleWindow(), 0)          # SW_HIDE
    generic_rw, share_rw, open_existing = 0xC0000000, 0x3, 3
    handle = k.CreateFileW("CONOUT$", generic_rw, share_rw, None, open_existing, 0, None)
    if not handle or handle == wintypes.HANDLE(-1).value:
        raise OSError(f"could not open the console (error {ctypes.get_last_error()})")
    return k, ctypes.c_void_p(handle)


def _buffer_info(k, handle) -> _BufferInfo:
    info = _BufferInfo()
    if not k.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
        raise OSError(f"could not read the console size (error {ctypes.get_last_error()})")
    return info


def read_console(k, handle) -> str:
    """The text of the console's lines up to the cursor, trailing spaces removed."""
    info = _buffer_info(k, handle)
    width, last = info.dwSize.X, info.dwCursorPosition.Y
    buf = ctypes.create_unicode_buffer(width)
    got = ctypes.c_ulong()
    lines = []
    for row in range(last + 1):
        if not k.ReadConsoleOutputCharacterW(handle, buf, width, _Coord(0, row), ctypes.byref(got)):
            break
        lines.append(buf[:got.value].rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def helper_main(argv: List[str]) -> int:
    """`--console-capture "<command>"`: run it in this console, then print the console's text. Exit code: its own."""
    if len(argv) != 1 or sys.platform != "win32":
        print("usage (Windows): --console-capture \"<command>\"", file=sys.stderr)
        return 2
    try:
        k, handle = _console()
        width = _buffer_info(k, handle).dwSize.X
        k.SetConsoleScreenBufferSize(handle, _Coord(max(width, 120), ROWS))
    except OSError as e:
        print(f"console capture: {e}", file=sys.stderr)
        return 3
    # the command shares this console and this process's output file (what it prints the usual way lands there too)
    code = subprocess.call(argv[0], shell=False)
    text = read_console(k, handle)
    k.CloseHandle(handle)
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.flush()
    return code

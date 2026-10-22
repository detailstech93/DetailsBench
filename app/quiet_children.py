"""No black console windows from the commands the app runs (Windows, when the app has no console of its own).

Windows ARM test machine, 2026-10-22: started with pythonw.exe (as the packaged app starts too), every PowerShell or
command-line program the scan ran opened its own black window. From a console (run.bat, python.exe) the commands share
that console, so it never showed on the development PC. Each call that left out CREATE_NO_WINDOW now gets it; a call
that asks for a console of its own (CREATE_NEW_CONSOLE, DETACHED_PROCESS) keeps it. Programs with windows of their
own (Cinebench, games) are not changed by the flag."""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
_KEEP = CREATE_NO_WINDOW | CREATE_NEW_CONSOLE | DETACHED_PROCESS


def has_console() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except (OSError, AttributeError):
        return True


def flags_for(creationflags: int) -> int:
    return creationflags if creationflags & _KEEP else creationflags | CREATE_NO_WINDOW


def hide_console_windows(popen=subprocess.Popen, windows: bool = os.name == "nt", console=has_console) -> bool:
    """True when commands now start without a window."""
    if not windows or console() or getattr(popen, "_quiet_children", False):
        return False
    original = popen.__init__

    def __init__(self, *args, creationflags=0, **kwargs):
        original(self, *args, creationflags=flags_for(creationflags), **kwargs)
    popen.__init__ = __init__
    popen._quiet_children = True
    return True

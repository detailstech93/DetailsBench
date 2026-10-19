"""Whether the app runs as administrator, and starting it again as administrator (Windows only).

The owner asked for it on 2026-10-19: tests started from the phone (remote mode) must not wait for someone to
press Yes on a Windows administrator prompt at the PC. That prompt shows on Windows' secure desktop and nothing
remote can answer it, so the owner answers it once at the PC: "Restart as administrator" starts the app again
through the normal UAC prompt (ShellExecute "runas", nothing bypasses it), and the old window closes. While the
app runs as administrator, the tools it starts elevated (PassMark through app/frames/admin_helper.py, PresentMon
through app/frames/elevated.py) start without a prompt. Only until the app closes: the next start is normal.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

REMOTE_ON_FLAG = "--remote-on"         # remote mode was on before the restart: switch it on again (new code)
ERROR_CANCELLED = 1223


def is_elevated(platform: str = sys.platform) -> bool:
    if platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def restart_command(remote_on: bool = False) -> Tuple[str, str, str]:
    """(program, parameters, folder) that start this app again: the packaged app, or Python with scripts/main.py."""
    extra: List[str] = [REMOTE_ON_FLAG] if remote_on else []
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline(extra), str(Path(sys.executable).parent)
    root = Path(__file__).resolve().parents[1]
    return sys.executable, subprocess.list2cmdline(["-I", str(root / "scripts" / "main.py")] + extra), str(root)


def relaunch_elevated(remote_on: bool = False, startfile: Optional[Callable] = None) -> Optional[str]:
    """Start the app again through the UAC prompt; None when it started, else why not ("declined" when the
    owner pressed No)."""
    program, params, folder = restart_command(remote_on)
    start = startfile or getattr(os, "startfile", None)
    if start is None:
        return "only on Windows"
    try:
        start(program, "runas", params, folder)
    except OSError as e:
        if getattr(e, "winerror", None) == ERROR_CANCELLED:
            return "declined"
        return str(e)
    return None


def remote_on_requested(argv: Optional[List[str]] = None) -> bool:
    return REMOTE_ON_FLAG in (sys.argv if argv is None else argv)[1:]

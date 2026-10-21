"""Whether the app runs as administrator, and starting it again as administrator (Windows only).

The owner asked for it on 2026-10-19: tests started from the phone (remote mode) must not wait for someone to
press Yes on a Windows administrator prompt at the PC. That prompt shows on Windows' secure desktop and nothing
remote can answer it, so the owner answers it once at the PC: "Restart as administrator" starts the app again
through the normal UAC prompt (ShellExecute "runas", nothing bypasses it), and the old window closes. While the
app runs as administrator, the tools it starts elevated (PassMark through app/frames/admin_helper.py, PresentMon
through app/frames/elevated.py) start without a prompt. Only until the app closes: the next start is normal.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

REMOTE_ON_FLAG = "--remote-on"         # remote mode was on before the restart: switch it on again (new code)
REMOTE_OPEN_FLAG = "--open-remote"     # the Remote button asked for the restart: open the Remote window again
INSTALL_FLAG = "--install-tests"       # + "id1,id2": tests ticked for "Install selected" before the restart
ERROR_CANCELLED = 1223
_TEST_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def is_elevated(platform: str = sys.platform) -> bool:
    if platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def restart_command(remote_on: bool = False, install_ids: Sequence[str] = (),
                    open_remote: bool = False) -> Tuple[str, str, str]:
    """(program, parameters, folder) that start this app again: the packaged app, or Python with scripts/main.py."""
    extra: List[str] = [REMOTE_ON_FLAG] if remote_on else []
    if open_remote and not remote_on:
        extra.append(REMOTE_OPEN_FLAG)
    ids = [i for i in install_ids if _TEST_ID.match(i)]
    if ids:
        extra += [INSTALL_FLAG, ",".join(ids)]
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline(extra), str(Path(sys.executable).parent)
    root = Path(__file__).resolve().parents[1]
    return sys.executable, subprocess.list2cmdline(["-I", str(root / "scripts" / "main.py")] + extra), str(root)


def relaunch_elevated(remote_on: bool = False, startfile: Optional[Callable] = None,
                      install_ids: Sequence[str] = (), open_remote: bool = False) -> Optional[str]:
    """Start the app again through the UAC prompt; None when it started, else why not ("declined" when the
    owner pressed No)."""
    program, params, folder = restart_command(remote_on, install_ids, open_remote)
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


def remote_open_requested(argv: Optional[List[str]] = None) -> bool:
    """The restart came from the Remote button (owner, 2026-10-21: carry on where I stopped)."""
    return REMOTE_OPEN_FLAG in (sys.argv if argv is None else argv)[1:]


def install_requested(argv: Optional[List[str]] = None) -> List[str]:
    """The test ids to install after a restart as administrator (only plain ids; the install still asks first)."""
    args = (sys.argv if argv is None else argv)[1:]
    if INSTALL_FLAG not in args:
        return []
    at = args.index(INSTALL_FLAG) + 1
    return [i for i in args[at].split(",") if _TEST_ID.match(i)] if at < len(args) else []

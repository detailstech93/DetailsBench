"""Running the package manager as root on Linux: pkexec when it is there (the desktop's own password window),
else sudo with this app's own password window.

Owner, 2026-10-20 (first real Linux run, WSL2 Ubuntu 24.04): 7-Zip, HandBrake and PassMark could not be installed:
"pkexec not found". WSL has no pkexec and no polkit agent. sudo is always there, but the app has no terminal for
its password, so sudo runs with -A: it starts the program in SUDO_ASKPASS, which prints the password. That program
is a small script the app writes in its data folder; it starts this app with --askpass, which shows one password
field (askpass_main). You type the password there; the app never stores, logs or shows it, it goes from the
window straight to sudo. sudo then remembers it for a few minutes (for this app), so several installs ask once.

The command lists stay argument lists, no shell: [env, SUDO_ASKPASS=<script>, sudo, -A, <package manager>, ...].
"""

import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

ASKPASS_FLAG = "--askpass"
ASKPASS_NAME = "askpass.sh"


def program_command() -> List[str]:
    """How to start this app: the packaged program, or Python with scripts/main.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-I", str(Path(__file__).resolve().parents[2] / "scripts" / "main.py")]


def askpass_script(command: List[str]) -> str:
    return "#!/bin/sh\nexec " + " ".join(shlex.quote(c) for c in command) + f" {ASKPASS_FLAG} \"$@\"\n"


def askpass_program(folder: Optional[Path] = None, command: Optional[List[str]] = None) -> Optional[str]:
    """Write the SUDO_ASKPASS script (only the owner can read or run it); its path, or None when it cannot be."""
    if folder is None:
        from app.paths import get_data_dir
        folder = get_data_dir()
    path = Path(folder) / ASKPASS_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(askpass_script(command or program_command()), encoding="utf-8")
        os.chmod(path, 0o700)
    except OSError:
        return None
    return str(path)


def admin_prefix(which: Callable[[str], Optional[str]] = shutil.which,
                 askpass: Optional[Callable[[], Optional[str]]] = None) -> Tuple[List[str], Optional[str]]:
    """(argv prefix that runs a command as root, None) or ([], why not)."""
    pkexec = which("pkexec")
    if pkexec:
        return [pkexec], None
    sudo, env = which("sudo"), which("env")
    if not sudo or not env:
        return [], "neither pkexec nor sudo was found"
    script = (askpass or askpass_program)()
    if not script:
        return [], "the password window could not be prepared"
    return [env, f"SUDO_ASKPASS={script}", sudo, "-A"], None


def is_sudo(argv: List[str]) -> bool:
    return any(os.path.basename(a) == "sudo" for a in argv[:3])


def askpass_main(args: List[str]) -> int:
    """The password window sudo starts (its prompt is args[0]); prints the password, exit 1 when cancelled."""
    from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit
    app = QApplication.instance() or QApplication([sys.argv[0]])
    prompt = (args[0].strip() if args else "") or "Password:"
    text, ok = QInputDialog.getText(
        None, "DetailsBench: administrator password",
        "DetailsBench installs a test with your distribution's package manager, which needs your password "
        "(sudo).\nIt goes only to sudo; nothing is stored.\n\n" + prompt, QLineEdit.Password)
    del app
    if not ok:
        return 1
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return 0

"""The "Restart as administrator" button (Tests screen and the remote window), see app/elevation.py."""

import logging
import sys
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from app import elevation

logger = logging.getLogger(__name__)

QUESTION = (
    "Start the app again as administrator?\n\n"
    "Windows shows its administrator prompt once, now, here at the PC. After that PassMark and the game "
    "benchmarks (frame capture) start without a prompt, also when they are started from the phone.\n\n"
    "- This window closes and the app opens again; results are kept in the database.\n"
    "- Remote mode, if it is on, switches on again with a new code (shown here after the restart).\n"
    "- Programs the app starts run as administrator too. Open Steam yourself first, so Steam and its games "
    "do not.\n"
    "- Only until the app closes: the next start is normal.")
INSTALL_QUESTION = (
    "Install {n} tests one after the other?\n\n"
    "Restart as administrator first (recommended): Windows asks once, now, then every installer runs without "
    "its own prompt. This window closes, the app opens again, scans this PC and shows the list of what it will "
    "install (from where, how big) before anything is downloaded.\n\n"
    "Install now: Windows may ask for administrator rights for each program.")
REMOTE_RETRIES = 5                    # the old window may still hold the port for a moment


def busy(window) -> str:
    if window.tests_page.busy or window.automation_panel.is_running():
        return "Tests are running: restart when they end."
    if window.install_flow.busy() or getattr(getattr(window, "run_all_flow", None), "busy", lambda: False)():
        return "An install is running: restart when it ends."
    if window.frame_panel.is_running():
        return "A frame capture is running: restart when it ends."
    return ""


def ask(window, text: str) -> bool:
    return QMessageBox.question(window, "Restart as administrator", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def restart(window, ask: Callable = ask, relaunch: Callable = elevation.relaunch_elevated,
            warn: Optional[Callable] = None, install_ids: Sequence[str] = ()) -> bool:
    """Ask, start the app again through the UAC prompt and close this window. False when nothing changed.
    install_ids: the new window installs these tests (Install selected; it asks first again, with the list)."""
    warn = warn or (lambda text: QMessageBox.information(window, "Restart as administrator", text))
    if elevation.is_elevated():
        warn("The app already runs as administrator.")
        return False
    why = busy(window)
    if why:
        warn(why)
        return False
    if not ask(window, QUESTION):
        return False
    remote_on = window.remote.running()
    settings = window.remote.settings()
    window.remote.stop()               # frees the port for the new window
    why = relaunch(remote_on, install_ids=list(install_ids)) if install_ids else relaunch(remote_on)
    if why is not None:
        if remote_on:
            window.remote.start(settings["local_only"], settings["port"])
            window.tests_page.show_remote(window.remote.running())
        warn("The app keeps running as it is: " + ("Windows' prompt was answered No." if why == "declined"
                                                  else f"it could not start again ({why}).")
             + ("\n\nRemote mode is on again with a new code." if remote_on else ""))
        return False
    logger.info("Restarting as administrator (remote mode %s)", "on" if remote_on else "off")
    window.close()
    return True


def remote_on_after_restart(window, attempt: int = 0):
    """The app was restarted as administrator with remote mode on: switch it on again and show the new code."""
    settings = window.remote.settings()
    ok, why = window.remote.start(settings["local_only"], settings["port"])
    if not ok and attempt < REMOTE_RETRIES:
        QTimer.singleShot(1000, lambda: remote_on_after_restart(window, attempt + 1))
        return
    window.tests_page.show_remote(window.remote.running())
    if not ok:
        logger.warning("Remote mode not switched on again after the restart: %s", why)
    window.show_page("tests")
    window.edit_remote_start()


# ---- Install selected (owner, 2026-10-20: "it may ask me to restart in admin mode, no problem") ----------------
RESTART_FIRST, INSTALL_NOW = "restart", "now"


def ask_install(window, count: int) -> Optional[str]:
    from app.ui.long_question import ask_buttons
    return ask_buttons(window, "Install selected tests", INSTALL_QUESTION.format(n=count),
                       [("Restart as administrator, then install", RESTART_FIRST), ("Install now", INSTALL_NOW),
                        ("Cancel", None)])


def install_selected(window, entries: List, platform: str = sys.platform, elevated: Optional[bool] = None,
                     choose: Callable = ask_install, restart_fn: Callable = restart) -> bool:
    """Install the ticked tests (app/ui/run_all.py, install only). Windows, not administrator, several tests: offer
    to start again as administrator first, so Windows asks once instead of once per installer."""
    if not entries:
        return False
    elevated = elevation.is_elevated(platform) if elevated is None else elevated
    if platform == "win32" and not elevated and len(entries) > 1:
        choice = choose(window, len(entries))
        if choice is None:
            return False
        if choice == RESTART_FIRST:
            # already asked here; the new window shows the list and asks before anything is downloaded
            return restart_fn(window, ask=lambda *a: True, install_ids=[e.id for e in entries])
    return window.run_all_flow.start(entries, install_only=True)


def install_after_restart(window, ids: Sequence[str]) -> bool:
    """The app started again as administrator for Install selected: tick those tests again and install them
    (after the first scan, so the list is current; what got installed meanwhile is skipped)."""
    page = window.tests_page
    entries = [e for e in (page.entry(i) for i in ids) if e is not None and page.can_install(e)]
    window.show_page("tests")
    for e in entries:
        page.set_checked(e.id, True)
    if not entries:
        logger.info("Nothing left to install after the restart: %s", ", ".join(ids))
        return False
    return window.run_all_flow.start(entries, install_only=True)

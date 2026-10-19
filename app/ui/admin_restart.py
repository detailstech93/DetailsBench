"""The "Restart as administrator" button (Tests screen and the remote window), see app/elevation.py."""

import logging
from typing import Callable, Optional

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
REMOTE_RETRIES = 5                     # the old window may still hold the port for a moment


def busy(window) -> str:
    if window.tests_page.busy or window.automation_panel.is_running():
        return "Tests are running: restart when they end."
    if window.install_flow.busy():
        return "An install is running: restart when it ends."
    if window.frame_panel.is_running():
        return "A frame capture is running: restart when it ends."
    return ""


def ask(window, text: str) -> bool:
    return QMessageBox.question(window, "Restart as administrator", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def restart(window, ask: Callable = ask, relaunch: Callable = elevation.relaunch_elevated,
            warn: Optional[Callable] = None) -> bool:
    """Ask, start the app again through the UAC prompt and close this window. False when nothing changed."""
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
    why = relaunch(remote_on)
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

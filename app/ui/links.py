"""Web links (official download pages, LM Studio): the desktop's browser; on WSL with no Linux browser, Windows'
own (owner, 2026-10-20: Qt said "Unable to detect a launcher" for LM Studio's page)."""

import logging
import shutil
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)


def open_link(url: str, platform: Optional[str] = None, wsl=None, html_app=None, run=subprocess.run,
              which=shutil.which, desktop=None) -> bool:
    platform = platform or sys.platform
    if platform.startswith("linux"):
        from app.ui import run_tests
        wsl = wsl or run_tests.on_wsl
        html_app = html_app or run_tests.linux_html_app
        explorer = which("explorer.exe")
        if wsl() and not html_app() and explorer and url.startswith(("https://", "http://")):
            try:
                run([explorer, url], capture_output=True, timeout=30)    # explorer.exe answers 1 even when it opened
                return True
            except (OSError, subprocess.SubprocessError):
                logger.exception("Could not open the link in Windows")
    if desktop is None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        desktop = lambda u: QDesktopServices.openUrl(QUrl(u))
    return bool(desktop(url))

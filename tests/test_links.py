"""Web links open in Windows' browser on WSL with no Linux browser (owner, 2026-10-20). Fakes only."""

import subprocess

from app.ui.links import open_link

URL = "https://lmstudio.ai/download"


def which(name):
    return "/mnt/c/WINDOWS/explorer.exe" if name == "explorer.exe" else None


def test_wsl_without_a_linux_browser_uses_windows():
    ran, desktop = [], []
    run = lambda argv, **kw: ran.append(argv) or subprocess.CompletedProcess(argv, 1)
    assert open_link(URL, "linux", wsl=lambda: True, html_app=lambda: False, run=run, which=which,
                     desktop=desktop.append)
    assert ran == [["/mnt/c/WINDOWS/explorer.exe", URL]] and desktop == []


def test_elsewhere_the_desktop_opens_it():
    for platform, wsl, html in (("linux", lambda: False, lambda: False), ("linux", lambda: True, lambda: True),
                                ("win32", lambda: True, lambda: False)):
        opened = []
        run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("explorer"))
        assert open_link(URL, platform, wsl=wsl, html_app=html, run=run, which=which,
                         desktop=lambda u: opened.append(u) or True)
        assert opened == [URL]

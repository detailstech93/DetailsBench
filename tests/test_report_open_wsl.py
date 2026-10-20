"""WSL has no Linux browser at first (owner, 2026-10-20): the report opens in Windows' browser instead."""

import subprocess
from pathlib import Path

from app.ui import run_tests


def which(name):
    return {"wslpath": "/usr/bin/wslpath", "explorer.exe": "/mnt/c/WINDOWS/explorer.exe"}.get(name)


def test_on_wsl_the_report_opens_in_windows_with_its_windows_path():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        out = "\\\\wsl.localhost\\Ubuntu-24.04\\home\\u\\Report\\r.html\n" if argv[0].endswith("wslpath") else ""
        return subprocess.CompletedProcess(argv, 1, out, "")

    path = Path("/home/u/Report/r.html")
    assert run_tests.open_in_windows(path, platform="linux", wsl=lambda: True, run=run, which=which)
    assert calls == [["/usr/bin/wslpath", "-w", str(path)],
                     ["/mnt/c/WINDOWS/explorer.exe", "\\\\wsl.localhost\\Ubuntu-24.04\\home\\u\\Report\\r.html"]]


def test_outside_wsl_or_without_the_tools_nothing_is_opened():
    def run(*a, **k):
        raise AssertionError("nothing may run")

    path = Path("/home/u/r.html")
    assert not run_tests.open_in_windows(path, platform="linux", wsl=lambda: False, run=run, which=which)
    assert not run_tests.open_in_windows(path, platform="win32", wsl=lambda: True, run=run, which=which)
    assert not run_tests.open_in_windows(path, platform="linux", wsl=lambda: True, run=run, which=lambda n: None)


def test_wsl_is_read_from_the_kernel_name(tmp_path):
    f = tmp_path / "version"
    f.write_text("Linux version 6.6.87.2-microsoft-standard-WSL2", encoding="utf-8")
    assert run_tests.on_wsl(str(f))
    f.write_text("Linux version 6.8.0-generic (Ubuntu)", encoding="utf-8")
    assert not run_tests.on_wsl(str(f))
    assert not run_tests.on_wsl(str(tmp_path / "missing"))


def test_a_linux_browser_is_found_by_name_or_by_the_html_default():
    def run_default(answer):
        return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, answer, "")

    assert run_tests.linux_html_app(which=lambda n: "/usr/bin/firefox" if n == "firefox" else None)
    xdg = lambda n: "/usr/bin/xdg-mime" if n == "xdg-mime" else None
    assert run_tests.linux_html_app(run=run_default("firefox.desktop\n"), which=xdg)
    assert not run_tests.linux_html_app(run=run_default("\n"), which=xdg)     # owner's WSL: gio finds nothing
    assert not run_tests.linux_html_app(which=lambda n: None)


def test_on_linux_without_a_browser_windows_opens_it_first(monkeypatch):
    monkeypatch.setattr(run_tests.sys, "platform", "linux")
    monkeypatch.setattr(run_tests, "linux_html_app", lambda: False)
    opened = []
    monkeypatch.setattr(run_tests, "open_in_windows", lambda p: opened.append(p) or True)
    monkeypatch.setattr(run_tests.webbrowser, "open", lambda url: (_ for _ in ()).throw(AssertionError("gio")))
    assert run_tests.open_report(Path("/home/u/r.html")) and opened == [Path("/home/u/r.html")]

"""Restart as administrator (app/elevation.py, app/ui/admin_restart.py): the command, the UAC answer, what happens
to remote mode, and no administrator warnings on the remote page while the app runs as administrator."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import elevation
from app.catalog import TestEntry
from app.ui import admin_restart

ROOT = Path(elevation.__file__).resolve().parents[1]


def test_the_restart_command_starts_this_app_again():
    program, params, folder = elevation.restart_command()
    assert program == sys.executable and folder == str(ROOT)
    assert params.split()[0] == "-I" and "main.py" in params and elevation.REMOTE_ON_FLAG not in params
    assert elevation.restart_command(remote_on=True)[1].endswith(elevation.REMOTE_ON_FLAG)


def test_the_packaged_app_starts_itself(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "PCBenchmarkManager.exe"))
    assert elevation.restart_command(True) == (str(tmp_path / "PCBenchmarkManager.exe"), elevation.REMOTE_ON_FLAG,
                                              str(tmp_path))
    assert elevation.restart_command()[1] == ""


def _oserror(winerror):
    e = OSError("no")
    e.winerror = winerror
    return e


def test_the_uac_answer_is_reported():
    seen = []

    def start(*args):
        seen.append(args)
    assert elevation.relaunch_elevated(True, startfile=start) is None
    program, verb, params, folder = seen[0]
    assert verb == "runas" and params.endswith(elevation.REMOTE_ON_FLAG)

    def declined(*args):
        raise _oserror(elevation.ERROR_CANCELLED)

    def broken(*args):
        raise _oserror(2)
    assert elevation.relaunch_elevated(startfile=declined) == "declined"
    assert elevation.relaunch_elevated(startfile=broken) == "no"


def test_remote_on_only_with_the_flag():
    assert elevation.remote_on_requested(["main.py", elevation.REMOTE_ON_FLAG])
    assert not elevation.remote_on_requested(["main.py"])
    assert not elevation.remote_on_requested([elevation.REMOTE_ON_FLAG])        # argv[0] is the program


class FakeRemote:
    def __init__(self, on, fail=0):
        self.on, self.fail, self.starts = on, fail, []

    def running(self):
        return self.on

    def settings(self):
        return {"port": 8737, "local_only": False}

    def stop(self):
        self.on = False

    def start(self, local_only, port):
        self.starts.append((local_only, port))
        if self.fail:
            self.fail -= 1
            return False, "port in use"
        self.on = True
        return True, ""


def _window(remote_on=False, busy=False):
    shown = []
    w = SimpleNamespace(remote=FakeRemote(remote_on), closed=False, shown=shown, pages=[], dialogs=0,
                        tests_page=SimpleNamespace(busy=busy, show_remote=shown.append),
                        automation_panel=SimpleNamespace(is_running=lambda: False),
                        install_flow=SimpleNamespace(busy=lambda: False),
                        frame_panel=SimpleNamespace(is_running=lambda: False))
    w.close = lambda: setattr(w, "closed", True)
    w.show_page = w.pages.append
    w.edit_remote_start = lambda: setattr(w, "dialogs", w.dialogs + 1)
    return w


def test_yes_starts_it_again_and_closes_this_window():
    w, said, asked, got = _window(remote_on=True), [], [], []
    ok = admin_restart.restart(w, ask=lambda win, text: asked.append(text) or True,
                               relaunch=lambda remote_on: got.append(remote_on), warn=said.append)
    assert ok and w.closed and got == [True] and not w.remote.on and not said
    assert "once" in asked[0] and "Steam" in asked[0] and "new code" in asked[0]


def test_no_at_the_prompt_keeps_everything_as_it_was():
    w, said = _window(remote_on=True), []
    ok = admin_restart.restart(w, ask=lambda win, text: True, relaunch=lambda remote_on: "declined",
                               warn=said.append)
    assert not ok and not w.closed and w.remote.on and w.shown == [True]
    assert "answered No" in said[0] and "new code" in said[0]
    w, said = _window(), []
    assert not admin_restart.restart(w, ask=lambda win, text: True, relaunch=lambda remote_on: "broken",
                                     warn=said.append)
    assert "broken" in said[0] and "new code" not in said[0] and not w.remote.starts


def test_nothing_happens_while_busy_or_when_already_administrator(monkeypatch):
    w, said = _window(busy=True), []
    never = lambda *a: pytest.fail("must not ask or restart")
    assert not admin_restart.restart(w, ask=never, relaunch=never, warn=said.append)
    assert "Tests are running" in said[0]
    assert not admin_restart.restart(_window(), ask=lambda win, text: False, relaunch=never, warn=said.append)
    monkeypatch.setattr(elevation, "is_elevated", lambda platform=None: True)
    assert not admin_restart.restart(_window(), ask=never, relaunch=never, warn=said.append)
    assert "already" in said[-1]


def test_remote_mode_comes_back_after_the_restart():
    w = _window()
    admin_restart.remote_on_after_restart(w)
    assert w.remote.on and w.remote.starts == [(False, 8737)] and w.shown == [True]
    assert w.pages == ["tests"] and w.dialogs == 1               # the new code is shown at the PC
    w = _window()
    w.remote.fail = 99
    admin_restart.remote_on_after_restart(w, attempt=admin_restart.REMOTE_RETRIES)
    assert not w.remote.on and w.shown == [False] and w.dialogs == 1


def _entry(id_, ref, group="pro"):
    return TestEntry(id=id_, name=id_, group=group, mode="automatic", state="installed", state_reason="",
                     compatible=True, compatibility_reason="", verified=False, source="workload", ref=ref)


def test_no_prompt_warning_on_the_page_while_administrator():
    from app.ui.remote_control import remote_listing
    admin = [_entry("passmark_cpu", SimpleNamespace(needs_admin=True)),
             _entry("cyberpunk", SimpleNamespace(script={"steam_app": 1091500}), group="game")]
    assert all(w for _, _, w in remote_listing(admin, "win32", elevated=False))
    shown = {e.id: (why, w) for e, why, w in remote_listing(admin, "win32", elevated=True)}
    assert shown == {"passmark_cpu": ("", ""), "cyberpunk": ("", "")}


def test_the_button_shows_only_on_windows():
    from PySide6.QtWidgets import QApplication
    from app.ui.tests_page import TestsPage
    QApplication.instance() or QApplication(sys.argv)
    page = TestsPage()
    page.show_admin("linux", False)
    assert page.admin_btn.isHidden()
    page.show_admin("win32", False)
    assert not page.admin_btn.isHidden() and page.admin_btn.isEnabled()
    page.show_admin("win32", True)
    assert not page.admin_btn.isEnabled() and page.admin_btn.text() == "Administrator"

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


def test_the_tests_to_install_go_through_the_restart():
    params = elevation.restart_command(install_ids=["fio", "sevenzip_benchmark", "bad id;rm"])[1]
    assert params.endswith(f"{elevation.INSTALL_FLAG} fio,sevenzip_benchmark")
    assert elevation.install_requested(["main.py", elevation.INSTALL_FLAG, "fio,sevenzip_benchmark,x y"]) == [
        "fio", "sevenzip_benchmark"]
    assert elevation.install_requested(["main.py"]) == [] and elevation.install_requested(
        ["main.py", elevation.INSTALL_FLAG]) == []
    got = []
    assert elevation.relaunch_elevated(startfile=lambda *a: got.append(a), install_ids=["fio"]) is None
    assert got[0][1] == "runas" and got[0][2].endswith(f"{elevation.INSTALL_FLAG} fio")
    w, got = _window(), []
    assert admin_restart.restart(w, ask=lambda win, text: True, install_ids=["fio"],
                                 relaunch=lambda remote_on, install_ids=(): got.append(install_ids))
    assert got == [["fio"]] and w.closed


class FakeFlow:
    def __init__(self):
        self.started = []

    def start(self, entries, install_only=False):
        self.started.append(([e.id for e in entries], install_only))
        return True


def test_install_selected_offers_the_administrator_restart_on_windows():
    entries = [SimpleNamespace(id="fio"), SimpleNamespace(id="sevenzip_benchmark")]
    w, restarted = _window(), []
    w.run_all_flow = FakeFlow()
    restart = lambda win, ask, install_ids: restarted.append((ask(win, "q"), install_ids)) or True
    assert admin_restart.install_selected(w, entries, "win32", False, choose=lambda win, n: "restart",
                                          restart_fn=restart)
    assert restarted == [(True, ["fio", "sevenzip_benchmark"])] and w.run_all_flow.started == []
    assert admin_restart.install_selected(w, entries, "win32", False, choose=lambda win, n: "now",
                                          restart_fn=lambda *a, **k: pytest.fail("no restart"))
    assert not admin_restart.install_selected(w, entries, "win32", False, choose=lambda win, n: None)
    never = lambda *a, **k: pytest.fail("no question")
    admin_restart.install_selected(w, entries, "win32", True, choose=never)          # already administrator
    admin_restart.install_selected(w, entries[:1], "win32", False, choose=never)     # one: Windows asks once anyway
    admin_restart.install_selected(w, entries, "linux", False, choose=never)         # Linux: pkexec asks
    both = (["fio", "sevenzip_benchmark"], True)
    assert w.run_all_flow.started == [both, both, (["fio"], True), both]     # "now", administrator, one, Linux


def test_after_the_restart_the_tests_are_ticked_and_installed():
    ticked, shown = [], []
    known = {"fio": SimpleNamespace(id="fio", ok=True), "diskspd": SimpleNamespace(id="diskspd", ok=False)}
    w = _window()
    w.run_all_flow = FakeFlow()
    w.show_page = shown.append
    w.tests_page = SimpleNamespace(entry=known.get, can_install=lambda e: e.ok,
                                   set_checked=lambda i, on=True: ticked.append(i))
    assert admin_restart.install_after_restart(w, ["fio", "diskspd", "gone"])
    assert ticked == ["fio"] and shown == ["tests"] and w.run_all_flow.started == [(["fio"], True)]
    assert not admin_restart.install_after_restart(w, ["diskspd"])                  # installed meanwhile


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


def test_a_restart_asked_from_the_remote_button_opens_the_remote_window_again():
    """Owner, 2026-10-21: Remote asked for administrator, the app restarted; the new window must open Remote so
    the owner carries on where they stopped."""
    assert elevation.restart_command(open_remote=True)[1].endswith(elevation.REMOTE_OPEN_FLAG)
    assert elevation.REMOTE_OPEN_FLAG not in elevation.restart_command(remote_on=True, open_remote=True)[1]
    assert elevation.remote_open_requested(["main.py", elevation.REMOTE_OPEN_FLAG])
    assert not elevation.remote_open_requested(["main.py"])
    w, got = _window(), []
    assert admin_restart.restart(w, ask=lambda win, text: True, open_remote=True,
                                 relaunch=lambda remote_on, open_remote=False: got.append(open_remote))
    assert got == [True] and w.closed
    w = _window()
    admin_restart.remote_window_after_restart(w)
    assert w.pages == ["tests"] and w.dialogs == 1

"""One-click install of a missing test: winget output parsed and checked, the flow on the Tests screen
(fake winget and fake dialogs; nothing is ever downloaded or installed)."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.catalog import INSTALLED, MISSING, TestEntry
from app.install import winget
from app.install.plans import Host, linux_commands, plan_for
from app.ui.tests_page import RunState, TestsPage, installable
from data.installers import INSTALLERS

SHOW = """\
Found 7-Zip [7zip.7zip]
Version: 26.04
Publisher: Igor Pavlov
License Url: https://7-zip.org/license.txt
Installer:
  Installer Type: wix
  Installer Url: https://www.7-zip.org/a/7z2604-x64.msi
  Installer SHA256: 0B01334A418654293513449F61D6BFC99E5196CA371E3C3DC961EDA57CD535C6
  Release Date: 2026-10-06
"""


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


def test_show_output_is_read_and_checked_against_the_vendor_host():
    pkg = winget.parse_show(SHOW, "sevenzip_benchmark", "7zip.7zip", ["www.7-zip.org"])
    assert pkg.problem is None and (pkg.name, pkg.version, pkg.publisher) == ("7-Zip", "26.04", "Igor Pavlov")
    assert pkg.url == "https://www.7-zip.org/a/7z2604-x64.msi" and pkg.sha256 == "0b01334a" + pkg.sha256[8:]
    assert len(pkg.sha256) == 64 and pkg.installer_type == "wix"
    moved = SHOW.replace("https://www.7-zip.org/a/", "https://mirror.example.com/a/")
    assert "not on the vendor's site" in winget.parse_show(moved, "x", "7zip.7zip", ["www.7-zip.org"]).problem
    plain = SHOW.replace("https://www.7-zip.org", "http://www.7-zip.org")
    assert winget.parse_show(plain, "x", "7zip.7zip", ["www.7-zip.org"]).problem       # https only
    other_repo = SHOW.replace("https://www.7-zip.org/a/7z2604-x64.msi", "https://github.com/someone/fio/x.msi")
    assert winget.parse_show(other_repo, "x", "7zip.7zip", ["github.com"], "/axboe/fio/").problem
    github = SHOW.replace("https://www.7-zip.org/a/7z2604-x64.msi",                 # 7-Zip 26.05 on winget
                          "https://github.com/ip7z/7zip/releases/download/26.05/7z2605-arm64.exe")
    entry = INSTALLERS["sevenzip_benchmark"]["windows"]
    assert winget.parse_show(github, "x", "7zip.7zip", entry["hosts"], entry["path_prefix"]).problem is None
    assert winget.parse_show(SHOW, "x", "7zip.7zip", entry["hosts"], entry["path_prefix"]).problem is None
    assert "github.com/ip7z/7zip/" in winget.parse_show(other_repo, "x", "7zip.7zip", entry["hosts"],
                                                         entry["path_prefix"]).problem
    two = SHOW + "  Installer Url: https://www.7-zip.org/a/7z2604-arm64.msi\n"
    assert "expected exactly one" in winget.parse_show(two, "x", "7zip.7zip", ["www.7-zip.org"]).problem
    nosha = SHOW.replace("Installer SHA256", "Installer Hash")
    assert winget.parse_show(nosha, "x", "7zip.7zip", ["www.7-zip.org"]).problem
    assert "did not find" in winget.parse_show("No package found matching input criteria.", "x", "7zip.7zip",
                                               ["www.7-zip.org"]).problem
    assert "did not find" in winget.parse_show(SHOW, "x", "other.id", ["www.7-zip.org"]).problem


def test_commands_are_exact_and_never_accept_the_source_agreement():
    assert winget.show_args("fio.fio") == ["winget", "show", "--id", "fio.fio", "--exact", "--source", "winget",
                                           "--disable-interactivity"]
    args = winget.install_args("fio.fio", "3.43")
    assert args[:4] == ["winget", "install", "--id", "fio.fio"] and args[-2:] == ["--version", "3.43"]
    assert "--exact" in args and "--accept-package-agreements" in args
    assert "--accept-source-agreements" not in args and "--force" not in args and "--ignore-security-hash" not in args


def test_describe_and_install_with_a_fake_winget():
    calls = []

    def run(args, **kw):
        calls.append((args, kw["timeout"]))
        return done(SHOW)
    pkg = winget.describe("sevenzip_benchmark", run=run, size_of=lambda url: 2009088)
    assert pkg.problem is None and pkg.size_bytes == 2009088 and calls[0][1] == winget.SHOW_TIMEOUT_SECONDS
    assert "Size: 1.9 MB" in winget.confirm_text(pkg) and pkg.url in winget.confirm_text(pkg)
    assert pkg.sha256 in winget.confirm_text(pkg)
    assert winget.describe("no_such_test", run=run).problem == "no winget package is known for this test"

    def missing(args, **kw):
        raise FileNotFoundError()
    assert "not installed" in winget.describe("fio", run=missing).problem
    assert "code 3" in winget.describe("sevenzip_benchmark", run=lambda a, **k: done(SHOW, 3),
                                       size_of=lambda u: None).problem

    out = winget.install(pkg, run=lambda a, **k: done("Found 7-Zip\n  ██▒▒ 1.0 MB / 1.9 MB\nSuccessfully installed\n"))
    assert out.ok and out.message == "7-Zip 26.04 installed." and out.output == ["Found 7-Zip",
                                                                                   "Successfully installed"]
    bad = winget.install(pkg, run=lambda a, **k: done("Installer hash does not match.\n", 2316632107))
    assert not bad.ok and "Installer hash does not match." in bad.message
    # real case (PassMark, 2026-10-18): installed, yet winget ends with 0x8A15002B; its list holds the package
    listed = "Name    Id         Version Source\n------\n7-Zip   7zip.7zip  26.04   winget\n"
    calls = []

    def already(args, **k):
        calls.append(args[1])
        return done("No newer package versions are available from the configured sources.\n", 2316632107) \
            if args[1] == "install" else done(listed)
    out = winget.install(pkg, run=already)
    assert out.ok and "is installed (winget ended with code 2316632107" in out.message
    assert calls == ["install", "list"] and winget.list_args("7zip.7zip")[:3] == ["winget", "list", "--id"]
    pkg.problem = "refused"
    assert winget.install(pkg, run=lambda a, **k: pytest.fail("must not run")).ok is False


WIN_X64, WIN_ARM, WIN_X86 = Host("windows", "x64"), Host("windows", "arm64"), Host("windows", "x86")
DEBIAN_ARM, ARCH, UNKNOWN_LINUX = Host("linux", "arm64", "debian"), Host("linux", "x64", "arch"), Host("linux", "x64")


def test_every_installer_entry_is_well_formed():
    for test_id, entry in INSTALLERS.items():
        assert set(entry) <= {"windows", "linux"}, test_id
        win = entry.get("windows")
        if win and "winget" in win:
            assert win["hosts"] and all("/" not in h for h in win["hosts"]) and win["arch"], test_id
            assert set(win["arch"]) <= {"x64", "arm64", "x86"}, test_id
        for family in (entry.get("linux") or {}).get("packages", {}):
            assert family in {"debian", "fedora", "arch", "suse"}, test_id
    assert winget.steam_install_url("223850") == "steam://install/223850"
    assert winget.size_text(None) == "not given by the server" and winget.size_text(3000) == "3 KB"


def test_the_install_fits_the_os_and_the_architecture():
    assert plan_for("fio", host=WIN_X64)["kind"] == "winget"
    assert plan_for("fio", host=WIN_ARM) is None                    # winget has no arm64 fio: the page stays
    assert plan_for("passmark_performancetest", host=WIN_ARM)["winget"] == "PassMark.PerformanceTest"
    assert plan_for("passmark_performancetest", host=WIN_X86) is None
    assert plan_for("diskspd", host=WIN_ARM)["path_prefix"] == "/microsoft/diskspd/"
    assert plan_for("fio", host=DEBIAN_ARM) == {"kind": "linux", "family": "debian", "package": "fio"}
    assert plan_for("sevenzip_benchmark", host=ARCH)["package"] == "7zip"
    assert plan_for("fio", host=UNKNOWN_LINUX) is None              # unknown distribution: the page
    assert plan_for("diskspd", host=DEBIAN_ARM) is None             # Windows-only tool
    assert plan_for("passmark_performancetest", host=ARCH) is None
    assert plan_for("d3dmark", host=WIN_X64) == {"kind": "steam", "app_id": 231350}   # the free demo
    assert plan_for("d3dmark", host=ARCH) is None
    assert plan_for("steam_1593500", "steam", "1593500", host=ARCH) == {"kind": "steam", "app_id": 1593500}
    assert plan_for("steam_1593500", "steam", "1593500", host=Host("darwin", "arm64")) is None
    assert plan_for("cinebench", host=WIN_X64) is None


def test_linux_commands_go_through_pkexec():
    found = {"apt-get": "/usr/bin/apt-get", "pkexec": "/usr/bin/pkexec", "pacman": "/usr/bin/pacman"}.get
    cmds, problem = linux_commands("debian", "fio", which=found)
    assert problem is None and cmds == [["/usr/bin/pkexec", "/usr/bin/apt-get", "update"],
                                        ["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", "fio"]]
    assert linux_commands("arch", "7zip", which=found)[0] == [["/usr/bin/pkexec", "/usr/bin/pacman", "-S",
                                                               "--noconfirm", "--needed", "7zip"]]
    assert linux_commands("fedora", "fio", which=found) == ([], "dnf not found")
    # WSL (owner, 2026-10-20): no pkexec -> sudo -A with the app's own password window
    cmds, problem = linux_commands("debian", "fio", which=lambda n: None if n == "pkexec" else "/usr/bin/" + n,
                                   askpass=lambda: "/data/askpass.sh")
    root = ["/usr/bin/env", "SUDO_ASKPASS=/data/askpass.sh", "/usr/bin/sudo", "-A"]
    assert problem is None and cmds[-1] == root + ["/usr/bin/apt-get", "install", "-y", "fio"]
    assert "neither pkexec nor sudo" in linux_commands("debian", "fio", which=lambda n: "/usr/bin/apt-get"
                                                       if n == "apt-get" else None)[1]
    assert "password window could not be prepared" in linux_commands(
        "debian", "fio", which=lambda n: None if n == "pkexec" else "/usr/bin/" + n, askpass=lambda: None)[1]
    assert linux_commands("gentoo", "fio", which=found) == ([], "no package manager known for gentoo")


def test_the_password_window_script_and_sudos_answers(tmp_path):
    from app.install import linux_admin
    from app.ui.frame_capture_panel import run_commands
    path = linux_admin.askpass_program(tmp_path, ["/opt/my app/python", "-I", "/opt/app/scripts/main.py"])
    text = (tmp_path / "askpass.sh").read_text(encoding="utf-8")
    assert path == str(tmp_path / "askpass.sh")
    assert text == "#!/bin/sh\nexec '/opt/my app/python' -I /opt/app/scripts/main.py --askpass \"$@\"\n"
    if os.name != "nt":
        assert (tmp_path / "askpass.sh").stat().st_mode & 0o777 == 0o700
    sudo = ["/usr/bin/env", "SUDO_ASKPASS=/x", "/usr/bin/sudo", "-A", "/usr/bin/apt-get", "update"]
    answer = lambda code, err: (lambda argv, **k: subprocess.CompletedProcess(argv, code, "", err))
    assert run_commands([sudo], answer(1, "sudo: no password was provided\n")) == "cancelled (password window closed)"
    assert run_commands([sudo], answer(1, "sudo: 3 incorrect password attempts\n")) == "the password was not accepted"
    assert run_commands([sudo], answer(0, "")) == "done"


def entry(id, state=MISSING, source="workload", ref=None, url="https://example.org/"):
    return TestEntry(id=id, name=id, group="storage", mode="automatic", state=state, state_reason="",
                     compatible=True, compatibility_reason="", verified=True, official_url=url, source=source,
                     ref=ref)


def test_missing_tests_get_install_or_the_download_page(qapp):
    page = TestsPage(open_url=lambda url: None)
    page.host = WIN_X64
    page.set_entries([entry("fio"), entry("cinebench"), entry("diskspd", INSTALLED),
                      entry("steam_1593500", source="steam", ref={"app_id": "1593500", "name": "GoW"})])
    texts = {i: page.tree.itemWidget(page.items[i], 5) for i in ("fio", "cinebench", "steam_1593500")}
    assert texts["fio"].text() == "Install" and texts["steam_1593500"].text() == "Install"
    assert texts["cinebench"].text() == "Official download page"
    asked = []
    page.install_requested.connect(asked.append)
    texts["fio"].click()
    assert [e.id for e in asked] == ["fio"]
    assert installable(entry("fio"), WIN_X64) and not installable(entry("cinebench"), WIN_X64)
    assert not installable(entry("fio"), WIN_ARM)
    arm = TestsPage(open_url=lambda url: None)
    arm.host = WIN_ARM
    arm.set_entries([entry("fio")])
    assert arm.tree.itemWidget(arm.items["fio"], 5).text() == "Official download page"


class Window:
    def __init__(self, page):
        self.tests_page, self.scans = page, 0

    def start_scan(self):
        self.scans += 1


def flow_for(qapp, answers, describe, install, host=None, **kw):
    host = host or WIN_X64
    from app.ui.install_flow import InstallFlow
    page = TestsPage(open_url=lambda url: opened.append(url))
    page.host = host
    opened = []
    win = Window(page)
    asked = []

    def ask(parent, title, text):
        asked.append(text)
        return answers.pop(0)
    flow = InstallFlow(None, ask=ask, describe=describe, install=install, start_thread=lambda fn: fn(), **kw)
    flow.window = win
    return flow, win, asked, opened


def test_flow_asks_before_installing_then_scans_again(qapp, monkeypatch):
    from app.ui import install_flow
    warned = []
    monkeypatch.setattr(install_flow.QMessageBox, "warning", lambda *a: warned.append(a[2]))
    pkg = winget.parse_show(SHOW, "sevenzip_benchmark", "7zip.7zip", ["www.7-zip.org"])
    installed = []
    flow, win, asked, _ = flow_for(qapp, [True], lambda tid: pkg,
                                   lambda p: installed.append(p) or winget.Outcome(True, "7-Zip 26.04 installed."))
    assert flow.start(entry("sevenzip_benchmark"))
    assert "https://www.7-zip.org/a/7z2604-x64.msi" in asked[0] and installed == [pkg]
    assert win.scans == 1 and not flow.busy() and flow.state.text.startswith("7-Zip 26.04 installed.")
    assert win.tests_page.stop_btn.isEnabled() and warned == []      # Stop ends the line (owner, 2026-10-22)

    flow, win, asked, _ = flow_for(qapp, [False], lambda tid: pkg, lambda p: pytest.fail("must not install"))
    flow.start(entry("sevenzip_benchmark"))
    assert win.scans == 0 and flow.state.text == "Install cancelled; nothing was downloaded."

    refused = winget.Package("fio", "fio.fio", problem="the installer is not on the vendor's site")
    flow, win, asked, _ = flow_for(qapp, [], lambda tid: refused, lambda p: pytest.fail("must not install"))
    flow.start(entry("fio"))
    assert asked == [] and "not on the vendor's site" in warned[-1] and win.scans == 0

    def broken(p):
        raise OSError("disk full")
    flow, win, asked, _ = flow_for(qapp, [True], lambda tid: pkg, broken)
    flow.start(entry("sevenzip_benchmark"))
    assert "disk full" in warned[-1] and not flow.busy() and win.scans == 0


def test_flow_opens_steams_install_dialog_for_a_game(qapp):
    flow, win, asked, opened = flow_for(qapp, [True], lambda tid: pytest.fail("no winget for Steam"),
                                        lambda p: pytest.fail("no winget for Steam"))
    assert flow.start(entry("steam_1593500", source="steam", ref={"app_id": "1593500", "name": "GoW"}))
    assert opened == ["steam://install/1593500"] and "Steam" in asked[0]
    flow, win, asked, opened = flow_for(qapp, [False], None, None)
    assert not flow.start(entry("steam_1593500", source="steam", ref={"app_id": "1593500"})) and opened == []
    assert not flow.start(entry("cinebench"))                       # no one-click install known


def test_flow_on_linux_shows_the_commands_then_runs_them(qapp):
    ran = []
    cmds = [["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", "fio"]]
    flow, win, asked, _ = flow_for(qapp, [True], None, None, host=DEBIAN_ARM,
                                   linux_commands=lambda fam, pkg: (cmds, None),
                                   run_commands=lambda c: ran.append(c) or "done")
    assert flow.start(entry("fio"))
    assert "/usr/bin/pkexec /usr/bin/apt-get install -y fio" in asked[0] and ran == [cmds]
    assert win.scans == 1 and flow.state.text.startswith("fio installed.")
    flow, win, asked, _ = flow_for(qapp, [False], None, None, host=DEBIAN_ARM,
                                   linux_commands=lambda fam, pkg: (cmds, None),
                                   run_commands=lambda c: pytest.fail("must not run"))
    assert not flow.start(entry("fio")) and win.scans == 0


class RunAllWindow(Window):
    def __init__(self, page):
        super().__init__(page)
        self.after_scan, self.ran = [], []

    def start_scan(self):
        super().start_scan()
        waiting, self.after_scan = self.after_scan, []     # the real window runs them when the scan is done
        for fn in waiting:
            fn()

    def run_tests(self, entries):
        self.ran.append([e.id for e in entries])


def run_all_for(qapp, choice, entries, describe=None, install=None):
    from app.ui.run_all import RunAllFlow
    page = TestsPage(open_url=lambda url: None)
    page.host = WIN_X64
    page.set_entries(entries)
    win = RunAllWindow(page)
    asked = []
    flow = RunAllFlow(None, ask=lambda parent, text: asked.append(text) or choice,
                      describe=describe or (lambda tid: pytest.fail("no check expected")),
                      install=install or (lambda p: pytest.fail("no install expected")),
                      start_thread=lambda fn: fn())
    flow.window = win
    return flow, win, asked


def ready(id):
    return entry(id, state=INSTALLED)


def test_run_all_installs_the_missing_tests_first_then_runs_everything(qapp, monkeypatch):
    pkg = winget.parse_show(SHOW, "sevenzip_benchmark", "7zip.7zip", ["www.7-zip.org"])
    refused = winget.Package("fio", "fio.fio", problem="winget did not find the package fio.fio")
    installed = []
    entries = [ready("diskspd"), entry("sevenzip_benchmark"), entry("fio"), entry("cinebench"),
               entry("steam_397540", source="steam", ref={"app_id": "397540", "name": "BL3"})]
    flow, win, asked = run_all_for(qapp, "install", entries,
                                   describe=lambda tid: pkg if tid == "sevenzip_benchmark" else refused,
                                   install=lambda p: installed.append(p) or winget.Outcome(True, "7-Zip installed."))
    # after the install and the scan, 7-Zip can run (the real scan would show it installed)
    monkeypatch.setattr(TestEntry, "can_run",
                        lambda self: self.state == INSTALLED or (self.id == "sevenzip_benchmark" and bool(installed)))
    assert flow.start(entries)
    text = asked[0]
    assert "https://www.7-zip.org/a/7z2604-x64.msi" in text and "winget did not find" in text
    assert "Not downloaded here" in text and "steam_397540" in text      # Steam games are only named
    assert "cinebench" not in text                                         # no one-click install: not offered
    assert installed == [pkg] and win.scans == 1
    assert win.ran == [["diskspd", "sevenzip_benchmark"]] and not flow.busy()


def test_run_all_can_run_only_the_installed_tests_or_be_cancelled(qapp):
    pkg = winget.parse_show(SHOW, "sevenzip_benchmark", "7zip.7zip", ["www.7-zip.org"])
    entries = [ready("diskspd"), entry("sevenzip_benchmark")]
    flow, win, asked = run_all_for(qapp, "installed_only", entries, describe=lambda tid: pkg)
    flow.start(entries)
    assert win.scans == 0 and win.ran == [["diskspd"]]
    flow, win, asked = run_all_for(qapp, None, entries, describe=lambda tid: pkg)
    flow.start(entries)
    assert win.ran == [] and flow.state.text == "Cancelled; nothing was downloaded or run."
    flow, win, asked = run_all_for(qapp, "install", [ready("diskspd")])
    flow.start([ready("diskspd")])
    assert asked == [] and win.ran == [["diskspd"]]                 # nothing missing: no question


def test_ticked_tests_that_are_not_installed_are_installed_with_one_button(qapp):
    """Owner, 2026-10-20: tick several tests that are not installed, then Install all."""
    from PySide6.QtCore import Qt
    page = TestsPage(open_url=lambda url: None)
    page.host = WIN_X64
    page.set_entries([ready("diskspd"), entry("fio"), entry("sevenzip_benchmark"), entry("cinebench"),
                      entry("steam_397540", source="steam", ref={"app_id": "397540", "name": "BL3"})])
    tickable = {i for i, item in page.items.items() if item.flags() & Qt.ItemIsUserCheckable}
    assert tickable == {"diskspd", "fio", "sevenzip_benchmark"}   # no one-click install / Steam: not ticked
    assert not page.install_btn.isEnabled()
    page.select_all_missing()
    page.set_checked("diskspd")
    assert page.install_btn.text() == "Install selected (2)..." and page.install_btn.isEnabled()
    assert page.run_btn.text() == "Run selected (1)..."            # only what can run is run
    asked = []
    page.install_many_requested.connect(asked.append)
    page.install_btn.click()
    assert [[e.id for e in a] for a in asked] == [["fio", "sevenzip_benchmark"]]
    page.set_scanning(True)
    assert not page.install_btn.isEnabled()


def test_install_selected_installs_then_scans_and_runs_nothing(qapp):
    pkg = winget.parse_show(SHOW, "sevenzip_benchmark", "7zip.7zip", ["www.7-zip.org"])
    installed, modes = [], []
    entries = [entry("sevenzip_benchmark")]
    flow, win, asked = run_all_for(qapp, "install", entries, describe=lambda tid: pkg,
                                   install=lambda p: installed.append(p) or winget.Outcome(True, "7-Zip installed."))
    flow.ask = lambda parent, text, install_only=False: modes.append(install_only) or asked.append(text) or "install"
    assert flow.start(entries, install_only=True)
    assert modes == [True] and "selected tests are not installed" in asked[0] and "Then all tests run" not in asked[0]
    assert installed == [pkg] and win.scans == 1 and win.ran == [] and not flow.busy()
    assert flow.state.text.startswith("Installs done: 7-Zip installed.")
    flow, win, asked = run_all_for(qapp, None, entries, describe=lambda tid: pkg)
    flow.ask = lambda parent, text, install_only=False: None
    flow.start(entries, install_only=True)
    assert win.scans == 0 and win.ran == [] and flow.state.text == "Cancelled; nothing was downloaded."
    refused = winget.Package("fio", "fio.fio", problem="winget did not find the package fio.fio")
    flow, win, asked = run_all_for(qapp, "install", [entry("fio")], describe=lambda tid: refused)
    flow.ask = lambda *a, **k: pytest.fail("nothing can be installed: no question")
    flow.start([entry("fio")], install_only=True)
    assert win.scans == 0 and win.ran == [] and "winget did not find" in flow.state.text


def test_passmark_is_found_by_its_real_names():
    import re
    from data.benchmark_definitions import BENCHMARK_DEFINITIONS
    d = next(x for x in BENCHMARK_DEFINITIONS if x.id == "passmark_performancetest")
    name = re.compile("|".join(d.name_patterns), re.IGNORECASE)
    # the uninstall entry and the folder as PerformanceTest 11.1 writes them (2026-10-18)
    assert name.search("PerformanceTest 11") and name.search("PerformanceTest") and name.search("PerformanceTest64.exe")
    assert not name.search("PerformanceTest_Help") and not name.search("My PerformanceTest Notes")


def test_an_install_from_the_remote_page_asks_nothing_at_the_pc(qapp, monkeypatch):
    """The owner saw the same question on the phone and pressed Install there (app/ui/remote_control.py)."""
    from app.ui import install_flow
    warned = []
    monkeypatch.setattr(install_flow.QMessageBox, "warning", lambda *a: warned.append(a[2]))
    pkg = winget.parse_show(SHOW, "sevenzip_benchmark", "7zip.7zip", ["www.7-zip.org"])
    text, why = install_flow.question_text(entry("sevenzip_benchmark"), WIN_X64, describe=lambda tid: pkg)
    assert "https://www.7-zip.org/a/7z2604-x64.msi" in text and "administrator prompt" in text and why == ""
    installed = []
    flow, win, asked, _ = flow_for(qapp, [], lambda tid: pkg,
                                   lambda p: installed.append(p) or winget.Outcome(True, "7-Zip 26.04 installed."))
    assert flow.start(entry("sevenzip_benchmark"), confirmed=True)
    assert asked == [] and installed == [pkg] and win.scans == 1
    flow, win, asked, _ = flow_for(qapp, [], lambda tid: pkg, lambda p: winget.Outcome(False, "disk full"))
    assert flow.start(entry("sevenzip_benchmark"), confirmed=True)
    assert warned == [] and "disk full" in flow.state.text            # the page reads it; no box at the PC
    # Steam's dialog needs someone at the PC: never from the page (Linux packages can: their password stays at the PC)
    steam = entry("steam_1593500", source="steam", ref={"app_id": "1593500", "name": "GoW"})
    assert install_flow.question_text(steam, WIN_X64)[0] is None
    flow, win, asked, opened = flow_for(qapp, [], None, None)
    assert not flow.start(steam, confirmed=True) and opened == []
    assert install_flow.question_text(entry("cinebench"), WIN_X64)[0] is None


def test_geekbench_6_comes_back_through_winget():
    # owner, 2026-10-20: Geekbench 6 is listed again; winget installs Primate Labs' own setup (x64 only there)
    plan = plan_for("geekbench_6", host=WIN_X64)
    assert plan["kind"] == "winget" and plan["winget"] == "PrimateLabs.Geekbench.6"
    assert plan["hosts"] == ["cdn.geekbench.com"]


def test_install_several_answered_on_the_remote_page_asks_nothing_at_the_pc(qapp):
    """Owner, 2026-10-21: Install several from the phone; the list was answered there."""
    pkg = winget.parse_show(SHOW, "sevenzip_benchmark", "7zip.7zip", ["www.7-zip.org"])
    installed = []
    entries = [entry("sevenzip_benchmark")]
    flow, win, asked = run_all_for(qapp, None, entries, describe=lambda tid: pkg,
                                   install=lambda p: installed.append(p) or winget.Outcome(True, "7-Zip installed."))
    flow.ask = lambda *a, **k: pytest.fail("answered on the page: no question at the PC")
    assert flow.start(entries, confirmed=True)
    assert installed == [pkg] and win.scans == 1 and win.ran == [] and flow.install_only
    assert flow.state.text.startswith("Installs done: 7-Zip installed.")


def test_a_linux_package_can_be_installed_from_the_phone_but_the_password_stays_at_the_pc(monkeypatch):
    from app.ui import install_flow
    plan = {"kind": "linux", "family": "debian", "package": "fio"}
    monkeypatch.setattr(install_flow, "install_plan", lambda e, host=None: plan)
    monkeypatch.setattr(install_flow.plans, "linux_commands",
                        lambda family, package: ([["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", package]],
                                                 None))
    text, why = install_flow.question_text(entry("fio"))
    assert not why and "/usr/bin/apt-get install -y fio" in text and "someone must type it at the PC" in text
    assert "linux" in install_flow.REMOTE_KINDS
    monkeypatch.setattr(install_flow.plans, "linux_commands", lambda family, package: ([], "apt-get not found"))
    assert install_flow.question_text(entry("fio")) == (None, "apt-get not found")
    monkeypatch.setattr(install_flow, "install_plan", lambda e, host=None: {"kind": "steam", "app_id": "1"})
    assert install_flow.question_text(entry("fio"))[0] is None


def _models(monkeypatch, warn=("llama_benchy_qwen38_27b",)):
    from app.ui import run_all
    items = []
    for tid in ("llama_benchy_qwen35_4b", "llama_benchy_qwen38_27b"):
        item = run_all.Item(entry(tid), {"kind": "lmstudio", "test_id": tid})
        item.warning = "Warning: this model most likely does not fit. It needs 21 GB. Download it anyway?" \
            if tid in warn else ""
        items.append(item)
    monkeypatch.setattr(run_all, "collect", lambda entries, host: (items, []))
    monkeypatch.setattr(run_all.RunAllFlow, "_check", lambda self, its: its)
    return [i.entry for i in items]


def test_a_model_that_may_not_fit_is_warned_first_and_can_be_left_out(qapp, monkeypatch):
    # owner, 2026-10-22: three models ticked; the 27B's warning was lost inside the list
    from app.ai import model_set
    from app.ui import run_all
    entries = _models(monkeypatch)
    installed = []
    monkeypatch.setattr(model_set, "install", lambda tid, stop=None: installed.append(tid) or
                        winget.Outcome(True, f"{tid} is ready."))
    flow, win, asked = run_all_for(qapp, None, entries)
    seen = []
    flow.ask = lambda parent, text, install_only=False, **kw: seen.append((text, kw)) or run_all.INSTALL_FITTING
    assert flow.start(entries, install_only=True)
    text, kw = seen[0]
    assert text.startswith("WARNING: this model most likely does not fit on this computer:\n"
                           "- llama_benchy_qwen38_27b: It needs 21 GB.")
    assert "llama_benchy_qwen38_27b: Download" in text and "MOST LIKELY DOES NOT FIT" in text
    assert kw == {"without": "llama_benchy_qwen38_27b"}
    assert installed == ["llama_benchy_qwen35_4b"] and win.ran == []


def test_stop_ends_a_model_download_and_starts_nothing_after_it(qapp, monkeypatch):
    from app.ai import model_set
    entries = _models(monkeypatch, warn=())
    calls = []

    def install(tid, stop=None):
        calls.append(tid)
        flow.page_stop()                                    # Stop pressed during the first download
        assert stop.is_set()
        return winget.Outcome(False, f"{tid}: the download was stopped.")
    monkeypatch.setattr(model_set, "install", install)
    flow, win, asked = run_all_for(qapp, "install", entries)
    flow.page_stop = lambda: win.tests_page.stop_btn.click()
    assert flow.start(entries)
    assert win.tests_page.stop_btn.text() == "Stopping..." or not flow.busy()
    assert calls == ["llama_benchy_qwen35_4b"] and win.ran == [] and not flow.busy()
    assert "not installed (stopped)" in flow.state.text and "No test was run" in flow.state.text


def test_a_stopped_command_is_killed(tmp_path):
    import threading
    import time
    from app.ai import model_set
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    start = time.monotonic()
    code, text = model_set._run([sys.executable, "-c", "import time; time.sleep(30)"], 60, stop)
    assert (code, text) == (-1, model_set.STOPPED) and time.monotonic() - start < 10
    assert model_set._run([sys.executable, "-c", "print('hi')"], 30) == (0, "hi")


# ---- downloads waiting in line, Skip and Stop during installs (owner, 2026-10-22) ---------------------------------
def _line(qapp, monkeypatch, answers=None):
    """An InstallFlow whose downloads finish only when the test says so (threads kept in `pending`)."""
    from app.ai import model_set
    from app.ui import install_flow, tests_page
    monkeypatch.setattr(install_flow, "install_plan", lambda e, host=None: {"kind": "lmstudio", "test_id": e.id})
    monkeypatch.setattr(tests_page, "needs_lmstudio", lambda e, host=None, **k: False)
    monkeypatch.setattr(install_flow.QMessageBox, "warning", lambda *a: warned.append(a[2]))
    warned, done = [], []

    def install(tid, progress=None, stop=None):
        done.append((tid, stop.is_set()))
        return winget.Outcome(False, f"{tid}: the download was stopped.") if stop.is_set() else \
            winget.Outcome(True, f"{tid} is ready.")
    monkeypatch.setattr(model_set, "install", install)
    monkeypatch.setattr(model_set, "install_text", lambda tid, fit=None: f"Download {tid}?")
    pending = []
    flow, win, asked, _ = flow_for(qapp, list(answers or [True]), None, None)
    flow.start_thread = pending.append
    return flow, win, asked, pending, done, warned


def test_install_during_an_install_waits_in_line(qapp, monkeypatch):
    flow, win, asked, pending, done, warned = _line(qapp, monkeypatch)
    assert flow.start(entry("model_a")) and len(asked) == 1 and flow.queue_open()
    assert flow.enqueue(entry("model_b")) == (True, "model_b is in line (1): it downloads after the one going on.")
    assert flow.enqueue(entry("model_b"))[0] is False and flow.enqueue(entry("model_a"))[0] is False
    assert flow.queued_ids() == ["model_b"]
    pending.pop(0)()                                         # model_a done: model_b starts, not asked again
    assert len(asked) == 1 and flow.busy() and flow.entry.id == "model_b" and win.scans == 0
    pending.pop(0)()
    assert done == [("model_a", False), ("model_b", False)] and not flow.busy() and win.scans == 1
    assert flow.state.text == "model_a is ready. model_b is ready. Checking the installed tests again..."


def test_skip_ends_the_download_and_starts_the_next(qapp, monkeypatch):
    flow, win, asked, pending, done, warned = _line(qapp, monkeypatch)
    flow.start(entry("model_a"))
    flow.enqueue(entry("model_b"))
    page = win.tests_page                                    # owner, 2026-10-22: it says what it skips
    assert page.skip_btn.text() == "Skip this download" and page.skip_what == "download"
    flow._skip()
    pending.pop(0)()
    assert flow.entry.id == "model_b" and flow.busy() and warned == []
    pending.pop(0)()
    assert done == [("model_a", True), ("model_b", False)] and win.scans == 1


def test_stop_ends_the_download_and_empties_the_line(qapp, monkeypatch):
    flow, win, asked, pending, done, warned = _line(qapp, monkeypatch)
    flow.start(entry("model_a"))
    flow.enqueue(entry("model_b"))
    flow._stop()
    assert flow.queued_ids() == [] and not flow.queue_open()
    assert flow.enqueue(entry("model_c"))[0] is False
    pending.pop(0)()
    assert done == [("model_a", True)] and pending == [] and not flow.busy() and warned == [] and win.scans == 0


def test_the_tests_screen_keeps_install_for_downloads_that_can_wait(qapp):
    page = TestsPage(open_url=lambda url: None)
    page.host = WIN_X64
    page.set_entries([entry("sevenzip_benchmark"), entry("fio")])
    page.can_queue = lambda e=None: e is None or e.id == "sevenzip_benchmark"
    asked = []
    page.queue_requested.connect(lambda e: asked.append(e.id))
    page.install_requested.connect(lambda e: pytest.fail("not while busy"))
    page.set_busy(True)
    seven, fio = (page.tree.itemWidget(page.items[i], 5) for i in ("sevenzip_benchmark", "fio"))
    assert page.tree.isEnabled() and seven.isEnabled() and not fio.isEnabled()
    seven.click()
    assert asked == ["sevenzip_benchmark"]
    page.show_queue(["sevenzip_benchmark"])
    assert seven.text() == "In line (1)" and not seven.isEnabled()
    page.set_busy(False)
    assert seven.text() == "Install" and seven.isEnabled() and fio.isEnabled()
    page.can_queue = lambda e=None: False                  # a test run: everything stays greyed out
    page.set_busy(True)
    assert not page.tree.isEnabled() and not seven.isEnabled()


def test_install_selected_takes_more_models_while_it_installs(qapp, monkeypatch):
    from app.ai import model_set
    from app.ui import run_all
    entries = _models(monkeypatch, warn=())
    monkeypatch.setattr(run_all, "install_plan", lambda e, host=None: {"kind": "lmstudio", "test_id": e.id})
    monkeypatch.setattr(run_all, "check_items", lambda items, *a: items)
    installed = []
    flow = None

    def install(tid, stop=None):
        if tid == "llama_benchy_qwen35_4b":                  # pressed during the first download
            assert flow.enqueue(entry("llama_benchy_gemma4_12b"))[0]
            assert flow.queued_ids() == ["llama_benchy_qwen38_27b", "llama_benchy_gemma4_12b"]
            assert flow.enqueue(entry("llama_benchy_qwen38_27b"))[1].endswith("is already in line.")
        installed.append(tid)
        return winget.Outcome(True, f"{tid} is ready.")
    monkeypatch.setattr(model_set, "install", install)
    flow, win, asked = run_all_for(qapp, None, entries)
    flow.ask = lambda parent, text, install_only=False, **kw: run_all.INSTALL
    assert flow.start(entries, install_only=True)
    assert installed == ["llama_benchy_qwen35_4b", "llama_benchy_qwen38_27b", "llama_benchy_gemma4_12b"]
    assert not flow.queue_open() and flow.queued_ids() == []


def test_a_plan_shows_a_dot_by_each_test(qapp, monkeypatch):
    # owner, 2026-10-22: green blinking while it runs, green when done, red when it failed, none before
    from app.ui import tests_page as tp
    page = TestsPage(open_url=lambda url: None)
    page.host = WIN_X64
    page.set_entries([entry("fio", INSTALLED), entry("diskspd", INSTALLED), entry("cinebench", INSTALLED)])
    marks = {"fio": "ok", "diskspd": "running"}
    page.watch(lambda: RunState(True, 1, 3, "x", marks=marks), lambda: None)
    clock = [0.1]
    monkeypatch.setattr(tp.time, "monotonic", lambda: clock[0])
    page.refresh_progress()
    icon = lambda i: page.items[i].icon(0)
    assert icon("fio").cacheKey() == tp._dot("#2e7d32").cacheKey()
    assert icon("diskspd").cacheKey() == tp._dot("#2e7d32").cacheKey() and icon("cinebench").isNull()
    assert page.scrolled_to == "diskspd"                   # the list follows the running test
    green = tp._dot("#2e7d32").pixmap(12, 12, tp.QIcon.Disabled).toImage().pixelColor(6, 6)
    assert green.green() > green.red() + 40                # still green on the locked rows, not grey
    clock[0] = 0.6                                         # half a second later: the running dot is off
    page.refresh_progress()
    assert icon("diskspd").cacheKey() == tp._dot(None).cacheKey()
    marks.update(diskspd="failed")
    page.refresh_progress()
    assert icon("diskspd").cacheKey() == tp._dot("#c62828").cacheKey()
    assert "Failed" in page.items["diskspd"].toolTip(0)
    page.fill()                                            # a new scan keeps the dots of the last run
    assert icon("fio").cacheKey() == tp._dot("#2e7d32").cacheKey()
    page.watch(lambda: RunState(True, 0, 1, "y"), lambda: None)     # the next run starts clean
    assert icon("fio").isNull()
    page.timer.stop()


def test_the_app_downloading_now_has_a_blue_dot(qapp, monkeypatch):
    # owner, 2026-10-22: a blue blinking dot by the app that downloads or installs now
    from app.ui import tests_page as tp
    page = TestsPage(open_url=lambda url: None)
    page.host = WIN_X64
    page.set_entries([entry("fio", MISSING), entry("diskspd", MISSING)])
    state = [RunState(True, 0, None, "Downloading fio")]
    page.watch(lambda: tp.with_install_dot(state[0], "fio"), lambda: None, skip_what="download")
    monkeypatch.setattr(tp.time, "monotonic", lambda: 0.1)
    page.refresh_progress()
    assert page.items["fio"].icon(0).cacheKey() == tp._dot("#1e88e5").cacheKey()
    assert page.items["diskspd"].icon(0).isNull() and page.scrolled_to == "fio"
    state[0] = RunState(False, 0, None, "fio installed.")          # done: the dot goes away
    page.refresh_progress()
    assert page.items["fio"].icon(0).isNull()
    page.timer.stop()


def test_one_program_for_several_tests_is_installed_once(qapp):
    # Windows ARM, 2026-10-22: PassMark was installed (and reported) once per PassMark test
    pkg = winget.Package("passmark_cpu", "PassMark.PerformanceTest")
    installed = []
    entries = [entry("passmark_cpu"), entry("passmark_memory"), entry("passmark_2d")]
    flow, win, asked = run_all_for(qapp, "install", entries, describe=lambda tid: pkg,
                                   install=lambda p: installed.append(p) or winget.Outcome(True, "PassMark installed."))
    flow.ask = lambda parent, text, install_only=False: "install"
    assert flow.start(entries, install_only=True)
    assert installed == [pkg] and flow.state.text.count("PassMark installed.") == 1


def test_install_selected_shows_the_download_in_mb_and_on_the_bar(qapp, monkeypatch):
    # Windows ARM, 2026-10-22: Cinebench's download in Install selected sat at 0% with no MB
    import threading
    from app.install import direct
    from app.ui.tests_page import progress_percent
    flow, win, asked = run_all_for(qapp, "install", [entry("cinebench_2024")])
    flow.item_cancel = threading.Event()
    flow.state = RunState(True, 1, 4, "Installing Cinebench...")
    seen = []

    def install(name, plan, progress):
        progress(50_000_000, 200_000_000)
        seen.append((flow.state.text, progress_percent(flow.state)))
        return winget.Outcome(True, "Cinebench installed.")
    monkeypatch.setattr(direct, "install", install)
    item = SimpleNamespace(entry=entry("cinebench_2024"), plan={"kind": "zip"})
    assert flow.install_zip(item).ok
    assert seen == [("Downloading cinebench_2024: 50 of 200 MB", 31)]      # (1 + 0.25) of 4 tests


def test_a_skipped_download_says_skipped_and_the_rest_install(qapp, monkeypatch):
    # Windows ARM, 2026-10-22: Skip on Cinebench read "stopped by user", as if everything had stopped
    from app.install import direct
    from app.ui.install_flow import Stopped
    from app.ui.run_all import Item
    flow, win, asked = run_all_for(qapp, "install", [entry("cinebench_2026")])

    def install(name, plan, progress):
        if name == "cinebench_2026":
            flow._skip()
            try:
                progress(1, 10)
            except Stopped as e:                    # as direct.install reports it
                return winget.Outcome(False, f"{name} was not installed: download failed: {e}")
        return winget.Outcome(True, f"{name} installed.")
    monkeypatch.setattr(direct, "install", install)
    items = [Item(entry("cinebench_2026"), {"kind": "zip", "folder": "c"}),
             Item(entry("handbrake_x264_1080p"), {"kind": "zip", "folder": "h"})]
    flow.pending, flow.installing = items, True
    assert flow._install(items) == ["cinebench_2026: skipped, not installed.", "handbrake_x264_1080p installed."]


def test_tests_of_one_program_show_its_line_and_dot_together(qapp, monkeypatch):
    # Fedora, 2026-10-22: Blender CPU said "In line" / had the dot, Blender GPU still offered Install
    from app.ui import tests_page as tp
    page = TestsPage(open_url=lambda url: None)
    page.host = WIN_X64
    page.can_queue = lambda e=None: True
    page.set_entries([entry("passmark_cpu"), entry("passmark_memory"), entry("diskspd"), entry("fio")])
    assert page.siblings["passmark_cpu"] == ["passmark_cpu", "passmark_memory"]
    page.set_busy(True)
    page.show_queue(["passmark_cpu", "fio"])
    text = lambda tid: page.tree.itemWidget(page.items[tid], 5).text()
    assert text("passmark_cpu") == text("passmark_memory") == "In line (1)" and text("fio") == "In line (2)"
    page.show_queue([])
    state = [RunState(True, 0, None, "Installing PassMark"), "passmark_cpu"]
    page.watch(lambda: tp.with_install_dot(state[0], state[1]), lambda: None, skip_what="download")
    page.busy = True
    monkeypatch.setattr(tp.time, "monotonic", lambda: 0.1)
    page.refresh_progress()
    assert page.items["passmark_memory"].icon(0).cacheKey() == tp._dot("#1e88e5").cacheKey()
    assert text("passmark_memory") == "Installing..." and text("diskspd") == "Install"
    state[:] = [RunState(True, 1, 2, "Installing DiskSpd"), "diskspd"]
    page.refresh_progress()
    assert text("passmark_memory") == "Install"
    page.timer.stop()


def test_a_steps_percent_moves_the_bar():
    from app.ui.tests_page import text_share
    assert text_share("Downloading Blender 5.2.0... 70 %") == 0.7 and text_share("Extracting...") is None

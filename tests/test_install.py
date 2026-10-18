"""One-click install of a missing test: winget output parsed and checked, the flow on the Tests screen
(fake winget and fake dialogs; nothing is ever downloaded or installed)."""

import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.catalog import INSTALLED, MISSING, TestEntry
from app.install import winget
from app.ui.tests_page import TestsPage, installable
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
    pkg.problem = "refused"
    assert winget.install(pkg, run=lambda a, **k: pytest.fail("must not run")).ok is False


def test_every_installer_entry_is_well_formed():
    for test_id, entry in INSTALLERS.items():
        assert ("winget" in entry) != ("steam" in entry), test_id
        if "winget" in entry:
            assert entry["hosts"] and all("/" not in h for h in entry["hosts"]), test_id
    assert winget.plan_for("fio") == INSTALLERS["fio"]
    assert winget.plan_for("steam_1593500", "steam", "1593500") == {"steam": 1593500}
    assert winget.plan_for("cinebench") is None
    assert winget.steam_install_url("223850") == "steam://install/223850"
    assert winget.size_text(None) == "not given by the server" and winget.size_text(3000) == "3 KB"


def entry(id, state=MISSING, source="workload", ref=None, url="https://example.org/"):
    return TestEntry(id=id, name=id, group="storage", mode="automatic", state=state, state_reason="",
                     compatible=True, compatibility_reason="", verified=True, official_url=url, source=source,
                     ref=ref)


def test_missing_tests_get_install_or_the_download_page(qapp):
    page = TestsPage(open_url=lambda url: None)
    page.set_entries([entry("fio"), entry("cinebench"), entry("diskspd", INSTALLED),
                      entry("steam_1593500", source="steam", ref={"app_id": "1593500", "name": "GoW"})])
    texts = {i: page.tree.itemWidget(page.items[i], 5) for i in ("fio", "cinebench", "steam_1593500")}
    assert texts["fio"].text() == "Install" and texts["steam_1593500"].text() == "Install"
    assert texts["cinebench"].text() == "Official download page"
    asked = []
    page.install_requested.connect(asked.append)
    texts["fio"].click()
    assert [e.id for e in asked] == ["fio"]
    assert installable(entry("fio")) and not installable(entry("cinebench"))


class Window:
    def __init__(self, page):
        self.tests_page, self.scans = page, 0

    def start_scan(self):
        self.scans += 1


def flow_for(qapp, answers, describe, install):
    from app.ui.install_flow import InstallFlow
    page = TestsPage(open_url=lambda url: opened.append(url))
    opened = []
    win = Window(page)
    asked = []

    def ask(parent, title, text):
        asked.append(text)
        return answers.pop(0)
    flow = InstallFlow(None, ask=ask, describe=describe, install=install, start_thread=lambda fn: fn())
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
    assert win.tests_page.stop_btn.isEnabled() is False and warned == []

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

"""Linux one-click installs: Google Chrome for the web tests (checked against Google's own index) and LM Studio
(its official install.sh) for the AI models. Nothing is downloaded or run here: openers and runs are fakes."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app import catalog
from app.ai import lmstudio, lmstudio_linux
from app.catalog import MISSING, TestEntry
from app.install import chrome_linux, plans, winget
from app.install.plans import Host

LINUX = Host("linux", "x64", "debian")
SHA = "8fbd2ae69cdf7ee98b7513c3396a7b5e06f974f8625fdd590df3091dd0c3e9de"
INDEX = f"""Package: google-chrome-beta
Version: 157.0.8103.6-1
Filename: pool/main/g/google-chrome-beta/google-chrome-beta_157.0.8103.6-1_amd64.deb
Size: 146776016
SHA256: 07591ef850d1d07ef11fefe7615adb8461a7fe13234c51652452ffa4de8901ec

Package: google-chrome-stable
Version: 155.0.8059.84-1
Depends: ca-certificates, fonts-liberation,
 wget
Filename: pool/main/g/google-chrome-stable/google-chrome-stable_155.0.8059.84-1_amd64.deb
Size: 144080360
SHA256: {SHA}
Description: The web browser from Google
"""


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class Response:
    def __init__(self, body: bytes, url: str):
        self.body, self.url = body, url

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# Chrome

def test_google_index_gives_the_stable_package_and_bad_entries_are_refused():
    entry = chrome_linux.parse_index(INDEX)
    assert entry == {"version": "155.0.8059.84-1", "size": 144080360, "sha256": SHA,
                     "filename": "pool/main/g/google-chrome-stable/google-chrome-stable_155.0.8059.84-1_amd64.deb"}
    assert chrome_linux.parse_index(INDEX.replace("pool/main/g/google-chrome-stable", "../x")) is None
    assert chrome_linux.parse_index(INDEX.replace(SHA, "abc")) is None
    assert chrome_linux.parse_index("Package: other\n") is None


def test_chrome_is_downloaded_from_google_with_the_index_size_and_checksum(tmp_path, monkeypatch):
    from app.frames import provision
    seen = {}
    monkeypatch.setattr(provision, "download_verified", lambda url, dest, size, sha, opener, **kw:
                        seen.update(url=url, dest=dest, size=size, sha=sha) or None)
    deb = tmp_path / "downloads" / "chrome.deb"
    opener = lambda req, timeout: Response(INDEX.encode(), req.full_url)
    assert chrome_linux.fetch(deb, opener=opener) is None
    assert seen["url"] == chrome_linux.SERVER + "pool/main/g/google-chrome-stable/google-chrome-stable_155.0.8059.84-1_amd64.deb"
    assert seen["size"] == 144080360 and seen["sha"] == SHA and seen["dest"] == deb
    assert "could not be read" in chrome_linux.fetch(deb, opener=lambda req, timeout: (_ for _ in ()).throw(OSError("offline")))
    assert "no google-chrome-stable" in chrome_linux.fetch(deb, opener=lambda r, timeout: Response(b"", r.full_url))


def test_the_web_tests_offer_chrome_on_debian_x64_only(tmp_path):
    plan = plans.plan_for("speedometer", host=LINUX)
    assert plan["kind"] == "linux" and plan["fetch"] == "chrome" and plan["package"].endswith(".deb")
    assert plans.plan_for("jetstream", host=LINUX) == plan
    assert plans.plan_for("speedometer", host=Host("linux", "arm64", "debian")) is None
    for family in ("fedora", "arch"):        # their own Chromium package (owner, 2026-10-22: before the Fedora test)
        host = Host("linux", "x64", family)
        assert plans.plan_for("speedometer", host=host) == {"kind": "linux", "family": family, "package": "chromium"}
        assert plans.plan_for("glmark2", host=host)["package"] == "glmark2"
        assert plans.plan_for("vkmark", host=host)["package"] == "vkmark"
    assert plans.plan_for("speedometer", host=Host("linux", "x64", "suse")) is None
    assert plans.plan_for("speedometer", host=Host("windows", "x64")) is None
    called = []
    assert chrome_linux.prepare({"kind": "linux", "package": "vkmark"}, fetch_fn=lambda *a, **k: called.append(1)) is None
    assert chrome_linux.prepare(plan, fetch_fn=lambda deb, progress: f"got {deb.name}") == f"got {Path(plan['package']).name}"
    assert called == []


def test_a_web_test_without_a_browser_is_not_installed_rather_than_broken():
    from app.web.benchmarks import SPEEDOMETER, WebBenchmarkWorkload
    w = WebBenchmarkWorkload(SPEEDOMETER, finder=lambda: None)
    entry = catalog.workload_entries([w], {"os_family": "linux", "native_arch": "x64"})[0]
    assert entry.state == MISSING


# LM Studio

def test_libatomic_is_found_in_the_linker_cache():
    have = lambda *a, **k: SimpleNamespace(stdout="\tlibatomic.so.1 (libc6,x86-64) => /lib/x86_64-linux-gnu/libatomic.so.1\n")
    none = lambda *a, **k: SimpleNamespace(stdout="\tlibc.so.6 (libc6,x86-64) => /lib/libc.so.6\n")
    assert not lmstudio_linux.libatomic_missing(have) and lmstudio_linux.libatomic_missing(none)


def test_lmstudio_runs_its_own_install_script_without_changing_the_shell_profile(tmp_path):
    script = b"#!/bin/sh\nARTIFACT_DOWNLOAD_URL=\"llmster.lmstudio.ai/download\"\n"
    opener = lambda req, timeout: Response(script, lmstudio_linux.SCRIPT_URL)
    ran = []

    def run(command, **kw):
        ran.append(command)
        assert Path(command[1]).read_bytes() == script and kw["timeout"] == lmstudio_linux.TIMEOUT
        return subprocess.CompletedProcess(command, 0, "Installing llmster...\n", "")
    out = lmstudio_linux.install(opener=opener, run=run, data_dir=tmp_path, lms=lambda: Path("/home/o/.lmstudio/bin/lms"))
    assert out.ok and "/home/o/.lmstudio/bin/lms" in out.message.replace("\\", "/")
    assert ran[0][0] == "sh" and ran[0][2] == "--no-modify-path"
    assert not Path(ran[0][1]).exists()                     # the script is not kept
    bad = lmstudio_linux.install(opener=lambda req, timeout: Response(b"<html>", lmstudio_linux.SCRIPT_URL),
                                 run=run, data_dir=tmp_path, lms=lambda: None)
    assert not bad.ok and "not LM Studio's install script" in bad.message
    failed = lmstudio_linux.install(opener=opener, data_dir=tmp_path, lms=lambda: None, run=lambda c, **kw:
                                    subprocess.CompletedProcess(c, 1, "", "ERROR: Missing required dependency\n"))
    assert not failed.ok and "Missing required dependency" in failed.message


def test_lms_is_found_where_lmstudio_points_its_home(tmp_path):
    home = tmp_path / "home"
    other = tmp_path / "elsewhere"
    (other / "bin").mkdir(parents=True)
    (other / "bin" / "lms").write_bytes(b"")
    home.mkdir()
    assert lmstudio.lms_path({"HOME": str(home)}) is None
    (home / ".lmstudio-home-pointer").write_text(str(other) + "\n", encoding="utf-8")
    assert lmstudio.lms_path({"HOME": str(home)}) == other / "bin" / "lms"


class Window:
    def __init__(self, page):
        self.tests_page, self.scans = page, 0

    def start_scan(self):
        self.scans += 1


def test_the_models_offer_install_lmstudio_on_linux_and_it_installs_libatomic_first(qapp, tmp_path, monkeypatch):
    from app.ui import tests_page as tp
    from app.ui.install_flow import InstallFlow
    model = next(iter(__import__("app.ai.model_set", fromlist=["all_models"]).all_models()))
    page = tp.TestsPage(open_url=lambda url: None)
    page.host = LINUX
    monkeypatch.setattr(tp, "needs_lmstudio", lambda e, host=None, lms=None: True)
    e = TestEntry(id=model, name="model", group="ai", mode="automatic", state=MISSING, state_reason="",
                  compatible=True, compatibility_reason="", verified=False)
    page.set_entries([e])
    button = page.tree.itemWidget(page.items[model], 5)
    assert button.text() == "Install LM Studio"
    assert tp.lmstudio_one_click(LINUX) and not tp.lmstudio_one_click(Host("windows", "x64"))
    asked, ran = [], []
    flow = InstallFlow(None, ask=lambda parent, title, text: asked.append(text) or True,
                       linux_commands=lambda family, package: ([["sudo", "-A", "apt-get", "install", "-y", package]], None),
                       run_commands=lambda commands: ran.append(commands) or "done", start_thread=lambda fn: fn())
    flow.window = Window(page)
    assert flow.start_lmstudio(missing=lambda: True, install=lambda: winget.Outcome(True, "LM Studio installed."))
    assert "libatomic1" in asked[0] and ran == [[["sudo", "-A", "apt-get", "install", "-y", "libatomic1"]]]
    assert flow.window.scans == 1 and flow.state.text.startswith("LM Studio installed.")


def test_a_models_install_installs_lmstudio_first_then_the_model(qapp, monkeypatch):
    # Radxa board, 2026-10-21: the model's Install only said "lms was not found in ~/.lmstudio/bin"
    from app.ai import lmstudio_linux, model_set
    from app.ui import tests_page as tp
    from app.ui.install_flow import InstallFlow
    model = next(iter(model_set.all_models()))
    page = tp.TestsPage(open_url=lambda url: None)
    page.host = LINUX
    have = []
    monkeypatch.setattr(tp, "needs_lmstudio", lambda e, host=None, lms=None: not have)
    monkeypatch.setattr(lmstudio_linux, "libatomic_missing", lambda: False)
    monkeypatch.setattr(lmstudio_linux, "install", lambda: have.append(1) or winget.Outcome(True, "LM Studio installed."))
    got = []
    monkeypatch.setattr(model_set, "install", lambda tid, progress=None, stop=None: got.append(tid) or
                        winget.Outcome(True, "ok"))
    e = TestEntry(id=model, name="model", group="ai", mode="automatic", state=MISSING, state_reason="",
                  compatible=True, compatibility_reason="", verified=False)
    asked = []
    flow = InstallFlow(None, ask=lambda parent, title, text: asked.append(text) or True, start_thread=lambda fn: fn())
    flow.window = Window(page)
    assert flow.start(e)
    assert "Install LM Studio" in asked[0] and len(asked) == 2 and got == [model] and have
    assert flow.window.scans == 1 and flow.after_lmstudio is None
    have.clear()                                    # from the phone: nothing is installed without the PC
    warned = []
    monkeypatch.setattr(flow, "_warn", warned.append)
    assert not flow.start(e, confirmed=True) and "at the PC" in warned[0] and not have


def test_lms_server_start_is_lmstudios_own_command(tmp_path):
    seen = []
    ok = lambda c, **kw: seen.append(c) or subprocess.CompletedProcess(c, 0, b"", b"")
    assert lmstudio.start_server(tmp_path / "lms", run=ok) is None and seen[0][1:] == ["server", "start"]
    bad = lambda c, **kw: subprocess.CompletedProcess(c, 1, b"", b"Error: port 1234 in use\n")
    assert lmstudio.start_server(tmp_path / "lms", run=bad) == "Error: port 1234 in use"


def test_fedora_and_arch_on_arm_get_the_same_packages():
    # owner, 2026-10-22: Fedora and Arch, x86 and ARM; Arch Linux ARM says ID=archarm, ID_LIKE=arch
    from app.frames.linux_install import distro_family
    assert distro_family({"ID": "archarm", "ID_LIKE": "arch"}) == "arch"
    assert distro_family({"ID": "fedora"}) == "fedora"
    for family in ("fedora", "arch"):
        for test_id in ("sevenzip_benchmark", "fio", "glmark2", "vkmark", "jetstream"):
            on_arm = plans.plan_for(test_id, host=Host("linux", "arm64", family))
            assert on_arm is not None and on_arm == plans.plan_for(test_id, host=Host("linux", "x64", family))
    assert plans.plan_for("sevenzip_benchmark", host=Host("linux", "arm64", "fedora"))["package"] == "7zip"

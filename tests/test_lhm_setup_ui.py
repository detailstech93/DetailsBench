"""The "Set up LibreHardwareMonitor..." button with fakes: nothing is downloaded, written or started."""

import sys

import pytest
from PySide6.QtWidgets import QApplication

from app.telemetry.lhm_files import LhmAsset
from app.telemetry.lhm_provision import LhmFound
from app.ui.lhm_setup import LhmSetupButton


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def make(qapp, tmp_path, answers=(), **kw):
    log, asked, steps = [], [], []
    answers = list(answers)

    def confirm(title, text):
        asked.append((title, text))
        return answers.pop(0)

    defaults = dict(finder=lambda: None, running=lambda: False, arch="x64", platform_name="win32",
                    configurer=lambda exe: steps.append(("configure", exe)),
                    launcher=lambda exe: steps.append(("launch", exe)))
    defaults.update(kw)
    btn = LhmSetupButton(log.append, tmp_path, confirm=confirm, **defaults)
    btn._run = lambda fn, on_done: on_done(fn()) or True        # run the slow steps inline
    return btn, log, asked, steps


ASSET = LhmAsset(version="0.9.6", tag="v0.9.6", name="LibreHardwareMonitor.zip",
                 url="https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/"
                     "LibreHardwareMonitor.zip", size=6632626, sha256="ab" * 32)


def test_download_then_configure_then_start(qapp, tmp_path):
    exe = str(tmp_path / "LibreHardwareMonitor.exe")
    btn, log, asked, steps = make(qapp, tmp_path, answers=[True, True], planner=lambda: ASSET,
                                  downloader=lambda a: LhmFound(exe, "0.9.6", ["size and SHA-256 verified"]))
    btn.start()
    assert [t for t, _ in asked] == ["Download LibreHardwareMonitor?", "Start LibreHardwareMonitor?"]
    assert "6.3 MB" in asked[0][1] and ASSET.sha256 in asked[0][1] and "MPL-2.0" in asked[0][1]
    assert "random password" in asked[1][1] and "UAC" in asked[1][1]
    assert steps == [("configure", exe), ("launch", exe)]
    assert log[-1].startswith("LibreHardwareMonitor started") and "SHA-256 verified" in log[-1]


def test_declined_download_does_nothing(qapp, tmp_path):
    downloads = []
    btn, log, asked, steps = make(qapp, tmp_path, answers=[False], planner=lambda: ASSET,
                                  downloader=lambda a: downloads.append(a))
    btn.start()
    assert downloads == [] and steps == [] and log[-1] == "LibreHardwareMonitor download cancelled."


def test_existing_copy_is_not_downloaded_and_start_can_be_declined(qapp, tmp_path):
    exe = tmp_path / "LibreHardwareMonitor.exe"
    btn, log, asked, steps = make(qapp, tmp_path, answers=[False], finder=lambda: exe,
                                  planner=lambda: pytest.fail("no download needed"))
    btn.start()
    assert [t for t, _ in asked] == ["Start LibreHardwareMonitor?"] and steps == []
    assert log[-1].startswith("LibreHardwareMonitor ready but not started")


def test_already_running_asks_nothing(qapp, tmp_path):
    btn, log, asked, steps = make(qapp, tmp_path, running=lambda: True)
    btn.start()
    assert asked == [] and steps == [] and "already running" in log[-1]


def test_errors_are_reported_and_stop_the_flow(qapp, tmp_path):
    btn, log, asked, _ = make(qapp, tmp_path, planner=lambda: LhmAsset(error="offline"))
    btn.start()
    assert asked == [] and log[-1] == "LibreHardwareMonitor download not possible: offline"
    exe = str(tmp_path / "x.exe")
    launched = []
    btn, log, _, _ = make(qapp, tmp_path, answers=[True], finder=lambda: exe,
                          configurer=lambda e: "LibreHardwareMonitor is running; close it first",
                          launcher=lambda e: launched.append(e))
    btn.start()
    assert launched == [] and "close it first" in log[-1]
    btn, log, _, _ = make(qapp, tmp_path, answers=[True], finder=lambda: exe,
                          launcher=lambda e: "the UAC prompt was declined")
    btn.start()
    assert log[-1] == "LibreHardwareMonitor not started: the UAC prompt was declined"


def test_arm_note_and_hidden_off_windows(qapp, tmp_path):
    btn, log, asked, _ = make(qapp, tmp_path, answers=[False], planner=lambda: ASSET, arch="arm64")
    btn.start()
    assert "not verified on ARM" in asked[0][1]
    linux_btn, _, _, _ = make(qapp, tmp_path, platform_name="linux")
    assert linux_btn.isHidden()

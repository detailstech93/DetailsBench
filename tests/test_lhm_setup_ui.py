"""The LibreHardwareMonitor check at run start, with fakes: nothing is downloaded, written or started."""

import sys

import pytest
from PySide6.QtWidgets import QApplication

from app.telemetry.lhm_files import LhmAsset
from app.telemetry.lhm_provision import LhmFound
from app.ui.lhm_setup import LhmPreflight, wait_until_answering


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def make(qapp, tmp_path, answers=(), **kw):
    log, asked, steps, done = [], [], [], []
    answers = list(answers)

    def confirm(title, text):
        asked.append((title, text))
        return answers.pop(0)

    defaults = dict(finder=lambda: None, running=lambda: False, arch="x64", platform_name="win32",
                    planner=lambda: pytest.fail("nothing to download"),
                    configurer=lambda exe: steps.append(("configure", exe)),
                    launcher=lambda exe: steps.append(("launch", exe)),
                    waiter=lambda: steps.append(("wait",)))
    defaults.update(kw)
    pre = LhmPreflight(log.append, None, tmp_path, confirm=confirm, **defaults)
    pre._run = lambda fn, on_done: on_done(fn())        # run the slow steps inline
    pre.go = lambda: pre.ensure(lambda: done.append(1))
    return pre, log, asked, steps, done


ASSET = LhmAsset(version="0.9.6", tag="v0.9.6", name="LibreHardwareMonitor.zip",
                 url="https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/"
                     "LibreHardwareMonitor.zip", size=6632626, sha256="ab" * 32)


def test_already_running_asks_nothing(qapp, tmp_path):
    pre, log, asked, steps, done = make(qapp, tmp_path, running=lambda: True)
    assert pre.go() is True
    assert asked == [] and steps == [] and log == [] and done == [1] and not pre.busy()


def test_missing_is_downloaded_configured_and_started_after_one_question(qapp, tmp_path):
    exe = str(tmp_path / "LibreHardwareMonitor.exe")
    pre, log, asked, steps, done = make(qapp, tmp_path, answers=[True], planner=lambda: ASSET,
                                        downloader=lambda a: LhmFound(exe, "0.9.6", ["size and SHA-256 verified"]))
    pre.go()
    assert [t for t, _ in asked] == ["Download and start LibreHardwareMonitor?"]
    text = asked[0][1]
    assert "6.3 MB" in text and ASSET.sha256 in text and "MPL-2.0" in text and ASSET.url in text
    assert "random password" in text and "UAC" in text and "without CPU sensors" in text
    assert steps == [("configure", exe), ("launch", exe), ("wait",)]
    assert done == [1] and "includes CPU sensors" in log[-1] and "SHA-256 verified" in log[-1]


def test_existing_copy_is_only_started(qapp, tmp_path):
    exe = tmp_path / "LibreHardwareMonitor.exe"
    pre, log, asked, steps, done = make(qapp, tmp_path, answers=[True], finder=lambda: exe)
    pre.go()
    assert [t for t, _ in asked] == ["Start LibreHardwareMonitor?"] and "Download" not in asked[0][1]
    assert steps == [("configure", str(exe)), ("launch", str(exe)), ("wait",)] and done == [1]


def test_no_is_remembered_and_the_run_still_goes_ahead(qapp, tmp_path):
    downloads = []
    pre, log, asked, steps, done = make(qapp, tmp_path, answers=[False], planner=lambda: ASSET,
                                        downloader=lambda a: downloads.append(a))
    pre.go()
    assert downloads == [] and steps == [] and done == [1] and "declined" in log[-1]
    pre.go()                                              # next run: not asked again
    assert len(asked) == 1 and done == [1, 1]


@pytest.mark.parametrize("kw, expected", [
    (dict(planner=lambda: LhmAsset(error="offline")), "cannot be downloaded (offline)"),
    (dict(planner=lambda: ASSET, downloader=lambda a: LhmFound(error="SHA-256 mismatch")), "download failed"),
    (dict(finder=lambda: "x.exe", configurer=lambda e: "LibreHardwareMonitor is running; close it first"),
     "not started (LibreHardwareMonitor is running; close it first)"),
    (dict(finder=lambda: "x.exe", configurer=lambda e: None, launcher=lambda e: "the UAC prompt was declined"),
     "not started (the UAC prompt was declined)"),
    (dict(finder=lambda: "x.exe", configurer=lambda e: None, launcher=lambda e: None,
          waiter=lambda: "401 Unauthorized"), "gives no CPU sensors yet (401 Unauthorized)"),
    (dict(running=lambda: 1 / 0), "could not check"),
])
def test_every_failure_still_lets_the_run_go_ahead(qapp, tmp_path, kw, expected):
    pre, log, asked, _, done = make(qapp, tmp_path, answers=[True], **kw)
    pre.go()
    assert done == [1] and expected in log[-1] and not pre.busy()


def test_busy_while_asking_and_off_windows_nothing_happens(qapp, tmp_path):
    pre, _, _, _, done = make(qapp, tmp_path, finder=lambda: "x.exe")
    pre.confirm = lambda title, text: (pre.busy() and pre.ensure(lambda: done.append(2)) is False) and False
    pre.go()
    assert done == [1]                                    # the second ensure while asking was refused
    linux, log, asked, _, done = make(qapp, tmp_path, platform_name="linux", running=lambda: pytest.fail("no"))
    linux.go()
    assert done == [1] and asked == [] and log == []


def test_arm_note_in_the_download_question(qapp, tmp_path):
    pre, _, asked, _, _ = make(qapp, tmp_path, answers=[False], planner=lambda: ASSET, arch="arm64")
    pre.go()
    assert "not verified on ARM" in asked[0][1]


def test_wait_until_answering_gives_up_after_the_timeout(monkeypatch):
    from app.telemetry import sources
    calls = []
    monkeypatch.setattr(sources.LhmSource, "available", lambda self: (calls.append(1), (False, "not reachable"))[1])
    now = [0.0]
    sleep = lambda s: now.__setitem__(0, now[0] + s)
    assert wait_until_answering(3, sleep=sleep, clock=lambda: now[0]) == "not reachable" and len(calls) == 4
    monkeypatch.setattr(sources.LhmSource, "available", lambda self: (True, ""))
    assert wait_until_answering(3, sleep=sleep, clock=lambda: now[0]) is None


def test_the_run_stays_followed_while_its_next_window_is_open(qapp, tmp_path):
    # after LibreHardwareMonitor the run's next window (settings, confirm) opens inside the callback: a screen
    # that follows the run must not take it for ended (owner, Witcher 3, 2026-10-18)
    exe = tmp_path / "LibreHardwareMonitor.exe"
    pre, log, asked, steps, done = make(qapp, tmp_path, answers=[True], finder=lambda: exe)
    seen = []
    pre.ensure(lambda: seen.append((pre.busy(), pre.active())))
    assert seen == [(False, True)] and not pre.active()

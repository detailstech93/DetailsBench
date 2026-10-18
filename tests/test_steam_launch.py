"""MOCKED tests for app/steam/launch.py: fake opener, snapshots and clock.

Nothing is ever launched here.
"""

import os

import pytest

from app.steam.launch import build_launch_url, launch_and_confirm, new_processes_in_folder

GAME = os.path.join("Z:" + os.sep, "SteamLibrary", "steamapps", "common", "Cyberpunk 2077")
EXE = os.path.join(GAME, "bin", "x64", "Cyberpunk2077.exe")


class TestUrl:
    def test_valid_discovered_appid(self):
        assert build_launch_url("1091500", ["730", "1091500"]) == "steam://rungameid/1091500"
        assert build_launch_url(1091500, [1091500]) == "steam://rungameid/1091500"

    @pytest.mark.parametrize("bad", ["", "abc", "-1", "0", "730//+quit", "730 ", "1e3", None])
    def test_rejects_non_numeric_or_unknown(self, bad):
        with pytest.raises(ValueError):
            build_launch_url(bad, ["730"]) if bad != "730 " else build_launch_url("731", ["730"])

    def test_rejects_appid_not_in_local_manifests(self):
        with pytest.raises(ValueError, match="not found in a local Steam manifest"):
            build_launch_url("999999", ["730"])


class TestNewProcess:
    def test_new_exe_inside_folder(self):
        assert new_processes_in_folder([], [("Cyberpunk2077.exe", EXE)], GAME) == [os.path.normpath(EXE)]

    def test_case_differences_still_match(self):
        before = [("Cyberpunk2077.exe", EXE.upper())]
        assert new_processes_in_folder(before, [("Cyberpunk2077.exe", EXE)], GAME) == []

    def test_already_running_instance_is_not_new(self):
        snap = [("Cyberpunk2077.exe", EXE)]
        assert new_processes_in_folder(snap, snap, GAME) == []

    def test_process_outside_folder_or_sibling_prefix_ignored(self):
        sibling = os.path.join(GAME + " Mod", "x.exe")
        after = [("steam.exe", r"C:\Steam\steam.exe"), ("x.exe", sibling), ("y.exe", None)]
        assert new_processes_in_folder([], after, GAME) == []


def fake_clock(step=2.0):
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s
    return clock, sleep


class TestLaunchAndConfirm:
    def test_launched_when_game_process_appears(self):
        opened = []
        snaps = iter([[], [], [("Cyberpunk2077.exe", EXE)]])
        clock, sleep = fake_clock()
        out = launch_and_confirm("1091500", GAME, ["1091500"], snapshot=lambda: next(snaps),
                                 opener=opened.append, sleep=sleep, clock=clock)
        assert opened == ["steam://rungameid/1091500"]
        assert out["status"] == "launched"
        assert "Cyberpunk2077.exe" in out["message"]

    def test_unconfirmed_after_timeout_and_nothing_killed(self):
        clock, sleep = fake_clock()
        out = launch_and_confirm("1091500", GAME, ["1091500"], snapshot=lambda: [],
                                 opener=lambda url: None, timeout=10, sleep=sleep, clock=clock)
        assert out["status"] == "unconfirmed"
        assert "within 10 s" in out["message"]

    def test_invalid_appid_never_opens_anything(self):
        opened = []
        out = launch_and_confirm("123abc", GAME, ["1091500"], snapshot=lambda: [], opener=opened.append)
        assert out["status"] == "error" and opened == []

    def test_opener_failure_is_error(self):
        def boom(url):
            raise OSError("no handler for steam://")
        out = launch_and_confirm("1091500", GAME, ["1091500"], snapshot=lambda: [], opener=boom)
        assert out["status"] == "error" and "no handler" in out["message"]


def test_launch_url_with_adapter_arguments():
    assert build_launch_url("1091500", ["1091500"], ["--launcher-skip", "-skipStartScreen"]) ==         "steam://run/1091500//--launcher-skip -skipStartScreen/"
    assert build_launch_url("1091500", ["1091500"], []) == "steam://rungameid/1091500"
    for bad in ["--x y", "-a/b", "x", "--a=1", "--a\"", "", "--"]:
        with pytest.raises(ValueError):
            build_launch_url("1091500", ["1091500"], [bad])


def test_launch_and_confirm_passes_arguments():
    opened = []
    out = launch_and_confirm("1091500", "Z:/G", ["1091500"], snapshot=lambda: [], opener=opened.append,
                             timeout=0, args=["--launcher-skip"])
    assert opened == ["steam://run/1091500//--launcher-skip/"] and out["url"] == opened[0]


def test_known_exe_is_started_directly_with_args(tmp_path):
    from app.steam.launch import direct_exe, launch_and_confirm
    exe = tmp_path / "bin" / "x64" / "Cyberpunk2077.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert direct_exe(1091500, str(tmp_path), []) is None                 # no args: Steam as before
    assert direct_exe(1091500, str(tmp_path), ["--launcher-skip"]) == os.path.realpath(str(exe))
    assert direct_exe(391220, str(tmp_path), ["-x"]) is None               # no exe listed for this game
    started, opened = [], []
    snaps = iter([[], [(1, str(exe))]])
    res = launch_and_confirm(1091500, str(tmp_path), ["1091500"], snapshot=lambda: next(snaps),
                             opener=opened.append, starter=started.append, sleep=lambda s: None,
                             args=["--launcher-skip", "-skipStartScreen"])
    assert res["status"] == "launched" and not opened
    assert started == [[os.path.realpath(str(exe)), "--launcher-skip", "-skipStartScreen"]]


def test_missing_exe_falls_back_to_steam(tmp_path):
    from app.steam.launch import launch_and_confirm
    opened = []
    res = launch_and_confirm(1091500, str(tmp_path), ["1091500"], snapshot=lambda: [], opener=opened.append,
                             starter=lambda c: (_ for _ in ()).throw(AssertionError("no exe")),
                             sleep=lambda s: None, timeout=0, args=["--launcher-skip"])
    assert opened == ["steam://run/1091500//--launcher-skip/"] and res["status"] == "unconfirmed"

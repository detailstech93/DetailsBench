"""MOCKED tests for app/steam/library.py: fake Steam folders on tmp_path into the REAL code.

Manifest/libraryfolders samples are shaped like the real files on the dev machine.
"""

import os

from app.steam.library import library_paths, parse_manifest, scan_library

MANIFEST = '''"AppState"
{
	"appid"		"%(appid)s"
	"universe"		"1"
	"name"		"%(name)s"
	"StateFlags"		"%(flags)s"
	"installdir"		"%(installdir)s"
	"SizeOnDisk"		"71644882396"
	"buildid"		"24098430"
	"LastOwner"		"76561198000000000"
}
'''


def manifest(appid=730, name="Counter-Strike 2", flags=4, installdir="Counter-Strike Global Offensive"):
    return MANIFEST % {"appid": appid, "name": name, "flags": flags, "installdir": installdir}


def libraryfolders(*paths):
    body = "".join('\t"%d"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n' % (i, p.replace("\\", "\\\\"))
                   for i, p in enumerate(paths))
    return '"libraryfolders"\n{\n' + body + '}\n'


def make_library(root):
    os.makedirs(os.path.join(root, "steamapps"), exist_ok=True)
    return str(root)


class TestLibraryPaths:
    def test_both_files_are_read_and_merged(self, tmp_path):
        steam = make_library(tmp_path / "Steam")
        lib1 = make_library(tmp_path / "Lib1")
        lib2 = make_library(tmp_path / "Lib2")
        os.makedirs(os.path.join(steam, "config"))
        open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), "w", encoding="utf-8").write(
            libraryfolders(steam, lib1))
        open(os.path.join(steam, "config", "libraryfolders.vdf"), "w", encoding="utf-8").write(
            libraryfolders(steam, lib2))
        libs, notes = library_paths(steam)
        assert libs == [os.path.normpath(p) for p in (steam, lib1, lib2)]
        assert notes == []

    def test_stale_library_is_reported_not_returned(self, tmp_path):
        steam = make_library(tmp_path / "Steam")
        gone = str(tmp_path / "UnpluggedDrive")
        open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), "w", encoding="utf-8").write(
            libraryfolders(steam, gone))
        libs, notes = library_paths(steam)
        assert libs == [os.path.normpath(steam)]
        assert any("not reachable" in n and "UnpluggedDrive" in n for n in notes)
        assert any("config" in n and "not found" in n for n in notes)

    def test_malformed_libraryfolders_is_a_note(self, tmp_path):
        steam = make_library(tmp_path / "Steam")
        open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), "w", encoding="utf-8").write('"libraryfolders" {')
        libs, notes = library_paths(steam)
        assert libs == [os.path.normpath(steam)]
        assert any("unreadable" in n for n in notes)


class TestManifest:
    def test_keeps_only_safe_fields(self):
        m = parse_manifest(manifest(), expected_appid=730)
        assert m == {"appid": 730, "name": "Counter-Strike 2", "installdir": "Counter-Strike Global Offensive",
                     "state_flags": 4, "fully_installed": True, "buildid": "24098430",
                     "size_on_disk": 71644882396}
        assert "LastOwner" not in str(m) and "76561198" not in str(m)

    def test_partial_install_flag(self):
        assert parse_manifest(manifest(flags=6))["fully_installed"] is True     # 4 bit set
        assert parse_manifest(manifest(flags=1026))["fully_installed"] is False

    def test_rejects_bad_manifests(self):
        assert parse_manifest(manifest(installdir="")) is None
        assert parse_manifest(manifest(appid="abc")) is None
        assert parse_manifest(manifest(), expected_appid=731) is None   # file name / content mismatch
        assert parse_manifest('"AppState" {') is None
        assert parse_manifest("") is None


class TestScanLibrary:
    def test_games_tmp_and_malformed(self, tmp_path):
        lib = make_library(tmp_path / "SteamLibrary")
        apps = os.path.join(lib, "steamapps")
        open(os.path.join(apps, "appmanifest_730.acf"), "w", encoding="utf-8").write(manifest())
        open(os.path.join(apps, "appmanifest_1091500.acf"), "w", encoding="utf-8").write(
            manifest(1091500, "Cyberpunk 2077", 4, "Cyberpunk 2077"))
        # Real-world leftovers: editor temp file and a broken manifest
        open(os.path.join(apps, "appmanifest_391220.acf~RF53e037d1.TMP"), "w", encoding="utf-8").write(manifest(391220))
        open(os.path.join(apps, "appmanifest_999.acf"), "w", encoding="utf-8").write("garbage {")
        games, notes = scan_library(lib, "windows")
        assert [g["app_id"] for g in games] == ["1091500", "730"]
        cp = games[0]
        assert cp["install_path"] == os.path.join(apps, "common", "Cyberpunk 2077")
        assert cp["builtin_benchmark"] == "unverified"      # from data/steam_benchmarks.py
        assert cp["runtime"] == "native" and cp["store"] == "steam"
        assert games[1]["builtin_benchmark"] is None
        assert notes == ["malformed manifest skipped: appmanifest_999.acf"]

    def test_linux_runtime_is_unavailable(self, tmp_path):
        lib = make_library(tmp_path / "steam")
        open(os.path.join(lib, "steamapps", "appmanifest_730.acf"), "w", encoding="utf-8").write(manifest())
        games, _ = scan_library(lib, "linux")
        assert games[0]["runtime"] == "unavailable"   # native vs Proton not read reliably yet

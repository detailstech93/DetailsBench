"""MOCKED tests: a fake registry fed into the REAL Windows discovery code."""

import pytest

from app.discovery.windows import (
    UNINSTALL, discover_windows, find_steam_client, read_uninstall_entries,
)


@pytest.fixture(autouse=True)
def isolated_program_data(tmp_path, monkeypatch):
    """discover_windows reads Epic manifests under ProgramData: point it at an empty temp dir."""
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))


class FakeKey:
    def __init__(self, values=None, subkeys=None):
        self.values = values or {}
        self.subkeys = subkeys or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeReg:
    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = 1, 2
    KEY_READ, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 0x20019, 0x100, 0x200

    def __init__(self, tree):
        # tree: {(hive, view, path): FakeKey}; view 0 = default
        self.tree = tree

    def OpenKey(self, parent, sub, reserved=0, access=0):
        if isinstance(parent, FakeKey):
            if sub not in parent.subkeys:
                raise OSError("missing")
            return parent.subkeys[sub]
        view = access & (self.KEY_WOW64_64KEY | self.KEY_WOW64_32KEY)
        key = self.tree.get((parent, view, sub)) or self.tree.get((parent, 0, sub))
        if key is None:
            raise OSError("missing")
        return key

    def QueryInfoKey(self, key):
        return (len(key.subkeys), len(key.values), 0)

    def EnumKey(self, key, i):
        return list(key.subkeys)[i]

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise OSError("no value")
        return key.values[name], 1


def uninstall_tree():
    hklm64 = FakeKey(subkeys={
        "Steam": FakeKey({"DisplayName": "Steam", "DisplayVersion": "2.10.91.91"}),
        "Private": FakeKey({"DisplayName": "Secret Diary"}),
        "NoName": FakeKey({"DisplayVersion": "1.0"}),
        "Broken": FakeKey({"DisplayName": 12345}),   # non-string value
    })
    hkcu = FakeKey(subkeys={"GB7": FakeKey({"DisplayName": "Geekbench 7", "InstallLocation": r"C:\GB7"})})
    return {
        (FakeReg.HKEY_LOCAL_MACHINE, FakeReg.KEY_WOW64_64KEY, UNINSTALL): hklm64,
        (FakeReg.HKEY_CURRENT_USER, 0, UNINSTALL): hkcu,
        # 32-bit view missing entirely -> skipped
    }


def test_read_uninstall_entries_all_hives_and_skips_bad_entries():
    entries = list(read_uninstall_entries(FakeReg(uninstall_tree())))
    assert [x["name"] for x in entries] == ["Steam", "Secret Diary", "Geekbench 7"]
    assert entries[2]["source"] == "registry HKCU"


def test_discover_keeps_only_known_software():
    reg = FakeReg(uninstall_tree())
    result = discover_windows(reg)
    assert [s["id"] for s in result.software] == ["steam", "geekbench"]
    assert "Secret Diary" not in str(result.to_dict())
    assert result.diagnostics == []
    assert set(result.step_seconds) == {"installed_software", "steam_client", "epic_games", "gog_games"}


class TestSteamClient:
    def test_detected_when_exe_exists(self):
        reg = FakeReg({(FakeReg.HKEY_LOCAL_MACHINE, 0, r"SOFTWARE\WOW6432Node\Valve\Steam"):
                       FakeKey({"InstallPath": r"C:\Program Files (x86)\Steam"})})
        out = find_steam_client(reg, isfile=lambda p: p.endswith("steam.exe"))
        assert out["status"] == "detected"
        assert out["path"].endswith("Steam")

    def test_stale_registry_path_is_not_detected(self):
        reg = FakeReg({(FakeReg.HKEY_CURRENT_USER, 0, r"SOFTWARE\Valve\Steam"): FakeKey({"SteamPath": "D:/old"})})
        out = find_steam_client(reg, isfile=lambda p: False)
        assert out["status"] == "not detected"
        assert "steam.exe missing" in out["source"]

    def test_absent(self):
        assert find_steam_client(FakeReg({}))["status"] == "not detected"


def test_failing_step_is_isolated():
    class Exploding(FakeReg):
        def OpenKey(self, *a, **kw):
            raise RuntimeError("registry exploded")
    result = discover_windows(Exploding({}))
    assert len(result.diagnostics) == 3   # epic reads files, not the registry
    assert result.software == []


class TestEpicGog:
    """MOCKED: fake manifest folder / fake GOG registry into the REAL readers."""

    def test_epic_manifests_read_and_bad_files_skipped(self, tmp_path):
        from app.discovery.windows import EPIC_MANIFESTS, read_epic_games
        folder = tmp_path / EPIC_MANIFESTS
        folder.mkdir(parents=True)
        (folder / "a.item").write_text('{"DisplayName": "Game A", "AppName": "a", '
                                       '"InstallLocation": "D:/A"}', encoding="utf-8")
        (folder / "b.item").write_text("{broken", encoding="utf-8")
        (folder / "notes.txt").write_text("ignored", encoding="utf-8")
        status, games = read_epic_games(str(tmp_path))
        assert status["status"] == "detected"
        assert [g["name"] for g in games] == ["Game A"]

    def test_epic_absent(self, tmp_path):
        from app.discovery.windows import read_epic_games
        assert read_epic_games(str(tmp_path))[0]["status"] == "not detected"
        assert read_epic_games(None)[0]["status"] == "not detected"

    def test_gog_registry(self):
        from app.discovery.windows import GOG_GAMES, read_gog_games
        games_key = FakeKey(subkeys={
            "1207658924": FakeKey({"gameName": "Witcher", "path": r"C:\GOG\Witcher", "gameID": "1207658924"}),
            "999": FakeKey({"gameName": "No Path"}),
        })
        reg = FakeReg({(FakeReg.HKEY_LOCAL_MACHINE, 0, GOG_GAMES): games_key})
        status, games = read_gog_games(reg)
        assert status["status"] == "detected"
        assert [(g["store"], g["app_id"], g["name"]) for g in games] == [("gog", "1207658924", "Witcher")]

    def test_gog_absent(self):
        from app.discovery.windows import read_gog_games
        assert read_gog_games(FakeReg({}))[0]["status"] == "not detected"

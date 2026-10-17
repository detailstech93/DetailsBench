"""MOCKED tests for Linux discovery: fake command output / which / home folder.

Unverified on real Linux hardware; these only prove the logic.
"""

from app.discovery.linux import discover_linux, match_linux
from app.discovery.runner import discover_installed

DPKG = ("fio\t3.36-1\tinstall ok installed\n"
        "libsecret-diary\t1.0\tinstall ok installed\n"
        "steam-installer\t1:1.0.0.79\tinstall ok installed\n")
SNAP = "Name     Version  Rev  Tracking  Publisher  Notes\nfirefox  131.0.2  5091 latest    mozilla**  -\n"


def fake_env(tools):
    outputs = {"dpkg-query": DPKG, "snap": SNAP, "rpm": None, "flatpak": ""}

    def which(name):
        return f"/usr/bin/{name}" if name in tools else None

    def run(cmd):
        return outputs.get(cmd[0])
    return run, which


def test_only_known_packages_and_executables_kept(tmp_path):
    run, which = fake_env({"dpkg-query", "snap", "fio"})
    result = discover_linux(run, which, home=str(tmp_path))
    names = [(s["id"], s["name"], s["source"].split(" ")[0]) for s in result.software]
    assert ("fio", "fio", "dpkg-query") in names
    assert ("steam", "steam-installer", "dpkg-query") in names
    assert ("firefox", "firefox", "snap") in names
    assert ("fio", "fio", "PATH") in names
    assert "libsecret-diary" not in str(result.to_dict())
    assert all("unverified on real hardware" in s["source"] for s in result.software)


def test_missing_package_managers_are_skipped(tmp_path):
    run, which = fake_env(set())
    result = discover_linux(run, which, home=str(tmp_path))
    assert result.software == []
    assert result.diagnostics == []
    assert result.stores["steam"]["status"] == "not detected"


def test_steam_folder_detected(tmp_path):
    (tmp_path / ".local" / "share" / "Steam").mkdir(parents=True)
    run, which = fake_env(set())
    assert discover_linux(run, which, home=str(tmp_path)).stores["steam"]["status"] == "detected"


def test_failing_command_is_isolated(tmp_path):
    def run(cmd):
        raise RuntimeError("dpkg exploded") if cmd[0] == "dpkg-query" else None
    _, which = fake_env({"dpkg-query"})
    result = discover_linux(run, which, home=str(tmp_path))
    assert result.diagnostics == ["step dpkg failed: dpkg exploded"]
    assert "steam" in result.stores


def test_match_linux_dedupes():
    entries = [{"name": "fio", "version": "1", "location": None, "source": "rpm"}] * 2
    assert len(match_linux(entries)) == 1


def test_runner_reports_unsupported_os():
    result = discover_installed("Darwin")
    assert result.software == [] and "not implemented for darwin" in result.diagnostics[0]

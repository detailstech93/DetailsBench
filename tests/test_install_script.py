"""install.sh: the one-command Linux install (owner, 2026-10-20). Read only: nothing is run."""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "install.sh"


def test_the_installer_is_whole_and_linux_ready():
    raw = SCRIPT.read_bytes()
    text = raw.decode("utf-8")
    assert raw.startswith(b"#!/usr/bin/env bash\n")
    # a download cut halfway runs nothing: everything is in main(), called on the last line
    assert text.rstrip().splitlines()[-1] == 'main "$@"'
    for manager in ("apt-get", "dnf", "pacman", "zypper"):
        assert manager in text
    assert "requirements.txt" in text and ".venv" in text and "--ff-only" in text
    assert "libxcb-cursor0" in text           # Qt 6.5+ will not open a window without it


def test_shell_scripts_keep_lf_endings():
    attributes = (SCRIPT.parent / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in attributes


def test_the_project_has_its_license():
    assert "GNU GENERAL PUBLIC LICENSE" in (SCRIPT.parent / "LICENSE").read_text(encoding="utf-8")[:200]

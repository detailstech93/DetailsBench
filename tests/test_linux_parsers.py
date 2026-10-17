"""Tests for app/environment/linux_parsers.py (pure functions, fake inputs)."""

from app.environment.linux_parsers import (
    parse_os_release,
    is_wsl,
    display_server,
)


def test_parse_os_release_ubuntu_like_sample():
    sample = (
        'NAME="Ubuntu"\n'
        'VERSION_ID="24.04"\n'
        'ID=ubuntu\n'
        "PRETTY_NAME='Ubuntu 24.04 LTS'\n"
        '# comment\n'
        '\n'
        'BAD LINE\n'
        '1BAD=x\n'
        'ESC="a \\"quoted\\" word"\n'
    )

    result = parse_os_release(sample)

    expected = {
        "NAME": "Ubuntu",
        "VERSION_ID": "24.04",
        "ID": "ubuntu",
        "PRETTY_NAME": "Ubuntu 24.04 LTS",
        "ESC": 'a "quoted" word',
    }

    assert result == expected


def test_parse_os_release_empty_string():
    assert parse_os_release("") == {}


def test_is_wsl_with_wsl_distro_name():
    assert is_wsl("", {"WSL_DISTRO_NAME": "Ubuntu"}) is True


def test_is_wsl_with_microsoft_proc_version():
    proc_version = "Linux version 5.15.153.1-microsoft-standard-WSL2"
    assert is_wsl(proc_version, {}) is True


def test_is_wsl_plain_generic_proc_version():
    proc_version = "Linux version 6.8.0-45-generic"
    assert is_wsl(proc_version, {}) is False


def test_is_wsl_none_proc_version():
    assert is_wsl(None, {}) is False


def test_display_server_wayland_case_insensitive():
    assert display_server({"XDG_SESSION_TYPE": "Wayland"}) == "wayland"


def test_display_server_x11():
    assert display_server({"XDG_SESSION_TYPE": "x11"}) == "x11"


def test_display_server_tty_with_wayland_display():
    env = {
        "XDG_SESSION_TYPE": "tty",
        "WAYLAND_DISPLAY": "wayland-0",
    }
    assert display_server(env) == "wayland"


def test_display_server_only_display():
    assert display_server({"DISPLAY": ":0"}) == "x11"


def test_display_server_empty_env():
    assert display_server({}) is None


def test_parse_os_release_backslash_escape():
    # file text: K="a\\b \$HOME"  ->  value: a\b $HOME
    assert parse_os_release(r'K="a\\b \$HOME"') == {"K": "a\\b $HOME"}


def test_real_os_environ_mapping_is_accepted():
    """os.environ is a Mapping, not a dict; it must still be read."""
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WSL_DISTRO_NAME": "Ubuntu"}):
        assert display_server(os.environ) == "wayland"
        assert is_wsl("", os.environ) is True

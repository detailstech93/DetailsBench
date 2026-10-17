"""Parsers for Epic / GOG game entries (drafted by the local agent, reviewed).

Pure functions; callers read the files / registry and pass the data in.
"""

import json
from collections.abc import Mapping
from typing import Optional


def _clean_string(value: object) -> Optional[str]:
    """Return a stripped non-empty string, or None if the value is not usable."""
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    return stripped if stripped else None


def parse_epic_manifest(text: str) -> Optional[dict]:
    """Parse one Epic Games Launcher manifest file.

    The field names used here are not verified on a real machine.
    """
    if not isinstance(text, str):
        return None

    try:
        data = json.loads(text)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    if data.get("bIsIncompleteInstall") is True:
        return None

    name = _clean_string(data.get("DisplayName"))
    app_id = _clean_string(data.get("AppName"))
    install_path = _clean_string(data.get("InstallLocation"))

    if name is None or app_id is None or install_path is None:
        return None

    version = _clean_string(data.get("AppVersionString"))

    return {
        "store": "epic",
        "app_id": app_id,
        "name": name,
        "install_path": install_path,
        "version": version,
    }


def parse_gog_entry(key_name: str, values: Mapping) -> Optional[dict]:
    """Parse one GOG registry entry.

    The field names used here are not verified on a real machine.
    """
    if not isinstance(key_name, str):
        return None

    if not isinstance(values, Mapping):
        return None

    try:
        name = _clean_string(values.get("gameName"))
        install_path = _clean_string(values.get("path"))
        app_id_raw = values.get("gameID")
        version_raw = values.get("ver")
    except Exception:
        return None

    if name is None or install_path is None:
        return None

    app_id = _clean_string(app_id_raw)
    if app_id is None:
        app_id = key_name.strip()

    if not app_id:
        return None

    version = _clean_string(version_raw)

    return {
        "store": "gog",
        "app_id": app_id,
        "name": name,
        "install_path": install_path,
        "version": version,
    }

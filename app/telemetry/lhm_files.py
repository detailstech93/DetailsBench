"""Pure helpers for setting up LibreHardwareMonitor (used by app/telemetry/lhm_provision.py).

- select_lhm_asset: the .NET Framework zip of the newest stable GitHub release that has a published SHA-256.
- parse_config / render_config / with_web_server / web_auth / web_port: its settings file
  (LibreHardwareMonitor.config, <configuration><appSettings><add key value/>, format of its
  PersistentSettings.cs in v0.9.6; booleans are "true"/"false"). Other keys are kept as they are.

Drafted by the local agent, reviewed.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
import xml.etree.ElementTree as ET

PREFERRED_ASSETS = ("LibreHardwareMonitor.zip", "LibreHardwareMonitor-net472.zip")
URL_PREFIX = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/"
_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+){1,3})$")
_DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")

WEB_USER = "pcbm"
WEB_SETTINGS_KEYS = (
    "runWebServerMenuItem",
    "listenerPort",
    "authenticationEnabled",
    "authenticationUserName",
    "authenticationPassword",
    "startMinMenuItem",
    "minTrayMenuItem",
    "gpuMenuItem",
)


@dataclass
class LhmAsset:
    version: Optional[str] = None
    tag: Optional[str] = None
    name: Optional[str] = None
    url: Optional[str] = None
    size: Optional[int] = None
    sha256: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ConfigParse:
    settings: dict = field(default_factory=dict)
    error: Optional[str] = None


def select_lhm_asset(releases, allow_prerelease: bool = False) -> LhmAsset:
    if not isinstance(releases, list):
        return LhmAsset(error="unexpected release list")

    for item in releases:
        if not isinstance(item, dict):
            continue
        if item.get("draft"):
            continue
        if not allow_prerelease and item.get("prerelease"):
            continue

        tag = item.get("tag_name")
        if not isinstance(tag, str):
            continue
        tag_match = _TAG_RE.match(tag)
        if tag_match is None:
            continue
        version = tag_match.group(1)

        assets = item.get("assets")
        if not isinstance(assets, list):
            continue

        for name in PREFERRED_ASSETS:
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                if asset.get("name") != name:
                    continue

                url = asset.get("browser_download_url")
                if not isinstance(url, str) or not url.startswith(URL_PREFIX):
                    continue

                size = asset.get("size")
                if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                    continue

                digest = asset.get("digest")
                if not isinstance(digest, str):
                    continue
                digest_match = _DIGEST_RE.match(digest)
                if digest_match is None:
                    continue

                return LhmAsset(
                    version=version,
                    tag=tag,
                    name=name,
                    url=url,
                    size=size,
                    sha256=digest_match.group(1).lower(),
                    error=None,
                )

    return LhmAsset(
        error="no LibreHardwareMonitor release with a .NET Framework zip and a published SHA-256"
    )


def parse_config(text) -> ConfigParse:
    if not isinstance(text, str):
        return ConfigParse(error="settings file is not text")

    if text.strip() == "":
        return ConfigParse()

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ConfigParse(error="settings file is not valid XML")

    if root.tag != "configuration":
        return ConfigParse(error="settings file has no <configuration> root")

    settings = {}
    for app_settings in root:
        if app_settings.tag != "appSettings":
            continue
        for add in app_settings:
            if add.tag != "add":
                continue
            key = add.get("key")
            value = add.get("value")
            if key is not None and value is not None:
                settings[key] = value

    return ConfigParse(settings=settings)


def _escape_attr(value) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\t", "&#9;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
    )


def render_config(settings: dict) -> str:
    if not isinstance(settings, dict):
        settings = {}

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<configuration>",
        "  <appSettings>",
    ]

    for key, value in settings.items():
        escaped_key = _escape_attr(key)
        escaped_value = _escape_attr(value)
        lines.append('    <add key="' + escaped_key + '" value="' + escaped_value + '" />')

    lines.append("  </appSettings>")
    lines.append("</configuration>")
    return "\n".join(lines) + "\n"


def with_web_server(settings: dict, password: str, port: int = 8085) -> dict:
    if not isinstance(password, str) or password == "":
        raise ValueError("password must be a non-empty string")

    if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
        raise ValueError("port must be an int in 1..65535")

    base = dict(settings) if isinstance(settings, dict) else {}

    updates = (
        ("runWebServerMenuItem", "true"),
        ("listenerPort", str(port)),
        ("authenticationEnabled", "true"),
        ("authenticationUserName", WEB_USER),
        ("authenticationPassword", password),
        ("startMinMenuItem", "true"),
        ("minTrayMenuItem", "true"),
        # GPU readings come from nvidia-smi; LibreHardwareMonitor polling the GPU driver every second took the
        # GPU stress from 100 % down to about 35 % (measured on an RTX 5090 Laptop with the CPU stress running)
        ("gpuMenuItem", "false"),
    )

    for key, value in updates:
        base[key] = value

    return base


def web_auth(settings) -> Optional[tuple]:
    if not isinstance(settings, dict):
        return None

    if settings.get("authenticationEnabled") == "true":
        return (
            settings.get("authenticationUserName") or "",
            settings.get("authenticationPassword") or "",
        )

    return None


def web_port(settings) -> int:
    if not isinstance(settings, dict):
        return 8085

    value = settings.get("listenerPort")
    if isinstance(value, str) and value.isdigit() and value.isascii():
        port = int(value)
        if 1 <= port <= 65535:
            return port

    return 8085

"""Pick the official PresentMon command-line file to download for this computer (Phase 6).

Input is the GitHub API release list of GameTechDev/PresentMon, newest first. Only a file
named PresentMon-<version>-<x64|x86|arm64>.exe with a published SHA-256, a positive size and
a GitHub download URL of that repository is ever chosen. ARM64 prefers a native arm64 file
and otherwise takes x64 (Windows x64 emulation), reported through `emulated`.

Drafted by the local agent, reviewed.
"""

import re
from dataclasses import dataclass
from typing import Optional

ASSET_RE = re.compile(r"^PresentMon-(?P<version>\d+(?:\.\d+){1,3})-(?P<arch>x64|x86|arm64)\.exe$")
ARCH_PREFERENCES = {"x64": ("x64",), "x86": ("x86",), "arm64": ("arm64", "x64")}
_DIGEST_RE = re.compile(r"[sS][hH][aA]256:[0-9a-fA-F]{64}")
_URL_PREFIX = "https://github.com/GameTechDev/PresentMon/releases/download/"


@dataclass
class AssetChoice:
    version: Optional[str] = None
    tag: Optional[str] = None
    name: Optional[str] = None
    url: Optional[str] = None
    size: Optional[int] = None
    sha256: Optional[str] = None
    asset_arch: Optional[str] = None
    emulated: bool = False
    error: Optional[str] = None


def parse_digest(digest) -> Optional[str]:
    if not isinstance(digest, str):
        return None
    if _DIGEST_RE.fullmatch(digest) is None:
        return None
    return digest[7:].lower()


def select_asset(releases, native_arch: str, allow_prerelease: bool = False) -> AssetChoice:
    if not isinstance(native_arch, str):
        return AssetChoice(error=f"no PresentMon build for architecture {native_arch}")

    preferences = ARCH_PREFERENCES.get(native_arch)
    if preferences is None:
        return AssetChoice(error=f"no PresentMon build for architecture {native_arch}")

    if not isinstance(releases, list):
        return AssetChoice(error="unexpected release data")

    first_bad_name = None

    for release in releases:
        if not isinstance(release, dict):
            continue

        if release.get("draft"):
            continue

        if release.get("prerelease") and not allow_prerelease:
            continue

        assets = release.get("assets")
        if not isinstance(assets, list):
            continue

        tag = release.get("tag_name")
        if not isinstance(tag, str):
            tag = None

        for preferred_arch in preferences:
            for asset in assets:
                if not isinstance(asset, dict):
                    continue

                name = asset.get("name")
                if not isinstance(name, str):
                    continue

                match = ASSET_RE.fullmatch(name)
                if match is None or match.group("arch") != preferred_arch:
                    continue

                sha256 = parse_digest(asset.get("digest"))
                if sha256 is None:
                    if first_bad_name is None:
                        first_bad_name = name
                    continue

                size = asset.get("size")
                if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                    continue

                url = asset.get("browser_download_url")
                if not isinstance(url, str) or not url.startswith(_URL_PREFIX):
                    continue

                emulated = native_arch == "arm64" and preferred_arch == "x64"

                return AssetChoice(
                    version=match.group("version"),
                    tag=tag,
                    name=name,
                    url=url,
                    size=size,
                    sha256=sha256,
                    asset_arch=preferred_arch,
                    emulated=emulated,
                    error=None,
                )

    if first_bad_name is not None:
        return AssetChoice(error="no published checksum for " + first_bad_name)

    return AssetChoice(error=f"no PresentMon release for {native_arch}")

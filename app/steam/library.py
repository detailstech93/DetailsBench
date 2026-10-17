"""Steam libraries and installed games (read-only).

Source of truth is appmanifest_<appid>.acf inside each library's steamapps
folder; libraryfolders.vdf (in steamapps/ or config/, sources disagree, so
both are read) is only a hint and can be stale. Libraries may live on slow
network shares, so this runs in the background worker and every library is
an isolated step.

Privacy: only the two libraryfolders.vdf files and appmanifest files are
read, never loginusers.vdf, ssfn* or config.vdf; from manifests only appid,
name, installdir, StateFlags, buildid and SizeOnDisk are kept (never
LastOwner, the account's SteamID).
"""

import os
import re
from typing import Callable, List, Optional, Tuple

from app.steam.vdf import parse_vdf
from data.steam_benchmarks import STEAM_BENCHMARK_APPS_BY_ID, STEAM_TOOL_NAME_PATTERNS

_TOOL_RE = re.compile("|".join(STEAM_TOOL_NAME_PATTERNS), re.IGNORECASE)

MANIFEST_RE = re.compile(r"^appmanifest_(\d+)\.acf$", re.IGNORECASE)   # *.tmp / ~RF*.TMP excluded
LIBRARY_FILES = (os.path.join("steamapps", "libraryfolders.vdf"),
                 os.path.join("config", "libraryfolders.vdf"))
STATE_FULLY_INSTALLED = 4


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def library_paths(steam_root: str, read: Callable[[str], Optional[str]] = read_text,
                  isdir: Callable[[str], bool] = os.path.isdir) -> Tuple[List[str], List[str]]:
    """Return (existing library folders, notes). The Steam root is always a library."""
    candidates, notes = [steam_root], []
    for rel in LIBRARY_FILES:
        text = read(os.path.join(steam_root, rel))
        if text is None:
            notes.append(f"{rel}: not found")
            continue
        data = parse_vdf(text)
        folders = (data or {}).get("libraryfolders")
        if not isinstance(folders, dict):
            notes.append(f"{rel}: unreadable or unexpected format")
            continue
        for entry in folders.values():
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                candidates.append(entry["path"])
    libraries, seen = [], set()
    for path in candidates:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        if isdir(os.path.join(path, "steamapps")):
            libraries.append(os.path.normpath(path))
        else:
            notes.append(f"library listed but not reachable: {path}")
    return libraries, notes


def parse_manifest(text: str, expected_appid: Optional[int] = None) -> Optional[dict]:
    """Keep only non-identifying fields from one appmanifest; None if malformed."""
    state = (parse_vdf(text) or {}).get("AppState")
    if not isinstance(state, dict):
        return None
    appid, name, installdir = state.get("appid"), state.get("name"), state.get("installdir")
    if not (isinstance(appid, str) and appid.isdigit() and isinstance(name, str) and name.strip()
            and isinstance(installdir, str) and installdir.strip()):
        return None
    if expected_appid is not None and int(appid) != expected_appid:
        return None
    flags = state.get("StateFlags")
    flags = int(flags) if isinstance(flags, str) and flags.isdigit() else None
    size = state.get("SizeOnDisk")
    return {
        "appid": int(appid), "name": name.strip(), "installdir": installdir.strip(),
        "state_flags": flags,
        "fully_installed": None if flags is None else bool(flags & STATE_FULLY_INSTALLED),
        "buildid": state.get("buildid") if isinstance(state.get("buildid"), str) else None,
        "size_on_disk": int(size) if isinstance(size, str) and size.isdigit() else None,
    }


def scan_library(library: str, platform: str,
                 listdir: Callable[[str], List[str]] = os.listdir,
                 read: Callable[[str], Optional[str]] = read_text) -> Tuple[List[dict], List[str]]:
    """Games in one library as GameEntry dicts (store "steam") plus notes."""
    steamapps = os.path.join(library, "steamapps")
    games, notes = [], []
    for fname in sorted(listdir(steamapps)):
        m = MANIFEST_RE.match(fname)
        if not m:
            continue
        text = read(os.path.join(steamapps, fname))
        manifest = parse_manifest(text, int(m.group(1))) if text is not None else None
        if manifest is None:
            notes.append(f"malformed manifest skipped: {fname}")
            continue
        bench = STEAM_BENCHMARK_APPS_BY_ID.get(manifest["appid"])
        games.append({
            "store": "steam", "app_id": str(manifest["appid"]), "name": manifest["name"],
            "install_path": os.path.join(steamapps, "common", manifest["installdir"]),
            "version": manifest["buildid"],
            "library": library, "installed": manifest["fully_installed"],
            "size_on_disk": manifest["size_on_disk"], "platform": platform,
            # Windows: native. Linux: native vs Proton is not read reliably yet.
            "runtime": "native" if platform == "windows" else "unavailable",
            "builtin_benchmark": bench["builtin_benchmark"] if bench else None,
            "kind": "tool" if _TOOL_RE.search(manifest["name"]) else "game",
        })
    return games, notes

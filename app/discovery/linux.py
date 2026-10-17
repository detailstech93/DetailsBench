"""Linux installed-software discovery (unverified on real hardware).

Package managers run read-only with a timeout; their output goes through
the pure parsers and is matched against data/known_software.py by exact
package / executable name. Unmatched packages are dropped at once.
The command runner and `which` are injectable for tests.
"""

import os
import shutil
import subprocess
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from app.discovery.linux_packages import (
    executables_on_path, parse_dpkg_query, parse_flatpak_list, parse_rpm_qa, parse_snap_list,
)
from app.discovery.result import DiscoveryResult, store_status
from data.known_software import KNOWN_SOFTWARE, KnownSoftware

NOTE = " (unverified on real hardware)"

# (step name, command, parser)
PACKAGE_MANAGERS = [
    ("dpkg", ["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n"], parse_dpkg_query),
    ("rpm", ["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"], parse_rpm_qa),
    ("flatpak", ["flatpak", "list", "--app", "--columns=application,version"], parse_flatpak_list),
    ("snap", ["snap", "list"], parse_snap_list),
]


def run_command(cmd: Sequence[str], timeout: int = 15) -> Optional[str]:
    """Run a read-only command; None when it is missing or fails."""
    try:
        res = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def match_linux(entries: Iterable[dict], known: Sequence[KnownSoftware] = KNOWN_SOFTWARE) -> List[dict]:
    """Keep entries whose name is a known package or executable (exact match)."""
    by_name: Dict[str, KnownSoftware] = {}
    for record in known:
        for name in list(record.linux_packages) + list(record.linux_executables):
            by_name.setdefault(name, record)
    out, seen = [], set()
    for e in entries:
        record = by_name.get(e.get("name") or "")
        if record is None or (record.id, e["name"], e.get("source")) in seen:
            continue
        seen.add((record.id, e["name"], e.get("source")))
        out.append({"id": record.id, "display_name": record.display_name, "kind": record.kind,
                    "name": e["name"], "version": e.get("version"), "path": e.get("location"),
                    "source": (e.get("source") or "") + NOTE})
    return out


def discover_linux(run: Callable[[Sequence[str]], Optional[str]] = run_command,
                   which: Callable[[str], Optional[str]] = shutil.which,
                   home: Optional[str] = None) -> DiscoveryResult:
    result = DiscoveryResult()
    entries: List[dict] = []

    for name, cmd, parser in PACKAGE_MANAGERS:
        def step(cmd=cmd, parser=parser):
            if which(cmd[0]) is None:
                return
            output = run(cmd)
            if output is not None:
                entries.extend(parser(output))
        result.run_step(name, step)

    def path_step():
        names = sorted({n for k in KNOWN_SOFTWARE for n in k.linux_executables})
        entries.extend(executables_on_path(names, which))
    result.run_step("path", path_step)
    result.software = match_linux(entries)

    def steam():
        base = home or os.path.expanduser("~")
        for rel in (".steam/steam", ".local/share/Steam"):
            path = os.path.join(base, rel)
            if os.path.isdir(path):
                result.stores["steam"] = store_status("detected", path, "Steam folder" + NOTE)
                return
        result.stores["steam"] = store_status("not detected", None, "~/.steam/steam, ~/.local/share/Steam")
    result.run_step("steam_client", steam)
    from app.steam.discovery import add_steam_games
    add_steam_games(result, "linux")
    return result

"""Which one-click install fits this computer (data/installers.py): OS, machine architecture, Linux family.

plan_for() returns one of
    {"kind": "winget", "test_id", "winget", "hosts", "path_prefix"}      Windows, an installer for this arch
    {"kind": "linux", "family", "package"}                                Linux, a package for this family
    {"kind": "steam", "app_id"}                                           Steam's install dialog
    {"kind": "lmstudio", "test_id"}                                       a fixed model through LM Studio's lms get
    {"kind": "zip", "test_id", **pinned zip}                              the vendor's zip (app/install/direct.py;
                                                                          "zip_by_arch": one per architecture)
or None: no install fits, the Tests screen keeps the official download page.
"""

import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

from data.installers import INSTALLERS


@dataclass(frozen=True)
class Host:
    os: str                         # "windows" | "linux" | other
    arch: str                       # "x64" | "arm64" | "x86" | "unknown"
    family: Optional[str] = None    # Linux: debian | fedora | arch | suse | None


@lru_cache(maxsize=1)
def this_host() -> Host:
    from app.frames.provision import native_arch
    arch = native_arch()
    if sys.platform == "win32":
        return Host("windows", arch)
    if sys.platform.startswith("linux"):
        from app.frames.linux_install import distro_family
        from app.ui.frame_capture_panel import read_os_release
        return Host("linux", arch, distro_family(read_os_release()))
    return Host(sys.platform, arch)


def plan_for(test_id: str, source: str = "", app_id=None, host: Optional[Host] = None) -> Optional[dict]:
    host = host or this_host()
    if source == "steam" and app_id is not None:
        if host.os not in ("windows", "linux"):
            return None
        try:
            return {"kind": "steam", "app_id": int(app_id)}
        except (TypeError, ValueError):
            return None
    from app.ai.model_set import all_models
    if test_id.startswith("llama_benchy_") and test_id in all_models():   # LM Studio downloads the model itself
        return {"kind": "lmstudio", "test_id": test_id} if host.os in ("windows", "linux") else None
    entry = (INSTALLERS.get(test_id) or {}).get(host.os)
    if not entry:
        return None
    if "steam" in entry:
        return {"kind": "steam", "app_id": int(entry["steam"])}
    if entry.get("zip"):
        if host.arch not in entry.get("arch", []):
            return None
        return {"kind": "zip", "test_id": test_id, **entry["zip"]}
    if entry.get("zip_by_arch"):                # one vendor zip per machine architecture
        z = entry["zip_by_arch"].get(host.arch)
        return {"kind": "zip", "test_id": test_id, **z} if z else None
    if host.os == "windows" and entry.get("winget"):
        if host.arch not in entry.get("arch", []):
            return None
        return {"kind": "winget", "test_id": test_id, "winget": entry["winget"], "hosts": list(entry["hosts"]),
                "path_prefix": entry.get("path_prefix", "")}
    if host.os == "linux":
        package = entry.get("packages", {}).get(host.family)
        return {"kind": "linux", "family": host.family, "package": package} if package else None
    return None


# Linux: the distribution's package manager through pkexec (app/frames/linux_install.py names the managers)
LINUX_INSTALL_ARGS = {
    "apt-get": lambda pkg: ["install", "-y", pkg],
    "dnf": lambda pkg: ["install", "-y", pkg],
    "pacman": lambda pkg: ["-S", "--noconfirm", "--needed", pkg],
    "zypper": lambda pkg: ["--non-interactive", "install", pkg],
}


def linux_commands(family: str, package: str, which: Callable[[str], Optional[str]] = shutil.which,
                   askpass: Optional[Callable[[], Optional[str]]] = None) -> Tuple[List[List[str]], Optional[str]]:
    """(argv list to run in order, None) or ([], why not). apt-get refreshes its package list first."""
    from app.frames.linux_install import PACKAGE_MANAGERS
    manager = next((m for m, f in PACKAGE_MANAGERS if f == family), None)
    if manager is None:
        return [], f"no package manager known for {family}"
    manager_path = which(manager)
    if not manager_path:
        return [], f"{manager} not found"
    from app.install.linux_admin import admin_prefix       # pkexec, else sudo with the app's password window
    root, why = admin_prefix(which, askpass)
    if why:
        return [], f"{why}; install {package} with {manager} yourself"
    commands = [root + [manager_path, "update"]] if manager == "apt-get" else []
    return commands + [root + [manager_path] + LINUX_INSTALL_ARGS[manager](package)], None

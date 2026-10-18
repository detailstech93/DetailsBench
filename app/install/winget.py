"""One-click install of a missing test on Windows (data/installers.py), from the vendor's own site.
Which install fits this computer is decided by app/install/plans.py.

Two ways, both official:
- winget (Microsoft's Windows Package Manager): `winget show` gives the installer URL and its SHA256. The owner
  sees name, version, publisher, URL, size and SHA256 and confirms; a URL that is not on the vendor's host is
  refused before anything is downloaded. `winget install` then downloads it, checks the SHA256 itself and runs
  the installer (which shows UAC when it needs administrator rights). Nothing runs elevated from the app.
- Steam: steam://install/<appid> opens Steam's own install dialog (size and folder chosen there).

Every command is an argument list with a timeout; no shell. The package agreement is accepted for winget only
after the owner clicked Install in the dialog that shows the license link (--accept-package-agreements); the
source agreement is never accepted for them.
"""

import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from data.installers import INSTALLERS

SHOW_TIMEOUT_SECONDS = 90
INSTALL_TIMEOUT_SECONDS = 1800
SIZE_TIMEOUT_SECONDS = 15
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Runner = Callable[..., "subprocess.CompletedProcess"]


@dataclass
class Package:
    """What `winget show` says about a package, checked against data/installers.py."""
    test_id: str
    package_id: str
    name: str = ""
    version: str = ""
    publisher: str = ""
    url: str = ""
    sha256: str = ""
    installer_type: str = ""
    license_url: str = ""
    size_bytes: Optional[int] = None
    problem: Optional[str] = None          # set: the install is refused, with the reason


@dataclass
class Outcome:
    ok: bool
    message: str
    output: List[str] = field(default_factory=list)


def steam_install_url(app_id) -> str:
    return f"steam://install/{int(app_id)}"


def _field(text: str, label: str) -> str:
    m = re.search(rf"^\s*{re.escape(label)}:[ \t]*(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_show(text: str, test_id: str, package_id: str, hosts: List[str], path_prefix: str = "") -> Package:
    """Read `winget show` output. Several installers (one per architecture) are not handled: the first listed
    is the one winget picks for this PC only when it is the only one, so more than one is refused."""
    pkg = Package(test_id, package_id)
    found = re.search(r"^Found (.+?) \[(.+?)\]\s*$", text, re.MULTILINE)
    if not found or found.group(2).lower() != package_id.lower():
        pkg.problem = f"winget did not find the package {package_id}"
        return pkg
    pkg.name = found.group(1).strip()
    pkg.version = _field(text, "Version")
    pkg.publisher = _field(text, "Publisher")
    pkg.license_url = _field(text, "License Url")
    pkg.installer_type = _field(text, "Installer Type")
    urls = re.findall(r"^\s*Installer Url:[ \t]*(\S+)\s*$", text, re.MULTILINE)
    shas = re.findall(r"^\s*Installer SHA256:[ \t]*([0-9A-Fa-f]{64})\s*$", text, re.MULTILINE)
    if len(urls) != 1 or len(shas) != 1:
        pkg.problem = f"winget lists {len(urls)} installers for {package_id}; expected exactly one with its SHA256"
        return pkg
    pkg.url, pkg.sha256 = urls[0], shas[0].lower()
    parsed = urlparse(pkg.url)
    if parsed.scheme != "https" or parsed.hostname not in hosts or not parsed.path.startswith(path_prefix or "/"):
        pkg.problem = (f"the installer is not on the vendor's site ({pkg.url}); expected https on "
                       + ", ".join(hosts) + (f" under {path_prefix}" if path_prefix else ""))
    return pkg


def show_args(package_id: str) -> List[str]:
    return ["winget", "show", "--id", package_id, "--exact", "--source", "winget", "--disable-interactivity"]


def install_args(package_id: str, version: str) -> List[str]:
    """The exact version the owner was shown (a newer one published meanwhile is not installed instead)."""
    args = ["winget", "install", "--id", package_id, "--exact", "--source", "winget", "--disable-interactivity",
            "--accept-package-agreements"]
    return args + (["--version", version] if version else [])


def describe(test_id: str, run: Runner = subprocess.run, size_of: Optional[Callable[[str], Optional[int]]] = None
             ) -> Package:
    """Ask winget about the package for this test (reads only; nothing is downloaded except a HEAD request
    to the vendor's URL for the size)."""
    entry = (INSTALLERS.get(test_id) or {}).get("windows") or {}
    package_id = entry.get("winget")
    if not package_id:
        return Package(test_id, "", problem="no winget package is known for this test")
    try:
        done = run(show_args(package_id), capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=SHOW_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    except FileNotFoundError:
        return Package(test_id, package_id, problem="winget (Windows Package Manager) is not installed")
    except subprocess.TimeoutExpired:
        return Package(test_id, package_id, problem=f"winget did not answer within {SHOW_TIMEOUT_SECONDS} s")
    pkg = parse_show(done.stdout or "", test_id, package_id, entry.get("hosts", []), entry.get("path_prefix", ""))
    if pkg.problem is None and done.returncode != 0:
        pkg.problem = f"winget show ended with code {done.returncode}"
    if pkg.problem is None:
        pkg.size_bytes = (size_of or head_size)(pkg.url)
    return pkg


def head_size(url: str, opener: Callable = urllib.request.urlopen) -> Optional[int]:
    """Content-Length of the installer from the vendor's server (None when it does not say)."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "PCBenchmarkManager"})
        with opener(req, timeout=SIZE_TIMEOUT_SECONDS) as resp:
            value = resp.headers.get("Content-Length")
            return int(value) if value and value.isdigit() else None
    except Exception:
        return None


def size_text(size: Optional[int]) -> str:
    if size is None:
        return "not given by the server"
    return f"{size / 1024 ** 2:.1f} MB" if size >= 1024 ** 2 else f"{size / 1024:.0f} KB"


def confirm_text(pkg: Package) -> str:
    return (f"Install {pkg.name} {pkg.version} for this test?\n\n"
            f"Publisher: {pkg.publisher or 'not given'}\n"
            f"Downloaded from: {pkg.url}\n"
            f"Size: {size_text(pkg.size_bytes)}\n"
            f"SHA256 (winget checks it before installing): {pkg.sha256}\n"
            + (f"License: {pkg.license_url}\n" if pkg.license_url else "")
            + "\nWindows Package Manager (winget) downloads it from that address and runs the vendor's installer. "
              "Windows may ask for administrator rights (UAC). By installing you accept the program's license.")


def install(pkg: Package, run: Runner = subprocess.run, on_line: Callable[[str], None] = lambda s: None) -> Outcome:
    if pkg.problem:
        return Outcome(False, f"Not installed: {pkg.problem}")
    try:
        done = run(install_args(pkg.package_id, pkg.version), capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=INSTALL_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    except FileNotFoundError:
        return Outcome(False, "winget (Windows Package Manager) is not installed")
    except subprocess.TimeoutExpired:
        return Outcome(False, f"winget did not finish within {INSTALL_TIMEOUT_SECONDS // 60} minutes")
    lines = [ln.strip() for ln in (done.stdout or "").splitlines()
             if ln.strip() and not re.fullmatch(r"[-\\|/\s█▒]+|.*\d+(\.\d+)? [KMG]B / .*", ln.strip())]
    for ln in lines[-8:]:
        on_line(ln)
    if done.returncode == 0:
        return Outcome(True, f"{pkg.name} {pkg.version} installed.", lines)
    last = lines[-1] if lines else ""
    return Outcome(False, f"winget could not install {pkg.name} (code {done.returncode}). {last}".strip(), lines)

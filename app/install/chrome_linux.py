"""Google Chrome for the web tests on Linux: the owner asked for one button (2026-10-20). Debian/Ubuntu get Google's
.deb, Fedora Google's .rpm (owner, 2026-11-19: Fedora's own Chromium was 30% behind Google Chrome on Speedometer),
each for x64 and arm64 (Google's lists have arm64/aarch64 since 2026; checked 2026-11-19).

Chrome has no fixed version to pin: Google's own apt index on dl.google.com lists the current stable package with
its file, size and SHA-256 (dists/stable/main/binary-amd64/Packages, read 2026-10-20). The app reads that index over
HTTPS, downloads that file from the same server, keeps it only if size and SHA-256 match the index, and then the
distribution's apt installs it (with its dependencies) through pkexec or sudo, the owner typing the password.
Chrome's own package then adds Google's apt source, so apt keeps Chrome up to date (Google's installer does that,
not the app). Nothing else is run. Fedora: the same from Google's rpm repository (repodata/repomd.xml names its
primary.xml.gz, which lists the package's file, size and SHA-256); dnf installs the file and Chrome adds Google's
dnf source. openSUSE is not offered: zypper stops at Google's signature until its key is imported by hand.
"""

import hashlib
import zlib
import xml.etree.ElementTree as ET

import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

from app.install.direct import tools_root

SERVER = "https://dl.google.com/linux/chrome/deb/"
INDEX_URL = SERVER + "dists/stable/main/binary-amd64/Packages"
RPM_SERVER = "https://dl.google.com/linux/chrome/rpm/stable/"
DEB_ARCH = {"x64": "amd64", "arm64": "arm64"}
RPM_ARCH = {"x64": "x86_64", "arm64": "aarch64"}
FAMILIES = ("debian", "fedora")
MAX_PRIMARY_BYTES = 8 * 1024 * 1024
PACKAGE = "google-chrome-stable"
TERMS_URL = "https://www.google.com/chrome/terms/"
MAX_INDEX_BYTES = 1024 * 1024
MAX_DEB_BYTES = 600 * 1024 * 1024


def deb_path(data_dir: Optional[Path] = None) -> Path:
    return package_path("debian", "x64", data_dir)


def package_path(family: str, arch: str, data_dir: Optional[Path] = None) -> Path:
    """Where the checked package is kept until it is installed; its name says which list it comes from."""
    name = (f"{PACKAGE}_current_{DEB_ARCH[arch]}.deb" if family == "debian"
            else f"{PACKAGE}-current.{RPM_ARCH[arch]}.rpm")
    return tools_root(data_dir) / "downloads" / name


def _source(path: Path) -> Tuple[str, str]:
    """("deb", its Packages list) or ("rpm", its repository folder) for a path package_path made."""
    name = Path(path).name
    if name.endswith(".rpm"):
        return "rpm", f"{RPM_SERVER}{'aarch64' if 'aarch64' in name else 'x86_64'}/"
    return "deb", f"{SERVER}dists/stable/main/binary-{'arm64' if '_arm64' in name else 'amd64'}/Packages"


def parse_index(text: str, package: str = PACKAGE) -> Optional[dict]:
    """{version, filename, size, sha256} of `package` in an apt Packages index, or None."""
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
        if fields.get("Package") != package:
            continue
        name, sha = fields.get("Filename", ""), fields.get("SHA256", "")
        try:
            size = int(fields.get("Size", ""))
        except ValueError:
            return None
        if not name.startswith("pool/") or ".." in name or not name.endswith(".deb") or len(sha) != 64 or size <= 0:
            return None
        return {"version": fields.get("Version", ""), "filename": name, "size": size, "sha256": sha.lower()}
    return None


def question(deb: Path) -> str:
    rpm = _source(deb)[0] == "rpm"
    tool, source = ("dnf", "dnf") if rpm else ("apt-get", "apt")
    return ("Download and install Google Chrome (stable) for the web tests?\n\n"
            f"The newest stable package from Google's own server ({RPM_SERVER if rpm else SERVER}): its size and "
            f"SHA-256 are read from Google's package list there and the file is kept only if both match. Then {tool} "
            f"installs it:\n{tool} install -y {deb}\n\n"
            f"The system asks for your password. Chrome's package also adds Google's {source} source, so your system "
            f"updates Chrome later. By installing it you accept Google Chrome's terms: {TERMS_URL}")


def _xml(text: str):
    """The parsed list, or None; a list with a DOCTYPE (entities) is refused, Google's never has one."""
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def parse_repomd(text: str) -> Optional[dict]:
    """{href, sha256} of the primary list in an rpm repository's repomd.xml, or None."""
    ns = {"r": "http://linux.duke.edu/metadata/repo"}
    root = _xml(text)
    if root is None:
        return None
    for data in root.findall("r:data", ns):
        if data.get("type") != "primary":
            continue
        loc, sha = data.find("r:location", ns), data.find("r:checksum", ns)
        href = loc.get("href", "") if loc is not None else ""
        digest = (sha.text or "").strip().lower() if sha is not None and sha.get("type") == "sha256" else ""
        if not href.startswith("repodata/") or ".." in href or len(digest) != 64:
            return None
        return {"href": href, "sha256": digest}
    return None


def parse_primary(text: str, package: str = PACKAGE) -> Optional[dict]:
    """{version, filename, size, sha256} of `package` in an rpm repository's primary.xml, or None."""
    ns = {"c": "http://linux.duke.edu/metadata/common"}
    root = _xml(text)
    if root is None:
        return None
    for pkg in root.findall("c:package", ns):
        if (pkg.findtext("c:name", "", ns) or "").strip() != package:
            continue
        sha, size, loc, ver = (pkg.find(f"c:{k}", ns) for k in ("checksum", "size", "location", "version"))
        name = loc.get("href", "") if loc is not None else ""
        digest = (sha.text or "").strip().lower() if sha is not None and sha.get("type") == "sha256" else ""
        try:
            nbytes = int(size.get("package", "")) if size is not None else 0
        except ValueError:
            return None
        if "/" in name or ".." in name or not name.endswith(".rpm") or len(digest) != 64 or nbytes <= 0:
            return None
        version = f"{ver.get('ver', '')}-{ver.get('rel', '')}" if ver is not None else ""
        return {"version": version, "filename": name, "size": nbytes, "sha256": digest}
    return None


def _read(url: str, opener: Callable, limit: int) -> bytes:
    from app.frames.provision import USER_AGENT
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=60) as response:
        return response.read(limit)


def _rpm_entry(folder: str, opener: Callable) -> Tuple[Optional[dict], str]:
    repomd = parse_repomd(_read(folder + "repodata/repomd.xml", opener, MAX_INDEX_BYTES).decode("utf-8", "replace"))
    if repomd is None:
        return None, "Google's rpm list could not be understood"
    packed = _read(folder + repomd["href"], opener, MAX_PRIMARY_BYTES)
    if hashlib.sha256(packed).hexdigest() != repomd["sha256"]:
        return None, "Google's rpm list did not match its checksum"
    if repomd["href"].endswith(".gz"):          # capped: never more than MAX_PRIMARY_BYTES unpacked
        packed = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(packed, MAX_PRIMARY_BYTES)
    text = packed.decode("utf-8", "replace")
    return parse_primary(text), ""


def fetch(deb: Path, opener: Callable = urllib.request.urlopen,
          progress: Callable[[int, int], None] = lambda d, t: None) -> Optional[str]:
    """Download the current stable .deb or .rpm to `deb`, checked against Google's list. None, or why not."""
    from app.frames.provision import download_verified
    kind, where = _source(deb)
    try:
        if kind == "rpm":
            entry, why = _rpm_entry(where, opener)
            if why:
                return why
            server = where
        else:
            entry = parse_index(_read(where, opener, MAX_INDEX_BYTES).decode("utf-8", errors="replace"))
            server = SERVER
    except Exception as e:
        return f"Google's package list could not be read ({e})"
    if entry is None:
        return f"no {PACKAGE} in Google's package list"
    if entry["size"] > MAX_DEB_BYTES:
        return f"Google's list gives {entry['size']} bytes, more than expected"
    deb.parent.mkdir(parents=True, exist_ok=True)
    try:
        deb.unlink()                    # our own leftover, not yet verified
    except FileNotFoundError:
        pass
    return download_verified(server + entry["filename"], deb, entry["size"], entry["sha256"], opener,
                             progress=progress, max_bytes=MAX_DEB_BYTES)


def prepare(plan: dict, progress: Callable[[int, int], None] = lambda d, t: None, fetch_fn=None) -> Optional[str]:
    """Before a Linux install plan's commands: Chrome's file is downloaded first. None, or why not."""
    if plan.get("fetch") != "chrome":
        return None
    return (fetch_fn or fetch)(Path(plan["package"]), progress=progress)


def cleanup(plan: dict) -> None:
    if plan.get("fetch") == "chrome":
        try:
            Path(plan["package"]).unlink()
        except OSError:
            pass

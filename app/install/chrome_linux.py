"""Google Chrome for the web tests on Linux (Debian/Ubuntu, x64): the owner asked for one button (2026-10-20).

Chrome has no fixed version to pin: Google's own apt index on dl.google.com lists the current stable package with
its file, size and SHA-256 (dists/stable/main/binary-amd64/Packages, read 2026-10-20). The app reads that index over
HTTPS, downloads that file from the same server, keeps it only if size and SHA-256 match the index, and then the
distribution's apt installs it (with its dependencies) through pkexec or sudo, the owner typing the password.
Chrome's own package then adds Google's apt source, so apt keeps Chrome up to date (Google's installer does that,
not the app). Nothing else is run.
"""

import urllib.request
from pathlib import Path
from typing import Callable, Optional

from app.install.direct import tools_root

SERVER = "https://dl.google.com/linux/chrome/deb/"
INDEX_URL = SERVER + "dists/stable/main/binary-amd64/Packages"
PACKAGE = "google-chrome-stable"
TERMS_URL = "https://www.google.com/chrome/terms/"
MAX_INDEX_BYTES = 1024 * 1024
MAX_DEB_BYTES = 600 * 1024 * 1024


def deb_path(data_dir: Optional[Path] = None) -> Path:
    return tools_root(data_dir) / "downloads" / f"{PACKAGE}_current_amd64.deb"


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
    return ("Download and install Google Chrome (stable) for the web tests?\n\n"
            f"The newest stable package from Google's own server ({SERVER}): its size and SHA-256 are read from "
            f"Google's package list there and the file is kept only if both match. Then apt installs it:\n"
            f"apt-get install -y {deb}\n\n"
            "The system asks for your password. Chrome's package also adds Google's apt source, so your system "
            f"updates Chrome later. By installing it you accept Google Chrome's terms: {TERMS_URL}")


def fetch(deb: Path, opener: Callable = urllib.request.urlopen,
          progress: Callable[[int, int], None] = lambda d, t: None) -> Optional[str]:
    """Download the current stable .deb to `deb`, checked against Google's index. None, or why not."""
    from app.frames.provision import USER_AGENT, download_verified
    try:
        request = urllib.request.Request(INDEX_URL, headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=60) as response:
            text = response.read(MAX_INDEX_BYTES).decode("utf-8", errors="replace")
    except Exception as e:
        return f"Google's package list could not be read ({e})"
    entry = parse_index(text)
    if entry is None:
        return f"no {PACKAGE} in Google's package list"
    if entry["size"] > MAX_DEB_BYTES:
        return f"Google's list gives {entry['size']} bytes, more than expected"
    deb.parent.mkdir(parents=True, exist_ok=True)
    try:
        deb.unlink()                    # our own leftover, not yet verified
    except FileNotFoundError:
        pass
    return download_verified(SERVER + entry["filename"], deb, entry["size"], entry["sha256"], opener,
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

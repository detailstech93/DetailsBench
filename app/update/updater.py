"""Is there a newer DetailsBench, and update this copy to it (owner, 2026-10-20: "either the user goes to Advanced and
updates, or the app checks when it opens, says there is an update, and they download it if they want").

Where new versions come from: the GitHub Releases of detailstech93/DetailsBench. A release is a tag vX.Y.Z (a
"-beta.N" ending is a test version, offered only when "Include beta versions" is on); app/__init__.py's __version__
is this copy's version. The check is one HTTPS GET of the public releases list (GitHub's REST API, no sign-in):
nothing about this PC is sent. While the repository is private GitHub answers 404 and the check says "no published
release yet".

When: at start at most once a day (settings.json "update_check", on by default; "update_last_check"), and Advanced >
Updates > "Check now". Never while tests run (app/ui/update_ui.py). A version the user skipped is not offered again
("update_skip").

Update: only on the user's click, and only for a git copy (install.sh on Linux and WSL, a git clone on Windows):
  git status (no changes of its own) -> git fetch --tags origin -> git merge --ff-only <tag> (the copy moves to the
  release; a copy with commits of its own, such as a development copy, is never changed) -> pip install -r
  requirements.txt with the app's own Python -> restart. The commit it came from is kept ("update_previous") to go
  back with git. The data folder (database, settings, reports) is not touched. A copy that is not a git copy (a
  packaged build) gets the release page to download from instead.
"""

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app import __version__

REPO = "detailstech93/DetailsBench"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
PAGE_URL = f"https://github.com/{REPO}/releases"
USER_AGENT = "DetailsBench-update-check"
ROOT = Path(__file__).resolve().parent.parent.parent
EVERY = timedelta(days=1)
VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.]+))?$")
# settings.json keys
CHECK_KEY, LAST_KEY, SKIP_KEY, BETA_KEY, PREVIOUS_KEY = (
    "update_check", "update_last_check", "update_skip", "update_beta", "update_previous")


def version_key(text: str) -> Optional[tuple]:
    """A sortable key for "v1.2.3" / "1.2.3-beta.2", or None. A final version sorts after its betas."""
    m = VERSION.match((text or "").strip())
    if not m:
        return None
    pre = m.group(4)
    parts = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")) if pre else ()
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), 0 if pre else 1, parts


def is_beta(text: str) -> bool:
    m = VERSION.match((text or "").strip())
    return bool(m and m.group(4))


@dataclass
class Release:
    version: str            # "0.2.0" (the tag without its v)
    tag: str                # "v0.2.0"
    url: str                # the release's page
    notes: str = ""
    beta: bool = False


@dataclass
class Check:
    release: Optional[Release] = None       # a newer version, or None
    message: str = ""                       # what to show (the newest version, or why nothing is known)
    error: bool = False


def newest(releases: list, current: str = __version__, beta: bool = False) -> Optional[Release]:
    """The newest release above the current version (no drafts; betas only when asked for), from GitHub's list."""
    best, best_key = None, version_key(current)
    if best_key is None:
        return None
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        tag = str(r.get("tag_name") or "")
        key = version_key(tag)
        if key is None or ((r.get("prerelease") or is_beta(tag)) and not beta):
            continue
        if key > best_key:
            best, best_key = r, key
    if best is None:
        return None
    tag = str(best["tag_name"])
    return Release(version=tag.lstrip("v"), tag=tag, url=str(best.get("html_url") or PAGE_URL),
                   notes=str(best.get("body") or "")[:4000], beta=bool(best.get("prerelease") or is_beta(tag)))


def check(opener: Callable = urllib.request.urlopen, current: str = __version__, beta: bool = False,
          timeout: float = 20) -> Check:
    req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/vnd.github+json"})
    try:
        with opener(req, timeout=timeout) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return Check(message="No published release yet (the project's releases page is not public).")
        return Check(message=f"The check failed: GitHub answered {e.code}.", error=True)
    except (OSError, ValueError) as e:
        return Check(message=f"The check failed (no internet?): {e}", error=True)
    found = newest(releases, current, beta)
    if found:
        return Check(release=found, message=f"Version {found.version} is out (this is {current}).")
    return Check(message=f"DetailsBench {current} is the newest version.")


def due(settings: dict, now: Optional[datetime] = None) -> bool:
    """Check at start? On unless turned off, and not more than once a day."""
    if settings.get(CHECK_KEY, True) is False:
        return False
    now = now or datetime.now()
    try:
        last = datetime.fromisoformat(str(settings.get(LAST_KEY)))
    except ValueError:
        return True
    return not (timedelta(0) <= now - last < EVERY)


def offered(found: Optional[Release], settings: dict) -> bool:
    """Offer it in the bar, unless the user skipped this version."""
    return bool(found) and settings.get(SKIP_KEY) != found.version


# ---- the update ------------------------------------------------------------------------------------------------
@dataclass
class Outcome:
    ok: bool
    message: str
    previous: Optional[str] = None          # the commit the copy was on
    log: List[str] = field(default_factory=list)


def git_copy(root: Path = ROOT) -> bool:
    return (root / ".git").exists() and not getattr(sys, "frozen", False)


def update(release: Release, root: Path = ROOT, python: str = sys.executable, run: Callable = subprocess.run,
           progress: Callable[[str], None] = lambda text: None) -> Outcome:
    """Move this git copy to the release's tag and install its Python libraries. Never forces anything."""
    if not git_copy(root):
        return Outcome(False, f"This copy was not installed with git, so it cannot update itself: download "
                              f"version {release.version} from {release.url}")
    if version_key(release.tag) is None:
        return Outcome(False, f"Not a version tag: {release.tag!r}")
    log: List[str] = []

    def step(command: List[str], timeout: int) -> Tuple[int, str]:
        try:
            res = run(command, cwd=str(root), capture_output=True, text=True, timeout=timeout,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as e:
            log.append(f"$ {' '.join(command)}\n{e}")
            return -1, str(e)
        text = ((res.stdout or "") + (res.stderr or "")).strip()
        log.append(f"$ {' '.join(command)}\n{text}")
        return res.returncode, text

    def last_line(text: str) -> str:
        lines = [l for l in text.splitlines() if l.strip()]
        return lines[-1][:300] if lines else "no output"

    progress("Checking this copy...")
    code, text = step(["git", "status", "--porcelain", "--untracked-files=no"], 60)
    if code != 0:
        return Outcome(False, f"git could not read this copy: {last_line(text)}", log=log)
    if text:
        return Outcome(False, "This copy has changes of its own, so it was not updated (nothing was changed):\n"
                       + "\n".join(text.splitlines()[:8]), log=log)
    code, previous = step(["git", "rev-parse", "HEAD"], 60)
    previous = previous.strip() if code == 0 else None
    progress("Downloading the new version...")
    code, text = step(["git", "fetch", "--tags", "origin"], 600)
    if code != 0:
        return Outcome(False, f"Downloading failed (git fetch): {last_line(text)}", log=log)
    progress(f"Moving to {release.tag}...")
    code, text = step(["git", "merge", "--ff-only", f"refs/tags/{release.tag}"], 120)
    if code != 0:
        return Outcome(False, f"This copy could not move to {release.tag} without changing its own commits (a "
                              f"development copy?), so it was left as it was: {last_line(text)}", previous, log)
    progress("Installing the Python libraries...")
    code, text = step([python, "-m", "pip", "install", "--disable-pip-version-check", "-r",
                       str(root / "requirements.txt")], 1800)
    if code != 0:
        return Outcome(False, f"The app is now {release.version}, but installing its Python libraries failed: "
                              f"{last_line(text)}. Close it and run the install again.", previous, log)
    return Outcome(True, f"Updated to {release.version}. Restart the app to use it.", previous, log)


def restart_command(root: Path = ROOT, python: str = sys.executable) -> List[str]:
    return [python, str(root / "scripts" / "main.py")]

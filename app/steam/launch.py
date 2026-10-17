"""Safe Steam game launching through steam://rungameid/<appid>.

Valve warns that the steam:// browser protocol has had security issues, so:
- the URL is built only from an appid found in a local appmanifest
  (numeric, and present in the discovered set), never from free text;
- launch arguments are not passed (support varies per game: unverified);
- the caller must get explicit user confirmation first (UI does);
- success is judged by a NEW process whose executable lives inside the
  game's install folder (snapshot before/after); otherwise "unconfirmed".
Processes are never killed and Steam settings are never changed.
"""

import os
import subprocess
import sys
import time
from typing import Callable, Iterable, List, Optional, Set, Tuple

Snapshot = List[Tuple[str, Optional[str]]]


def build_launch_url(appid, discovered_appids: Iterable[str]) -> str:
    text = str(appid).strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"invalid Steam appid: {appid!r}")
    if text not in {str(a) for a in discovered_appids}:
        raise ValueError(f"appid {text} was not found in a local Steam manifest")
    return f"steam://rungameid/{text}"


def open_url(url: str) -> None:
    """Hand the URL to the OS handler (no shell, no extra arguments)."""
    if sys.platform == "win32":
        os.startfile(url)  # documented ShellExecute path for URL protocols
    else:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def new_processes_in_folder(before: Snapshot, after: Snapshot, folder: str) -> List[str]:
    """Executables under `folder` that appear in `after` more often than in `before`."""
    root = os.path.normcase(os.path.normpath(folder)) + os.sep

    def key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    remaining = [key(p) for _, p in before if p and key(p).startswith(root)]
    new = []
    for _, path in after:
        if not path or not key(path).startswith(root):
            continue
        if key(path) in remaining:
            remaining.remove(key(path))
        else:
            new.append(os.path.normpath(path))   # original spelling for messages
    return new


def launch_and_confirm(appid, install_path: str, discovered_appids: Iterable[str],
                       snapshot: Callable[[], Snapshot],
                       opener: Callable[[str], None] = open_url,
                       timeout: float = 60.0, poll: float = 2.0,
                       sleep: Callable[[float], None] = time.sleep,
                       clock: Callable[[], float] = time.monotonic) -> dict:
    """Launch one discovered game and report {status, message, url}.

    status: "launched" (new process seen in the install folder),
            "unconfirmed" (URL accepted, no such process within timeout),
            "error" (invalid appid or the OS handler failed).
    """
    try:
        url = build_launch_url(appid, discovered_appids)
    except ValueError as exc:
        return {"status": "error", "message": str(exc), "url": None}
    before = snapshot()
    try:
        opener(url)
    except Exception as exc:
        return {"status": "error", "message": f"could not open {url}: {exc}", "url": url}
    deadline = clock() + timeout
    while clock() < deadline:
        sleep(poll)
        new = new_processes_in_folder(before, snapshot(), install_path)
        if new:
            return {"status": "launched", "url": url,
                    "message": f"new process in the game folder: {os.path.basename(new[0])}"}
    return {"status": "unconfirmed", "url": url,
            "message": (f"Steam accepted {url}, but no new process appeared in the game folder "
                        f"within {int(timeout)} s (Steam may be updating or showing a dialog)")}

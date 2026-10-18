"""Safe Steam game launching through steam://rungameid/<appid>.

Valve warns that the steam:// browser protocol has had security issues, so:
- the URL is built only from an appid found in a local appmanifest
  (numeric, and present in the discovered set), never from free text;
- launch arguments are passed only when a game adapter names them (fixed
  flags such as Cyberpunk 2077's --launcher-skip, or forced settings such as
  -w 1920 or +r_mode 17; every token is checked against SAFE_ARG),
  with steam://run/<appid>//<args>/; they apply to that launch only and are
  never written to the game's Steam launch options;
- Steam asks "Launch Game with custom arguments?" for every steam://run URL. When the
  game's own executable is known (data/steam_benchmarks.py "exe", a path inside the
  install folder that exists), it is started directly with the arguments instead
  (Steam keeps running; the game still signs in through it), so no dialog appears;
- the caller must get explicit user confirmation first (UI does);
- success is judged by a NEW process whose executable lives inside the
  game's install folder (snapshot before/after); otherwise "unconfirmed".
Processes are never killed and Steam settings are never changed.
"""

import os
import re
import subprocess
import sys
import time
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

Snapshot = List[Tuple[str, Optional[str]]]
SAFE_ARG = re.compile(r"^(?:-{1,2}|\+)[A-Za-z][A-Za-z0-9_-]*$|^-?[0-9]+$")   # a flag, +cvar or whole number: no space, slash or quote


def build_launch_url(appid, discovered_appids: Iterable[str], args: Sequence[str] = ()) -> str:
    text = str(appid).strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"invalid Steam appid: {appid!r}")
    if text not in {str(a) for a in discovered_appids}:
        raise ValueError(f"appid {text} was not found in a local Steam manifest")
    args = list(args)
    if not args:
        return f"steam://rungameid/{text}"
    for arg in args:
        if not isinstance(arg, str) or not SAFE_ARG.fullmatch(arg):
            raise ValueError(f"unsafe launch argument: {arg!r}")
    return f"steam://run/{text}//{' '.join(args)}/"


def direct_exe(appid, install_path: str, args: Sequence[str] = ()) -> Optional[str]:
    """The game's own executable to start with `args` instead of steam://run (None: use Steam)."""
    if not args or not install_path:
        return None
    from data.steam_benchmarks import STEAM_BENCHMARK_APPS_BY_ID
    try:
        rel = STEAM_BENCHMARK_APPS_BY_ID.get(int(str(appid).strip()), {}).get("exe")
    except ValueError:
        return None
    if not rel:
        return None
    path = os.path.normpath(os.path.join(install_path, rel))        # keep the drive letter (no realpath)
    if not inside(path, folder_roots(install_path)) or not os.path.isfile(path):
        return None
    return path


def start_exe(command: List[str]) -> None:
    """Start the game executable (argument list, no shell); it is closed later like a Steam-started game."""
    subprocess.Popen(command, cwd=os.path.dirname(command[0]), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def open_url(url: str) -> None:
    """Hand the URL to the OS handler (no shell, no extra arguments)."""
    if sys.platform == "win32":
        os.startfile(url)  # documented ShellExecute path for URL protocols
    else:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def folder_roots(folder: str) -> Tuple[str, ...]:
    """The folder as written and as resolved: a mapped network drive (drive Z:) is also seen by Windows as its
    network share path, and a process started from it may report either spelling."""
    roots = {os.path.normcase(os.path.normpath(folder)) + os.sep}
    try:
        roots.add(os.path.normcase(os.path.realpath(folder)) + os.sep)
    except (OSError, ValueError):
        pass
    return tuple(sorted(roots))


def inside(path: Optional[str], roots: Sequence[str]) -> bool:
    return bool(path) and os.path.normcase(os.path.normpath(path)).startswith(tuple(roots))


def new_processes_in_folder(before: Snapshot, after: Snapshot, folder: str) -> List[str]:
    """Executables under `folder` that appear in `after` more often than in `before`."""
    roots = folder_roots(folder)

    def key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    remaining = [key(p) for _, p in before if inside(p, roots)]
    new = []
    for _, path in after:
        if not inside(path, roots):
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
                       clock: Callable[[], float] = time.monotonic, args: Sequence[str] = (),
                       starter: Callable[[List[str]], None] = start_exe) -> dict:
    """Launch one discovered game and report {status, message, url}.

    status: "launched" (new process seen in the install folder),
            "unconfirmed" (URL accepted, no such process within timeout),
            "error" (invalid appid or the OS handler failed).
    """
    try:
        url = build_launch_url(appid, discovered_appids, args)
    except ValueError as exc:
        return {"status": "error", "message": str(exc), "url": None}
    exe = direct_exe(appid, install_path, args)
    before = snapshot()
    try:
        if exe:
            starter([exe] + list(args))
            url = f"{os.path.basename(exe)} {' '.join(args)}"
        else:
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

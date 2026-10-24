"""The results library folder: on this PC, a NAS share or a synced cloud folder (owner, 2026-10-24).

Not one database file on the share (SQLite over SMB or a sync tool can break when two PCs write). Instead:
  <library>/library.json                                   what this folder is, and its format version
  <library>/packages/<unit id>/s<session>/<test>.json      one package per finished test, written once
  <library>/events/<package id>__<time>__<unit>.json       delete / restore, also written once; the newest wins
  <library>/names/<part>__<time>__<unit>.json              the owner's name for a device, newest wins
File names carry the PC's unit id, so two PCs never write the same file and no lock is needed.

Nothing is lost on a power cut or with the NAS away: a package is first written to this PC's outbox (next to the
database), then copied to the library under a temporary name and renamed. The outbox copy is removed only once
the library holds the same file (same hash). What is still in the outbox goes at the next send.
Nothing in the library is ever erased: a delete is an event that hides the result, and a restore shows it again.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from app.library.package import check, safe_name

LIBRARY_FILE = "library.json"
LIBRARY_FORMAT = "detailsbench.library"
LIBRARY_VERSION = 1            # the folder layout; a package's own version is checked per package (package.py)
EVENT_FORMAT = "detailsbench.library.event"


class LibraryError(Exception):
    pass


def _write_once(path: Path, data: Mapping) -> None:
    """A temporary name in the same folder, then a rename: a cut copy never has the final name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def open_library(folder: Path, create: bool = False) -> Path:
    """The library at `folder`. create=True makes a new one there when the folder is empty of a library (the
    owner picked it); a folder of a newer library format is refused, so an old app never misreads it."""
    folder = Path(folder)
    marker = folder / LIBRARY_FILE
    if not folder.is_dir():
        raise LibraryError(f"The folder {folder} cannot be reached (NAS or cloud folder offline?).")
    info = _read(marker)
    if info is None:
        if marker.exists():
            raise LibraryError(f"{marker} cannot be read.")
        if not create:
            raise LibraryError(f"{folder} is not a results library yet.")
        _write_once(marker, {"format": LIBRARY_FORMAT, "format_version": LIBRARY_VERSION,
                             "created_at": datetime.now().isoformat(timespec="seconds")})
        return folder
    if info.get("format") != LIBRARY_FORMAT:
        raise LibraryError(f"{marker} is not a DetailsBench results library.")
    if not isinstance(info.get("format_version"), int) or info["format_version"] > LIBRARY_VERSION:
        raise LibraryError("This library's folders are laid out by a newer DetailsBench: update the app to use it.")
    return folder


def relative_path(package: Mapping) -> Path:
    return (Path("packages") / safe_name(package["pc"]["unit_id"]) / f"s{package['session']['id']}"
            / f"{safe_name(package['test']['id'])}.json")


def to_outbox(outbox: Path, packages: List[dict]) -> int:
    """Each package into this PC's outbox; one already there (or already sent, same id) is kept as it is."""
    n = 0
    for pkg in packages:
        path = outbox / relative_path(pkg)
        if not path.exists():
            _write_once(path, pkg)
            n += 1
    return n


def waiting(outbox: Path) -> List[Path]:
    return sorted(p for p in Path(outbox).glob("packages/*/*/*.json")) if Path(outbox).is_dir() else []


def send(outbox: Path, library: Path) -> Tuple[int, List[str]]:
    """Copies what waits in the outbox into the library. (sent, problems). Stops at the first copy that fails
    (the share went away): the rest waits for the next send."""
    library = open_library(library)
    sent, problems = 0, []
    for path in waiting(outbox):
        pkg = _read(path)
        why = check(pkg) if pkg is not None else "cannot be read"
        if why:
            problems.append(f"{path.name}: {why} (kept in the outbox)")
            continue
        rel = path.relative_to(outbox)
        dest = library / rel
        try:
            there = _read(dest)
            if there is None:
                _write_once(dest, pkg)
                there = _read(dest)
            if there is None:
                problems.append(f"{rel}: written but cannot be read back (kept in the outbox)")
                continue
            if there.get("hash") == pkg["hash"]:
                sent += 1
            elif there.get("package_id") != pkg.get("package_id") or check(there):
                problems.append(f"{rel}: the library has another file of that name (kept in the outbox)")
                continue
            # else: this test of this session is in the library already (added again, e.g. "Add earlier
            # sessions"): the file there stays as it was written; this copy is not needed
            path.unlink()                   # the library holds it now
        except OSError as e:
            problems.append(f"Could not copy to the library: {e}. The rest waits in the outbox.")
            break
    return sent, problems


def write_event(library: Path, package_id: str, action: str, unit_id: str, reason: str = "",
                now: Optional[datetime] = None) -> Path:
    if action not in ("delete", "restore"):
        raise ValueError(action)
    library = open_library(library)
    now = now or datetime.now()
    stamp = now.strftime("%Y%m%dT%H%M%S%f")
    path = library / "events" / f"{safe_name(package_id)}__{stamp}__{safe_name(unit_id)}.json"
    _write_once(path, {"format": EVENT_FORMAT, "format_version": 1, "package_id": package_id, "action": action,
                       "reason": reason, "at": now.isoformat(timespec="seconds"), "by": unit_id})
    return path


def write_name(library: Path, part_key: str, name: str, unit_id: str, now: Optional[datetime] = None) -> Path:
    """The owner's name for a part (a device with no model name: "Living room mini PC"); empty takes it back."""
    library = open_library(library)
    now = now or datetime.now()
    path = library / "names" / f"{safe_name(part_key)}__{now.strftime('%Y%m%dT%H%M%S%f')}__{safe_name(unit_id)}.json"
    _write_once(path, {"format": "detailsbench.library.name", "format_version": 1, "part_key": part_key,
                       "name": name.strip(), "at": now.isoformat(timespec="seconds"), "by": unit_id})
    return path

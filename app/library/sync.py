"""Adding a session's results to the library: packages to the outbox, the outbox to the library folder, the
index refreshed. Settings (settings.json): library_folder (none until the owner picks one), library_auto (after
each run, on by default once a folder is picked). Without a folder the packages wait in the outbox.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from app.library import package as pk
from app.library import readings, store
from app.library.index import LibraryIndex

logger = logging.getLogger(__name__)

FOLDER_KEY = "library_folder"
AUTO_KEY = "library_auto"
OUTBOX = "library_outbox"
INDEX = "library_index.db"


def folder_setting(data_dir: Path) -> Optional[Path]:
    from app.paths import load_settings
    value = load_settings(data_dir)[0].get(FOLDER_KEY)
    return Path(value) if isinstance(value, str) and value.strip() else None


def auto_setting(data_dir: Path) -> bool:
    from app.paths import load_settings
    return bool(load_settings(data_dir)[0].get(AUTO_KEY, True))


def ai_run_reader(runs_root: Optional[Path], session_id: int):
    """The ai_run.json a run of this test wrote (app/ai), from the session's run folder."""
    from app.problem_report import session_folder
    folder = session_folder(Path(runs_root), session_id) if runs_root else None

    def read(test_id: str) -> Optional[dict]:
        if folder is None:
            return None
        for path in sorted(folder.glob(f"{test_id}_r*/ai_run.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return data
        return None
    return read


def send_waiting(data_dir: Path, library: Optional[Path] = None) -> str:
    """The outbox into the library and the index refreshed; a line saying what happened."""
    library = library or folder_setting(data_dir)
    outbox = Path(data_dir) / OUTBOX
    left = len(store.waiting(outbox))
    if library is None:
        return f"{left} result(s) wait on this PC: no results library folder is chosen."
    try:
        sent, problems = store.send(outbox, library)
        try:
            readings.send(outbox, library)          # the readings over each test, beside its result
        except (OSError, ValueError) as e:
            problems.append(f"Readings not copied: {e}. They wait in the outbox.")
        got = LibraryIndex(Path(data_dir) / INDEX).refresh(library)
    except store.LibraryError as e:
        return f"{left} result(s) wait on this PC: {e}"
    for p in problems + got["skipped"]:
        logger.warning("Results library: %s", p)
    text = f"{sent} result(s) added to the library"
    still = len(store.waiting(outbox))
    if still:
        text += f"; {still} still wait on this PC"
    return text + (f" ({len(problems)} problem(s), see the log)" if problems else "") + "."


def add_session(db, session_id: int, data_dir: Path, runs_root: Optional[Path] = None) -> str:
    """Every finished test of the session as a package, then sent when a library folder is chosen."""
    from app.export import build_session_export
    export = build_session_export(db, session_id)
    packages, left = pk.build_packages(export, ai_run_reader(runs_root, session_id))
    for test_id, why in left:
        logger.info("Results library: session %s %s not added: %s", session_id, test_id, why)
    new = store.to_outbox(Path(data_dir) / OUTBOX, packages)
    readings.to_outbox(Path(data_dir) / OUTBOX, packages, runs_root)
    logger.info("Results library: session %s: %d package(s), %d new", session_id, len(packages), new)
    if not packages:
        return "No result of this session goes to the library" + (f" ({left[0][1]})." if left else ".")
    return send_waiting(data_dir)


def after_run(window, session_id: Optional[int]) -> None:
    """Called when a run ends (the Tests screen): adds the session in a background thread, so a slow NAS never
    holds the app; never raises."""
    if session_id is None:
        return
    try:
        data_dir = Path(window.frame_panel.data_dir)
        if folder_setting(data_dir) is not None and not auto_setting(data_dir):
            return
        runs_root = getattr(window.automation_panel, "output_root", None)
        db = window.db
    except Exception:
        logger.exception("Results library: not added")
        return
    import threading

    def work():
        try:
            logger.info("Results library: %s", add_session(db, session_id, data_dir, runs_root))
            _done(data_dir, session_id)
        except Exception:
            logger.exception("Results library: session %s not added", session_id)
    threading.Thread(target=work, name="results-library", daemon=True).start()


def add_earlier(db, data_dir: Path, runs_root: Optional[Path] = None, this_pc: Optional[dict] = None) -> str:
    """Every session on this PC into the outbox, then sent. A session with the PC's parts record (from 0.1.3 on) as
    it is; an older one with a record made from the names it kept (phase 6, app/library/legacy.py), each result
    marked "driver unknown". A test already in the library stays as it is there."""
    from app.export import build_session_export
    from app.library import legacy
    count, sessions, older = 0, 0, 0
    for s in db.get_sessions():
        manifest = db.get_session_manifest(s["id"]) or {}
        old = not ((manifest.get("environment") or {}).get("hardware") or {}).get("unit_id")
        hw = legacy.hardware(manifest, this_pc) if old else None
        if old and hw is None:
            continue
        try:
            export = build_session_export(db, s["id"])
            if old:
                export["manifest"] = dict(export.get("manifest") or {})
                export["manifest"]["environment"] = dict(export["manifest"].get("environment") or {}, hardware=hw)
            packages, _ = pk.build_packages(export, ai_run_reader(runs_root, s["id"]))
        except Exception:
            logger.exception("Results library: session %s not added", s["id"])
            continue
        if old:
            packages = [legacy.mark(p) for p in packages]
            older += 1
        sessions += 1
        count += store.to_outbox(Path(data_dir) / OUTBOX, packages)
        readings.to_outbox(Path(data_dir) / OUTBOX, packages, runs_root)
    if not sessions:
        return "No earlier session to add."
    return (f"{sessions} session(s) read" + (f", {older} of them from before the parts record (driver unknown)"
                                             if older else "") + ". " + send_waiting(data_dir))


def _wanted(data_dir: Path) -> bool:
    """Added on its own unless a folder is chosen with "after each run" unticked."""
    return folder_setting(data_dir) is None or auto_setting(data_dir)


RUNNING = "running"


def _marker(data_dir: Path, session_id: int) -> Path:
    return Path(data_dir) / OUTBOX / RUNNING / f"s{int(session_id)}.json"


def mark_running(data_dir: Path, session_id: Optional[int]) -> None:
    """Owner, 2026-10-24: a run cut short (power cut, app closed) must not lose its finished tests. Each run is in
    the database once it ends; this marker says the session is not in the library yet, so the next app start adds
    it. Packages are built only when the run is over (a test packaged mid-run could miss its last run: the
    llama-benchy step between runs looked like the test's end, 0df42f7). Never raises."""
    try:
        if session_id is None:
            return
        path = _marker(data_dir, session_id)
        if not path.exists() and _wanted(Path(data_dir)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"session_id": int(session_id)}), encoding="utf-8")
    except Exception:
        logger.exception("Results library: run not marked")


def _done(data_dir: Path, session_id: int) -> None:
    try:
        _marker(data_dir, session_id).unlink(missing_ok=True)
    except OSError:
        logger.exception("Results library: run marker not removed")


def cut_short(data_dir: Path) -> list:
    """Sessions whose run never reached its end (their marker is still there)."""
    folder = Path(data_dir) / OUTBOX / RUNNING
    out = []
    for path in sorted(folder.glob("s*.json")) if folder.is_dir() else []:
        try:
            out.append(int(path.stem[1:]))
        except ValueError:
            continue
    return out


def send_at_start(window) -> None:
    """When the app opens: a run cut short is added from the database (every run it saved), and what waits on
    this PC goes to the library, in a background thread, only while no test runs. Never raises."""
    try:
        data_dir = Path(window.frame_panel.data_dir)
        if not _wanted(data_dir):
            return
        cut = cut_short(data_dir)
        if not cut and (folder_setting(data_dir) is None or not store.waiting(data_dir / OUTBOX)):
            return
        db, runs_root = window.db, getattr(window.automation_panel, "output_root", None)
    except Exception:
        logger.exception("Results library: waiting results not sent")
        return
    import threading

    def work():
        for sid in cut:
            try:
                logger.info("Results library (run cut short, session %s): %s", sid,
                            add_session(db, sid, data_dir, runs_root))
                _done(data_dir, sid)
            except Exception:
                logger.exception("Results library: session %s not added", sid)
        try:
            if not cut:
                logger.info("Results library (app start): %s", send_waiting(data_dir))
        except Exception:
            logger.exception("Results library: waiting results not sent")
    threading.Thread(target=work, name="results-library-start", daemon=True).start()

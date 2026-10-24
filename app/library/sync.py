"""Adding a session's results to the library: packages to the outbox, the outbox to the library folder, the
index refreshed. Settings (settings.json): library_folder (none until the owner picks one), library_auto (after
each run, on by default once a folder is picked). Without a folder the packages wait in the outbox.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from app.library import package as pk
from app.library import store
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
        except Exception:
            logger.exception("Results library: session %s not added", session_id)
    threading.Thread(target=work, name="results-library", daemon=True).start()


def add_earlier(db, data_dir: Path, runs_root: Optional[Path] = None) -> str:
    """Every session on this PC whose record has the PC's parts (from 0.1.3 on) into the outbox, then sent.
    A test already in the library stays as it is there."""
    from app.export import build_session_export
    count, sessions = 0, 0
    for s in db.get_sessions():
        manifest = db.get_session_manifest(s["id"]) or {}
        if not ((manifest.get("environment") or {}).get("hardware") or {}).get("unit_id"):
            continue
        try:
            packages, _ = pk.build_packages(build_session_export(db, s["id"]), ai_run_reader(runs_root, s["id"]))
        except Exception:
            logger.exception("Results library: session %s not added", s["id"])
            continue
        sessions += 1
        count += store.to_outbox(Path(data_dir) / OUTBOX, packages)
    if not sessions:
        return ("No earlier session has the record of the PC's parts (sessions from before 0.1.3, or run before "
                "the app was restarted after the update): nothing to add.")
    return f"{sessions} session(s) read. " + send_waiting(data_dir)


def _wanted(data_dir: Path) -> bool:
    """Added on its own unless a folder is chosen with "after each run" unticked."""
    return folder_setting(data_dir) is None or auto_setting(data_dir)


def keep_finished(export, test_ids, data_dir: Path, runs_root: Optional[Path] = None) -> int:
    """Owner, 2026-10-24: each test, once over, kept on this PC at once (the outbox, a few KB on the local disk),
    so a run cut short loses nothing; sending to the library (NAS / cloud) waits until the run is over, so no
    network or sync work runs while a test measures. Never raises; how many packages are new."""
    try:
        if not _wanted(Path(data_dir)):
            return 0
        session_id = (export.get("session") or {}).get("id")
        packages, _ = pk.build_packages(export, ai_run_reader(runs_root, session_id))
        wanted = set(test_ids)
        return store.to_outbox(Path(data_dir) / OUTBOX, [p for p in packages if p["test"]["id"] in wanted])
    except Exception:
        logger.exception("Results library: finished tests not kept")
        return 0


def send_at_start(window) -> None:
    """When the app opens: what waits on this PC (a run cut short, the NAS away last time) goes to the library,
    in a background thread, and only while no test runs. Never raises."""
    try:
        data_dir = Path(window.frame_panel.data_dir)
        if folder_setting(data_dir) is None or not auto_setting(data_dir):
            return
        if not store.waiting(data_dir / OUTBOX):
            return
    except Exception:
        logger.exception("Results library: waiting results not sent")
        return
    import threading

    def work():
        try:
            logger.info("Results library (app start): %s", send_waiting(data_dir))
        except Exception:
            logger.exception("Results library: waiting results not sent")
    threading.Thread(target=work, name="results-library-start", daemon=True).start()

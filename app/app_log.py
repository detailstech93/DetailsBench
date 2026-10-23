"""The app's own log file, for problem reports (app/problem_report.py).

Until 2026-10-23 the app's messages went nowhere when it ran without a console (pythonw). Now they go to
<data folder>/logs/detailsbench.log (at most 1 MB, then the last two older files are kept), and an error the app
did not catch is written there and to last_error.txt, so a tester's report shows it.
"""

import logging
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, List, Optional

LOG_NAME = "detailsbench.log"
ERROR_NAME = "last_error.txt"
MAX_BYTES = 1024 * 1024
BACKUPS = 2


def log_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "logs"


def setup(data_dir: Path, on_error: Optional[Callable[[str], None]] = None) -> Optional[Path]:
    """Sends the app's log to the file and catches errors nobody caught; None when the folder cannot be written."""
    folder = log_dir(data_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / LOG_NAME, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    previous = sys.excepthook

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        logging.getLogger("app").error("Error the app did not catch:\n%s", text)
        try:
            (folder / ERROR_NAME).write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{text}", encoding="utf-8")
        except OSError:
            pass
        if on_error is not None:
            try:
                on_error(text)
            except Exception:
                pass
        if previous not in (None, hook) and previous is not sys.__excepthook__:
            previous(kind, value, tb)

    sys.excepthook = hook
    logging.getLogger("app").info("DetailsBench started")
    return folder / LOG_NAME


def log_files(data_dir: Path) -> List[Path]:
    folder = log_dir(data_dir)
    names = [LOG_NAME] + [f"{LOG_NAME}.{i}" for i in range(1, BACKUPS + 1)] + [ERROR_NAME]
    return [folder / n for n in names if (folder / n).is_file()]


def last_error(data_dir: Path) -> str:
    try:
        return (log_dir(data_dir) / ERROR_NAME).read_text(encoding="utf-8")[-8000:]
    except OSError:
        return ""

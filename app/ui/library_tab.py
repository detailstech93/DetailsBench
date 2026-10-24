"""Advanced > Results library: where this PC's results go (this PC, a NAS share or a synced cloud folder), whether
they go after each run, and what waits to be sent. The library screen by part comes later (phase 4)."""

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from app.library import store, sync
from app.library.index import LibraryIndex

logger = logging.getLogger(__name__)


class LibraryTab(QWidget):
    def __init__(self, data_dir: Callable[[], Path], pick=None):
        super().__init__()
        self.data_dir = data_dir
        self.pick = pick or (lambda start: QFileDialog.getExistingDirectory(self, "Results library folder", start))
        layout = QVBoxLayout(self)
        intro = QLabel("Every PC you test can add its results to one results library: a folder on this PC, on the "
                       "NAS, or in a synced cloud folder (Google Drive, OneDrive). Each finished test is one small "
                       "file, written once. Results first wait on this PC, so nothing is lost when the folder is "
                       "offline. Game results are not added until the Games section is ready.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        row = QHBoxLayout()
        self.choose = QPushButton("Choose folder...")
        self.choose.clicked.connect(self.choose_folder)
        self.send_now = QPushButton("Send waiting results now")
        self.send_now.clicked.connect(self.send)
        row.addWidget(self.choose)
        row.addWidget(self.send_now)
        row.addStretch()
        layout.addLayout(row)
        self.auto = QCheckBox("Add the results after each run")
        self.auto.toggled.connect(self._save_auto)
        layout.addWidget(self.auto)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def refresh(self, text: str = "") -> None:
        data = Path(self.data_dir())
        folder = sync.folder_setting(data)
        self.folder_label.setText(f"Library folder: {folder}" if folder else "Library folder: none chosen yet")
        self.auto.blockSignals(True)
        self.auto.setChecked(sync.auto_setting(data))
        self.auto.blockSignals(False)
        waiting = len(store.waiting(data / sync.OUTBOX))
        try:
            count = len(LibraryIndex(data / sync.INDEX).results())
        except Exception:
            count = 0
        line = f"In the library (as this PC last read it): {count} result(s). Waiting on this PC: {waiting}."
        self.status.setText((text + "\n" if text else "") + line)

    def _save_auto(self, on: bool) -> None:
        from app.paths import save_setting
        save_setting(Path(self.data_dir()), sync.AUTO_KEY, bool(on))

    def choose_folder(self) -> None:
        data = Path(self.data_dir())
        start = str(sync.folder_setting(data) or Path.home())
        chosen = self.pick(start)
        if not chosen:
            return
        try:
            store.open_library(Path(chosen), create=True)
        except store.LibraryError as e:
            self.refresh(str(e))
            return
        from app.paths import save_setting
        save_setting(data, sync.FOLDER_KEY, str(Path(chosen)))
        logger.info("Results library folder: %s", chosen)
        self.send()

    def send(self) -> None:
        try:
            text = sync.send_waiting(Path(self.data_dir()))
        except Exception as e:
            logger.exception("Results library: send failed")
            text = f"Not sent: {e}"
        self.refresh(text)

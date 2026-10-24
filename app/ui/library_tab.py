"""Advanced > Results library: where this PC's results go (this PC, a NAS share or a synced cloud folder), and the
library itself by part (phase 4): devices, processors, graphics cards, memory, storage; each part's results with
their driver, accuracy and the PC they ran on; delete with a reason and restore; a name for an unnamed device."""

import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from app.library import store, sync, view
from app.library.index import LibraryIndex

logger = logging.getLogger(__name__)
COLUMNS = ("Test", "Result", "Accuracy", "Driver / software", "Variant", "Tested on", "Date", "Notes")


def _ask_text(parent, title: str, label: str, text: str = "") -> Optional[str]:
    value, ok = QInputDialog.getText(parent, title, label, text=text)
    return value if ok else None


class LibraryTab(QWidget):
    def __init__(self, data_dir: Callable[[], Path], pick=None, window=None, ask_text=None):
        super().__init__()
        self.data_dir = data_dir
        self.window = window
        self.pick = pick or (lambda start: QFileDialog.getExistingDirectory(self, "Results library folder", start))
        self.ask_text = ask_text or (lambda title, label, text="": _ask_text(self, title, label, text))
        self.results = []
        self.names = {}
        self._fitted = False
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
        self.earlier = QPushButton("Add earlier sessions")
        self.earlier.setToolTip("Sessions of this PC that have the record of its parts (from 0.1.3 on). A test "
                                "already in the library stays as it is there.")
        self.earlier.clicked.connect(self.add_earlier)
        self.reload = QPushButton("Refresh")
        self.reload.setToolTip("Reads what other PCs added to the library since the last look.")
        self.reload.clicked.connect(self.send)
        for b in (self.choose, self.send_now, self.earlier, self.reload):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        self.auto = QCheckBox("Add the results after each run")
        self.auto.toggled.connect(self._save_auto)
        layout.addWidget(self.auto)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Part", "Results"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 250)
        self.tree.setMinimumWidth(260)
        self.tree.currentItemChanged.connect(lambda *_: self.show_part())
        split.addWidget(self.tree)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.part_title = QLabel("Choose a part on the left.")
        self.part_title.setWordWrap(True)
        self.part_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        rl.addWidget(self.part_title)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)     # drag to resize
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._buttons)
        rl.addWidget(self.table)
        bar = QHBoxLayout()
        self.name_btn = QPushButton("Name this device...")
        self.name_btn.clicked.connect(self.name_part)
        self.delete_btn = QPushButton("Delete result...")
        self.delete_btn.setToolTip("Hides the result from the library and its comparisons, with your reason (for "
                                   "a retest). Nothing is erased: it can be restored.")
        self.delete_btn.clicked.connect(self.delete)
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.clicked.connect(self.restore)
        self.show_deleted = QCheckBox("Show deleted results")
        self.show_deleted.toggled.connect(lambda _: self.load())
        for w in (self.name_btn, self.delete_btn, self.restore_btn, self.show_deleted):
            bar.addWidget(w)
        bar.addStretch()
        rl.addLayout(bar)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        split.setSizes([340, 1000])
        layout.addWidget(split, 1)
        self._buttons()

    # ── settings and sending ──
    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def _data(self) -> Path:
        return Path(self.data_dir())

    def _index(self) -> LibraryIndex:
        return LibraryIndex(self._data() / sync.INDEX)

    def refresh(self, text: str = "") -> None:
        data = self._data()
        folder = sync.folder_setting(data)
        self.folder_label.setText(f"Library folder: {folder}" if folder else "Library folder: none chosen yet")
        for b in (self.earlier, self.reload):
            b.setEnabled(folder is not None)
        self.auto.blockSignals(True)
        self.auto.setChecked(sync.auto_setting(data))
        self.auto.blockSignals(False)
        waiting = len(store.waiting(data / sync.OUTBOX))
        try:
            when = self._index().info().get("refreshed_at")
        except Exception:
            when = None
        line = f"Waiting on this PC: {waiting}." + (f" Library last read: {when.replace('T', ' ')}." if when else "")
        self.status.setText((text + "\n" if text else "") + line)
        self.load()

    def _save_auto(self, on: bool) -> None:
        from app.paths import save_setting
        save_setting(self._data(), sync.AUTO_KEY, bool(on))

    def choose_folder(self) -> None:
        data = self._data()
        chosen = self.pick(str(sync.folder_setting(data) or Path.home()))
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
            text = sync.send_waiting(self._data())
        except Exception as e:
            logger.exception("Results library: send failed")
            text = f"Not sent: {e}"
        self.refresh(text)

    def add_earlier(self) -> None:
        w = self.window
        try:
            text = sync.add_earlier(w.db, self._data(), getattr(w.automation_panel, "output_root", None))
        except Exception as e:
            logger.exception("Results library: earlier sessions not added")
            text = f"Not added: {e}"
        self.refresh(text)

    # ── the library by part ──
    def load(self) -> None:
        try:
            index = self._index()
            self.results = index.results(include_deleted=True)
            self.names = index.names()
        except Exception:
            logger.exception("Results library: index not read")
            self.results, self.names = [], {}
        current = self._current_key()
        shown = self.results if self.show_deleted.isChecked() else [p for p in self.results if not p.get("deleted")]
        grouped = view.parts(shown, self.names)
        self.tree.blockSignals(True)
        self.tree.clear()
        pick = None
        for kind, title in view.KINDS:
            top = QTreeWidgetItem([title, str(sum(p["results"] for p in grouped[kind]))])
            self.tree.addTopLevelItem(top)
            for part in grouped[kind]:
                count = str(part["results"]) + (f" (+{part['deleted']} deleted)" if part["deleted"] else "")
                item = QTreeWidgetItem([part["name"], count])
                item.setData(0, Qt.UserRole, part)
                top.addChild(item)
                if part["key"] == current:
                    pick = item
            top.setExpanded(True)
        self.tree.blockSignals(False)
        if pick is not None:
            self.tree.setCurrentItem(pick)
        self.show_part()

    def _current_part(self) -> Optional[dict]:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _current_key(self) -> Optional[str]:
        part = self._current_part()
        return part["key"] if part else None

    def show_part(self) -> None:
        part = self._current_part()
        self.table.setRowCount(0)
        if not part:
            self.part_title.setText("Choose a part on the left." if self.results else
                                    "The library has no results yet (or this PC has not read it: Refresh).")
            self._buttons()
            return
        self.part_title.setText(part["name"])
        self.rows = view.rows(self.results, part["key"], self.names)
        if not self.show_deleted.isChecked():
            self.rows = [r for r in self.rows if not r["deleted"]]
        for r in self.rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            notes = r["notes"]
            if r["deleted"]:
                notes = f"DELETED {r['deleted'].get('at', '')[:10]}: {r['deleted'].get('reason') or ''}" + (
                    f"; {notes}" if notes else "")
            for c, value in enumerate((r["test"], r["result"], r["accuracy"], r["software"], r["variant"], r["pc"],
                                       r["date"], notes)):
                cell = QTableWidgetItem(value)
                if r["deleted"]:
                    cell.setForeground(Qt.gray)
                self.table.setItem(i, c, cell)
        if self.rows and not self._fitted:              # fitted to the text once; then the owner's widths stay
            self.table.resizeColumnsToContents()
            self._fitted = True
        self._buttons()

    def _selected_row(self) -> Optional[dict]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.rows[rows[0].row()] if rows and getattr(self, "rows", None) else None

    def _buttons(self) -> None:
        part, row = self._current_part(), self._selected_row()
        has_folder = sync.folder_setting(self._data()) is not None
        self.name_btn.setEnabled(bool(part and part["kind"] == "system" and has_folder))
        self.delete_btn.setEnabled(bool(row and not row["deleted"] and has_folder))
        self.restore_btn.setEnabled(bool(row and row["deleted"] and has_folder))

    def _unit(self) -> str:
        hw = getattr(getattr(self.window, "environment_profile", None), "hardware", None) or {}
        return hw.get("unit_id") or "unknown-pc"

    def _event(self, action: str, reason: str = "") -> None:
        row = self._selected_row()
        folder = sync.folder_setting(self._data())
        if row is None or folder is None:
            return
        try:
            store.write_event(folder, row["package_id"], action, self._unit(), reason)
            self._index().refresh(folder)
            logger.info("Results library: %s %s (%s)", action, row["package_id"], reason)
        except (store.LibraryError, OSError) as e:
            self.refresh(f"Not done: {e}")
            return
        self.load()

    def delete(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        reason = self.ask_text("Delete result", f"Why is \"{row['test']}\" ({row['result']}) deleted? "
                                                "(e.g. retest after a driver update)")
        if reason is None:
            return
        self._event("delete", reason.strip())

    def restore(self) -> None:
        self._event("restore")

    def name_part(self) -> None:
        part = self._current_part()
        folder = sync.folder_setting(self._data())
        if not part or folder is None:
            return
        name = self.ask_text("Name this device", "Its name in the library (all PCs that read it see it). Leave it "
                                                 "empty to go back to the name read from the device.",
                             self.names.get(part["key"], ""))
        if name is None:
            return
        try:
            store.write_name(folder, part["key"], name, self._unit())
            self._index().refresh(folder)
        except (store.LibraryError, OSError) as e:
            self.refresh(f"Not named: {e}")
            return
        self.load()

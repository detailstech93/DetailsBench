"""Tests: every test this PC can run, grouped, with an installed mark, how it runs and a download link
for the ones that are missing. Ticking tests and pressing Run hands them to `run_requested`."""

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from app.catalog import ASSISTED, AUTOMATIC, GROUPS, INSTALLED, MISSING, TestEntry, counts

COLUMNS = ["Test", "Installed", "How it runs", "Verified", "Time", ""]
MODE_TEXT = {AUTOMATIC: "Automatic", ASSISTED: "Assisted (you start it)", "manual": "Manual (you type the score)"}
STATE_TEXT = {INSTALLED: "✔ installed", MISSING: "⬇ not installed", "unusable": "⚠ cannot run"}
STATE_COLOR = {INSTALLED: "#15803d", MISSING: "#757575", "unusable": "#B26A00"}


def state_text(entry: TestEntry) -> str:
    if not entry.compatible:
        return "✖ " + (entry.compatibility_reason or "not for this PC")
    return STATE_TEXT.get(entry.state, entry.state)


class TestsPage(QWidget):
    __test__ = False        # not a pytest class
    run_requested = Signal(list)      # [TestEntry], all can_run()
    rescan_requested = Signal()

    def __init__(self, open_url: Optional[Callable[[str], object]] = None, parent=None):
        super().__init__(parent)
        self.entries: List[TestEntry] = []
        self.items: Dict[str, QTreeWidgetItem] = {}
        self.open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))

        layout = QVBoxLayout(self)
        head = QLabel("Tests")
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        head.setFont(font)
        layout.addWidget(head)
        self.summary = QLabel("Looking for installed tests...")
        layout.addWidget(self.summary)

        bar = QHBoxLayout()
        self.all_btn = QPushButton("Select all installed")
        self.all_btn.clicked.connect(self.select_all_installed)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(lambda: self.set_all(False))
        self.rescan_btn = QPushButton("I installed something: check again")
        self.rescan_btn.clicked.connect(self.rescan_requested.emit)
        self.show_all = QCheckBox("Show tests this PC cannot run")
        self.show_all.toggled.connect(lambda _: self.fill())
        bar.addWidget(self.all_btn)
        bar.addWidget(self.clear_btn)
        bar.addWidget(self.rescan_btn)
        bar.addStretch(1)
        bar.addWidget(self.show_all)
        layout.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(lambda *_: self.update_run_button())
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree, 1)

        row = QHBoxLayout()
        self.hint = QLabel("Tick tests, or double-click one, then choose how many times to run them.")
        self.hint.setStyleSheet("color: #555555;")
        self.run_btn = QPushButton("Run selected...")
        self.run_btn.setMinimumHeight(40)
        self.run_btn.setStyleSheet("QPushButton { font-size: 14px; font-weight: bold; padding: 6px 24px; }")
        self.run_btn.clicked.connect(self.run_selected)
        row.addWidget(self.hint, 1)
        row.addWidget(self.run_btn)
        layout.addLayout(row)
        self.update_run_button()

    # ---- list ----------------------------------------------------------------
    def set_entries(self, entries: List[TestEntry]):
        keep = set(self.selected_ids())
        self.entries = list(entries)
        self.fill(keep)
        c = counts(self.entries)
        self.summary.setText(f"{c['compatible']} tests can run on this PC; {c['installed']} of them are installed."
                             + (f" {c['incompatible']} more are for other systems." if c["incompatible"] else ""))

    def visible_entries(self) -> List[TestEntry]:
        return [e for e in self.entries if e.compatible or self.show_all.isChecked()]

    def fill(self, keep=None):
        keep = set(self.selected_ids()) if keep is None else keep
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items.clear()
        names = dict(GROUPS)
        for group, title in GROUPS:
            members = [e for e in self.visible_entries() if e.group == group]
            if not members:
                continue
            top = QTreeWidgetItem([f"{names[group]} ({len(members)})"])
            top.setFlags(Qt.ItemIsEnabled)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            for e in members:
                item = QTreeWidgetItem([e.name, state_text(e), MODE_TEXT.get(e.mode, e.mode),
                                        "yes" if e.verified else "not yet",
                                        f"~{e.expected_minutes:g} min" if e.expected_minutes else "", ""])
                item.setData(0, Qt.UserRole, e.id)
                item.setToolTip(0, e.state_reason or "")
                item.setToolTip(1, e.compatibility_reason or e.state_reason or "")
                item.setForeground(1, self.palette().text() if not e.compatible else
                                   _brush(STATE_COLOR.get(e.state, "#757575")))
                if e.can_run():
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                    item.setCheckState(0, Qt.Checked if e.id in keep else Qt.Unchecked)
                else:
                    item.setFlags(Qt.ItemIsEnabled)
                top.addChild(item)
                self.items[e.id] = item
                button = self._action_button(e)
                if button is not None:
                    self.tree.setItemWidget(item, 5, button)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        self.update_run_button()

    def _action_button(self, e: TestEntry) -> Optional[QPushButton]:
        if e.can_run():
            b = QPushButton("Run...")
            b.clicked.connect(lambda checked=False, x=e: self.run_requested.emit([x]))
            return b
        if e.compatible and e.state == MISSING and e.official_url:
            b = QPushButton("Official download page")
            b.setToolTip(e.official_url)
            b.clicked.connect(lambda checked=False, url=e.official_url: self.open_url(url))
            return b
        return None

    # ---- selection -------------------------------------------------------------
    def entry(self, test_id: str) -> Optional[TestEntry]:
        return next((e for e in self.entries if e.id == test_id), None)

    def selected_ids(self) -> List[str]:
        return [tid for tid, item in self.items.items()
                if item.flags() & Qt.ItemIsUserCheckable and item.checkState(0) == Qt.Checked]

    def selected(self) -> List[TestEntry]:
        return [e for e in (self.entry(t) for t in self.selected_ids()) if e is not None and e.can_run()]

    def set_checked(self, test_id: str, checked: bool = True):
        item = self.items.get(test_id)
        if item is not None and item.flags() & Qt.ItemIsUserCheckable:
            item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def set_all(self, checked: bool):
        for tid in self.items:
            self.set_checked(tid, checked)

    def select_all_installed(self):
        for e in self.entries:
            if e.can_run():
                self.set_checked(e.id, True)

    def update_run_button(self):
        n = len(self.selected_ids())
        self.run_btn.setEnabled(n > 0)
        self.run_btn.setText(f"Run selected ({n})..." if n else "Run selected...")

    def run_selected(self):
        chosen = self.selected()
        if chosen:
            self.run_requested.emit(chosen)

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        e = self.entry(item.data(0, Qt.UserRole) or "")
        if e is None:
            return
        if e.can_run():
            self.run_requested.emit([e])
        elif e.compatible and e.state == MISSING and e.official_url:
            self.open_url(e.official_url)


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(color))

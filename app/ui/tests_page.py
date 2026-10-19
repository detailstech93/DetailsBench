"""Tests: every test this PC can run, grouped, with an installed mark, how it runs and a download link
for the ones that are missing. Ticking tests and pressing Run hands them to `run_requested`."""

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.catalog import ASSISTED, AUTOMATIC, GROUPS, INSTALLED, MISSING, TestEntry, counts

COLUMNS = ["Test", "Installed", "How it runs", "Verified", "Time", ""]
MODE_TEXT = {AUTOMATIC: "Automatic", ASSISTED: "Assisted (you start it)", "manual": "Manual (you type the score)"}
STATE_TEXT = {INSTALLED: "✔ installed", MISSING: "⬇ not installed", "unusable": "⚠ cannot run"}
STATE_COLOR = {INSTALLED: "#15803d", MISSING: "#757575", "unusable": "#B26A00"}


BAR_STYLE = ("QProgressBar { border: 1px solid #3f3f46; border-radius: 5px; text-align: center; height: 22px; "
             "font-weight: bold; } QProgressBar::chunk { background-color: #16a34a; border-radius: 4px; }")


@dataclass
class RunState:
    """What is running now, for the progress bar. total None = no run count (a game capture)."""
    running: bool = False
    done: int = 0
    total: Optional[int] = None
    text: str = ""
    fraction: float = 0.0           # of the run going on now (a stress test knows it from its minutes)


def progress_percent(state: RunState) -> Optional[int]:
    if not state.total:
        return None
    return max(0, min(100, round(100 * (state.done + state.fraction) / state.total)))


def elapsed_text(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def install_plan(entry: TestEntry, host=None) -> Optional[dict]:
    """How a missing test can be installed with one click on this OS and architecture (app/install/plans.py),
    or None: only its official download page."""
    from app.install.plans import plan_for
    ref = entry.ref if isinstance(entry.ref, dict) else {}
    return plan_for(entry.id, entry.source, ref.get("app_id", ref.get("appid")), host)


def installable(entry: TestEntry, host=None) -> bool:
    return install_plan(entry, host) is not None


def state_text(entry: TestEntry) -> str:
    if not entry.compatible:
        return "✖ " + (entry.compatibility_reason or "not for this PC")
    return STATE_TEXT.get(entry.state, entry.state)


class TestsPage(QWidget):
    __test__ = False        # not a pytest class
    run_requested = Signal(list)      # [TestEntry], all can_run()
    run_all_requested = Signal(list)  # every entry shown (app/ui/run_all.py offers the installs first)
    run_plan_requested = Signal()     # "Run a saved plan..." (app/experiment.py)
    ai_models_requested = Signal()    # "AI models..." (app/ui/ai_models_dialog.py)
    remote_requested = Signal()       # "Remote: ON/OFF..." (app/ui/remote_control.py)
    rescan_requested = Signal()
    install_requested = Signal(object)      # TestEntry: missing, with a one-click install (app/install)

    def __init__(self, open_url: Optional[Callable[[str], object]] = None, parent=None):
        super().__init__(parent)
        self.entries: List[TestEntry] = []
        self.items: Dict[str, QTreeWidgetItem] = {}
        self.open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.host = None            # app/install/plans.Host; None: this computer (tests set one)

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
        self.run_all_btn = QPushButton("Run all tests...")
        self.run_all_btn.setMinimumHeight(40)
        self.run_all_btn.setToolTip("Every test for this PC. Tests that are not installed are listed first: install "
                                    "them, then everything runs, or run only what is installed.")
        self.run_all_btn.clicked.connect(lambda: self.busy or self.run_all_requested.emit(list(self.entries)))
        self.run_plan_btn = QPushButton("Run a saved plan...")
        self.run_plan_btn.setMinimumHeight(40)
        self.run_plan_btn.setToolTip("Every run from this screen is saved as a plan (tests, runs, warm-ups, pause, "
                                     "game settings). Pick one to run the same again.")
        self.run_plan_btn.clicked.connect(lambda: self.busy or self.run_plan_requested.emit())
        self.ai_models_btn = QPushButton("AI models...")
        self.ai_models_btn.setMinimumHeight(40)
        self.ai_models_btn.setToolTip("The LM Studio models measured with fixed settings (llama-benchy): add your "
                                      "own model or remove it again.")
        self.ai_models_btn.clicked.connect(lambda: self.busy or self.ai_models_requested.emit())
        self.remote_btn = QPushButton("Remote: OFF...")
        self.remote_btn.setMinimumHeight(40)
        self.remote_btn.setToolTip("Start tests from a phone or another computer on the local network (off until "
                                   "you switch it on).")
        self.remote_btn.clicked.connect(self.remote_requested.emit)
        row.addWidget(self.hint, 1)
        row.addWidget(self.remote_btn)
        row.addWidget(self.ai_models_btn)
        row.addWidget(self.run_plan_btn)
        row.addWidget(self.run_all_btn)
        row.addWidget(self.run_btn)
        layout.addLayout(row)

        # progress while a test runs: green bar, percent, what is running, time, stop
        self.progress_box = QFrame()
        self.progress_box.setFrameShape(QFrame.StyledPanel)
        box = QVBoxLayout(self.progress_box)
        line = QHBoxLayout()
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.elapsed_label = QLabel("")
        line.addWidget(self.progress_label, 1)
        line.addWidget(self.elapsed_label)
        box.addLayout(line)
        bar_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(BAR_STYLE)
        self.progress_bar.setFormat("%p%")
        self.record_btn = QPushButton("Enter the score...")
        self.record_btn.setVisible(False)
        self.record_btn.clicked.connect(self._record)
        self.report_btn = QPushButton("Open report")
        self.report_btn.setVisible(False)
        self.report_btn.clicked.connect(lambda: self.report_fn and self.report_fn())
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setStyleSheet("QPushButton:enabled { color: white; background: #b91c1c; padding: 4px 18px; }")
        self.stop_btn.clicked.connect(self._stop)
        bar_row.addWidget(self.progress_bar, 1)
        bar_row.addWidget(self.record_btn)
        bar_row.addWidget(self.report_btn)
        bar_row.addWidget(self.stop_btn)
        box.addLayout(bar_row)
        self.progress_box.setVisible(False)
        layout.addWidget(self.progress_box)

        self.get_state: Callable[[], RunState] = RunState
        self.stop_fn: Callable[[], object] = lambda: None
        self.record_fn: Optional[Callable[[], object]] = None
        self.report_fn: Optional[Callable[[], object]] = None
        self.busy = False
        self.started_at: Optional[float] = None
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.refresh_progress)
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
            b.clicked.connect(lambda checked=False, x=e: self.request_run([x]))
            b.setEnabled(not self.busy)
            return b
        if e.compatible and e.state == MISSING and installable(e, self.host):
            b = QPushButton("Install")
            b.setToolTip("Download from the official source and install it (you are asked first)"
                         + (f"\nOfficial page: {e.official_url}" if e.official_url else ""))
            b.clicked.connect(lambda checked=False, x=e: self.request_install(x))
            b.setEnabled(not self.busy)
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
            if e.can_run() and not e.waits_for_typed_score():     # those only run alone
                self.set_checked(e.id, True)

    def update_run_button(self):
        n = len(self.selected_ids())
        self.run_btn.setEnabled(n > 0 and not self.busy)
        self.run_all_btn.setEnabled(bool(self.entries) and not self.busy)
        self.run_plan_btn.setEnabled(bool(self.entries) and not self.busy)
        self.run_btn.setText(f"Run selected ({n})..." if n else "Run selected...")

    def run_selected(self):
        chosen = self.selected()
        if chosen:
            self.request_run(chosen)

    def request_install(self, entry: TestEntry):
        if not self.busy:
            self.install_requested.emit(entry)

    def request_run(self, entries: List[TestEntry]):
        if not self.busy:           # one test (or plan) at a time
            self.run_requested.emit(entries)

    # ---- progress ------------------------------------------------------------------
    def watch(self, get_state: Callable[[], RunState], stop: Callable[[], object]):
        """Follow a run started from this screen until it ends."""
        self.get_state, self.stop_fn = get_state, stop
        self.report_fn = None
        self.report_btn.setVisible(False)
        self.started_at = time.monotonic()
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("Stop")
        self.progress_box.setVisible(True)
        self.set_busy(True)
        self.refresh_progress()
        self.timer.start()

    def show_remote(self, on: bool):
        """The Remote button says whether remote mode is on (green) or off."""
        self.remote_btn.setText("Remote: ON..." if on else "Remote: OFF...")
        self.remote_btn.setStyleSheet("QPushButton { background: #2e7d32; color: white; font-weight: bold; }"
                                      if on else "")

    def offer_record(self, text: str, record: Callable[[], object]):
        """A manual test was started: the bar waits, the score is typed in when it ends."""
        self.record_fn = record
        self.record_btn.setVisible(True)
        self.watch(lambda: RunState(True, 0, None, text), self._end_manual)

    def offer_report(self, open_report: Callable[[], object]):
        """The run's report is written: a button opens it again (the browser may have opened behind)."""
        self.report_fn = open_report
        self.report_btn.setVisible(True)

    def _end_manual(self):
        self.record_fn = None
        self.record_btn.setVisible(False)
        self.get_state = RunState

    def _record(self):
        if self.record_fn is not None:
            self.record_fn()
            self._end_manual()
            self.refresh_progress()

    def _stop(self):
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stopping...")
        self.stop_fn()

    def set_busy(self, busy: bool):
        self.busy = busy
        for widget in (self.all_btn, self.clear_btn, self.rescan_btn):
            widget.setEnabled(not busy)
        for item in self.items.values():
            button = self.tree.itemWidget(item, 5)
            if isinstance(button, QPushButton) and button.text() in ("Run...", "Install"):
                button.setEnabled(not busy)
        self.tree.setEnabled(not busy)
        self.update_run_button()

    def refresh_progress(self):
        state = self.get_state()
        if self.started_at is not None:
            self.elapsed_label.setText(elapsed_text(time.monotonic() - self.started_at))
        if state.running:
            percent = progress_percent(state)
            if percent is None:
                self.progress_bar.setRange(0, 0)          # moving bar: no run count to show
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(percent)
            runs = f"Run {min(state.done + 1, state.total)} of {state.total}: " if state.total else ""
            self.progress_label.setText(runs + state.text)
            return
        self.timer.stop()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if state.total and state.done >= state.total else self.progress_bar.value())
        self.progress_label.setText(state.text or "Finished.")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stop")
        self.set_busy(False)

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        e = self.entry(item.data(0, Qt.UserRole) or "")
        if e is None:
            return
        if e.can_run():
            self.request_run([e])
        elif e.compatible and e.state == MISSING and e.official_url:
            self.open_url(e.official_url)


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(color))

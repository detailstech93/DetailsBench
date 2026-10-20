"""Tests: every test this PC can run, grouped, with an installed mark, how it runs and a download link
for the ones that are missing. Ticking tests and pressing Run hands them to `run_requested`."""

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.catalog import ASSISTED, AUTOMATIC, GROUPS, INSTALLED, MISSING, TYPED_GROUP, TestEntry, counts

SCANNING_TEXT = "Scanning this PC for tests and programs... Tests can start when the scan ends."
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
    share: Optional[float] = None   # a download: the part done (0..1), with no runs


def progress_percent(state: RunState) -> Optional[int]:
    if state.share is not None and not state.total:
        return max(0, min(100, round(100 * state.share)))
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


LMSTUDIO_URL = "https://lmstudio.ai/download"


def needs_lmstudio(entry: TestEntry, host=None, lms=None) -> bool:
    """A fixed AI model downloads through LM Studio's lms tool: without LM Studio there is nothing to install it
    with (owner, 2026-10-20: "Install selected" said so for three models). LM Studio itself is installed by hand."""
    plan = install_plan(entry, host)
    if not plan or plan["kind"] != "lmstudio":
        return False
    if lms is None:
        from app.ai.lmstudio import lms_path as lms
    return lms() is None


def installable(entry: TestEntry, host=None, lms=None) -> bool:
    return install_plan(entry, host) is not None and not needs_lmstudio(entry, host, lms)


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
    reports_folder_requested = Signal()   # "Reports folder..." (app/ui/reports_folder.py)
    open_reports_requested = Signal()     # "Open reports": the folder itself, in the file manager
    admin_requested = Signal()        # "Restart as administrator" (app/ui/admin_restart.py)
    rescan_requested = Signal()
    install_requested = Signal(object)      # TestEntry: missing, with a one-click install (app/install)
    install_many_requested = Signal(list)   # the ticked missing tests: installed one after the other (app/ui/run_all)

    def __init__(self, open_url: Optional[Callable[[str], object]] = None, parent=None):
        super().__init__(parent)
        self.entries: List[TestEntry] = []
        self.items: Dict[str, QTreeWidgetItem] = {}
        from app.ui.links import open_link
        self.open_url = open_url or open_link
        self.host = None            # app/install/plans.Host; None: this computer (tests set one)
        # owner, 2026-10-20: games have their own section (Games) and never run together with the tests
        self.section = "tests"

        layout = QVBoxLayout(self)
        self.head = head = QLabel("Tests")
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
        self.missing_btn = QPushButton("Select all not installed")
        self.missing_btn.setToolTip("Ticks every test that is not installed and that the app can install.")
        self.missing_btn.clicked.connect(self.select_all_missing)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(lambda: self.set_all(False))
        # owner, 2026-10-20: tick several tests that are not installed and install them all with one button
        self.install_btn = QPushButton("Install selected...")
        self.install_btn.setToolTip("Installs the ticked tests that are not installed, one after the other, each "
                                    "from its official source (you are asked first). Windows: the app can start "
                                    "again as administrator first, so Windows asks only once.")
        self.install_btn.setStyleSheet("QPushButton:enabled { color: white; background: #1d4ed8; padding: 4px 14px; "
                                       "font-weight: bold; }")
        self.install_btn.clicked.connect(self.install_selected)
        self.rescan_btn = QPushButton("I installed something: check again")
        self.rescan_btn.clicked.connect(self.rescan_requested.emit)
        self.show_all = QCheckBox("Show tests this PC cannot run")
        self.show_all.toggled.connect(lambda _: self.fill())
        bar.addWidget(self.all_btn)
        bar.addWidget(self.missing_btn)
        bar.addWidget(self.clear_btn)
        bar.addWidget(self.install_btn)
        bar.addWidget(self.rescan_btn)
        bar.addStretch(1)
        bar.addWidget(self.show_all)
        layout.addLayout(bar)

        # owner, 2026-10-20: while the app scans the PC for tests nothing starts, and the screen says so
        self.scan_banner = QLabel(SCANNING_TEXT)
        self.scan_banner.setWordWrap(True)
        self.scan_banner.setStyleSheet("QLabel { background: #fef3c7; color: #92400e; border: 1px solid #f59e0b; "
                                       "border-radius: 4px; padding: 8px; font-weight: bold; }")
        self.scan_banner.setVisible(False)
        layout.addWidget(self.scan_banner)

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
        self.run_all_btn.clicked.connect(lambda: self.blocked() or self.run_all_requested.emit(self.section_entries()))
        self.run_plan_btn = QPushButton("Run a saved plan...")
        self.run_plan_btn.setMinimumHeight(40)
        self.run_plan_btn.setToolTip("Every run from this screen is saved as a plan (tests, runs, warm-ups, pause, "
                                     "game settings). Pick one to run the same again.")
        self.run_plan_btn.clicked.connect(lambda: self.blocked() or self.run_plan_requested.emit())
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
        self.reports_btn = QPushButton("Reports folder...")
        self.reports_btn.setMinimumHeight(40)
        self.reports_btn.setToolTip("The folder every report is saved in (asked the first time a test runs).")
        self.reports_btn.clicked.connect(self.reports_folder_requested.emit)
        self.open_reports_btn = QPushButton("Open reports")
        self.open_reports_btn.setMinimumHeight(40)
        self.open_reports_btn.setToolTip("Open the folder the reports are saved in.")
        self.open_reports_btn.clicked.connect(self.open_reports_requested.emit)
        self.admin_btn = QPushButton("Restart as administrator")
        self.admin_btn.setMinimumHeight(40)
        self.admin_btn.setToolTip("Windows asks once, here at the PC; then PassMark and game benchmarks start "
                                  "without a prompt, also from the phone (until the app closes).")
        self.admin_btn.clicked.connect(self.admin_requested.emit)
        self.admin_btn.setVisible(False)
        row.addWidget(self.hint, 1)
        row.addWidget(self.admin_btn)
        row.addWidget(self.open_reports_btn)
        row.addWidget(self.reports_btn)
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
        self.skip_btn = QPushButton("Skip this test")
        self.skip_btn.setToolTip("Ends only the test going on (the report says how far it got); "
                                 "the other tests go on.")
        self.skip_btn.setVisible(False)
        self.skip_btn.clicked.connect(self.skip)
        bar_row.addWidget(self.progress_bar, 1)
        bar_row.addWidget(self.record_btn)
        bar_row.addWidget(self.report_btn)
        bar_row.addWidget(self.skip_btn)
        bar_row.addWidget(self.stop_btn)
        box.addLayout(bar_row)
        self.progress_box.setVisible(False)
        layout.addWidget(self.progress_box)

        self.get_state: Callable[[], RunState] = RunState
        self.stop_fn: Callable[[], object] = lambda: None
        self.skip_fn: Optional[Callable[[], object]] = None     # a plan of several tests: skip the one going on
        self.record_fn: Optional[Callable[[], object]] = None
        self.report_fn: Optional[Callable[[], object]] = None
        self.busy = False
        self.scanning = False
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
        self._summary()

    def _summary(self):
        c = counts(self.section_entries())
        what = "games" if self.section == "games" else "tests"
        self.summary.setText(f"{c['compatible']} {what} can run on this PC; {c['installed']} of them are installed."
                             + (f" {c['incompatible']} more are for other systems." if c["incompatible"] else ""))

    def set_section(self, section: str):
        """Tests (everything but games) or Games: what the list shows; a selection never mixes the two."""
        section = "games" if section == "games" else "tests"
        if section == self.section:
            return
        self.set_all(False)
        self.section = section
        games = section == "games"
        self.head.setText("Games" if games else "Tests")
        self.run_all_btn.setText("Run all games..." if games else "Run all tests...")
        self.ai_models_btn.setVisible(not games)
        self.hint.setText("Tick games, or double-click one, then choose the preset and how many times to run them."
                          if games else "Tick tests, or double-click one, then choose how many times to run them.")
        self.fill(set())
        self._summary()

    def section_entries(self) -> List[TestEntry]:
        games = self.section == "games"
        return [e for e in self.entries if (e.group == "game") == games]

    def visible_entries(self) -> List[TestEntry]:
        return [e for e in self.section_entries() if e.compatible or self.show_all.isChecked()]

    def fill(self, keep=None):
        keep = set(self.selected_ids()) if keep is None else keep
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items.clear()
        names = dict(GROUPS)
        visible = self.visible_entries()
        typed = [e for e in visible if e.waits_for_typed_score()]
        groups = [(names[g], [e for e in visible if e.group == g and not e.waits_for_typed_score()])
                  for g, _ in GROUPS]
        # owner, 2026-10-20: tests whose score is typed are kept apart and never ticked into a run of several
        for title, members in groups + [(TYPED_GROUP, typed)]:
            if not members:
                continue
            top = QTreeWidgetItem([f"{title} ({len(members)})"])
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
                if (e.can_run() and not e.waits_for_typed_score()) or self.can_install(e):
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
            b = QPushButton("Run alone..." if e.waits_for_typed_score() else "Run...")
            b.clicked.connect(lambda checked=False, x=e: self.request_run([x]))
            b.setEnabled(not self.blocked())
            return b
        if e.compatible and e.state == MISSING and installable(e, self.host):
            b = QPushButton("Install")
            b.setToolTip("Download from the official source and install it (you are asked first)"
                         + (f"\nOfficial page: {e.official_url}" if e.official_url else ""))
            b.clicked.connect(lambda checked=False, x=e: self.request_install(x))
            b.setEnabled(not self.busy)
            return b
        if e.compatible and e.state == MISSING and needs_lmstudio(e, self.host):
            b = QPushButton("Get LM Studio first")
            b.setToolTip("This model downloads through LM Studio, which is not installed here. Install LM Studio "
                         f"from its official page ({LMSTUDIO_URL}), open it once, then Rescan.")
            b.clicked.connect(lambda checked=False: self.open_url(LMSTUDIO_URL))
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

    def can_install(self, e: TestEntry) -> bool:
        """Missing and installable with one click here; Steam games are not (Steam's own dialog, often tens of GB)."""
        return e.compatible and e.state == MISSING and e.source != "steam" and installable(e, self.host)

    def selected_to_install(self) -> List[TestEntry]:
        return [e for e in (self.entry(t) for t in self.selected_ids()) if e is not None and self.can_install(e)]

    def select_all_missing(self):
        for e in self.section_entries():
            if self.can_install(e):
                self.set_checked(e.id, True)

    def install_selected(self):
        chosen = self.selected_to_install()
        if chosen and not self.blocked():
            self.install_many_requested.emit(chosen)

    def set_checked(self, test_id: str, checked: bool = True):
        item = self.items.get(test_id)
        if item is not None and item.flags() & Qt.ItemIsUserCheckable:
            item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def set_all(self, checked: bool):
        for tid in self.items:
            self.set_checked(tid, checked)

    def select_all_installed(self):
        for e in self.section_entries():
            if e.can_run() and not e.waits_for_typed_score():     # those only run alone
                self.set_checked(e.id, True)

    def update_run_button(self):
        n = len(self.selected())
        k = len(self.selected_to_install())
        self.install_btn.setEnabled(k > 0 and not self.blocked())
        self.install_btn.setText(f"Install selected ({k})..." if k else "Install selected...")
        self.run_btn.setEnabled(n > 0 and not self.blocked())
        self.run_all_btn.setEnabled(bool(self.section_entries()) and not self.blocked())
        self.run_plan_btn.setEnabled(bool(self.entries) and not self.blocked())
        self.run_btn.setText(f"Run selected ({n})..." if n else "Run selected...")

    def run_selected(self):
        chosen = self.selected()
        if chosen:
            self.request_run(chosen)

    def request_install(self, entry: TestEntry):
        if not self.busy:
            self.install_requested.emit(entry)

    def request_run(self, entries: List[TestEntry]):
        if not self.blocked():      # one test (or plan) at a time, never while the scan goes on
            self.run_requested.emit(entries)

    def blocked(self) -> bool:
        """A run is going on, or the app is scanning the PC for tests (nothing starts then)."""
        return self.busy or self.scanning

    def set_scanning(self, scanning: bool):
        self.scanning = scanning
        self.scan_banner.setVisible(scanning)
        self.rescan_btn.setEnabled(not scanning and not self.busy)
        self._sync_run_buttons()

    # ---- progress ------------------------------------------------------------------
    def watch(self, get_state: Callable[[], RunState], stop: Callable[[], object],
              skip: Optional[Callable[[], object]] = None):
        """Follow a run started from this screen until it ends (skip: the Skip button of a plan of several tests)."""
        self.get_state, self.stop_fn, self.skip_fn = get_state, stop, skip
        self.skip_btn.setVisible(skip is not None)
        self.skip_btn.setEnabled(True)
        self.report_fn = None
        self.report_btn.setVisible(False)
        self.started_at = time.monotonic()
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("Stop")
        self.progress_box.setVisible(True)
        self.set_busy(True)
        self.refresh_progress()
        self.timer.start()

    def show_admin(self, platform: str, elevated: bool):
        """Windows only: the restart button, or a plain "Administrator" when the app already runs so."""
        self.admin_btn.setVisible(platform == "win32")
        self.admin_btn.setEnabled(not elevated)
        self.admin_btn.setText("Administrator" if elevated else "Restart as administrator")
        if elevated:
            self.admin_btn.setToolTip("The app runs as administrator: PassMark and game benchmarks start without "
                                      "a prompt (until the app closes).")

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
        self.skip_btn.setEnabled(False)
        self.stop_btn.setText("Stopping...")
        self.stop_fn()

    def can_skip(self) -> bool:
        return self.busy and self.skip_fn is not None and self.skip_btn.isEnabled()

    def skip(self) -> bool:
        """Skip the test going on (the Skip button or the remote page). False: nothing to skip."""
        if not self.can_skip():
            return False
        self.skip_fn()
        return True

    def set_busy(self, busy: bool):
        self.busy = busy
        for widget in (self.all_btn, self.missing_btn, self.clear_btn, self.rescan_btn):
            widget.setEnabled(not busy)
        self.rescan_btn.setEnabled(not busy and not self.scanning)
        for item in self.items.values():
            button = self.tree.itemWidget(item, 5)
            if isinstance(button, QPushButton) and button.text() == "Install":
                button.setEnabled(not busy)
        self.tree.setEnabled(not busy)
        self._sync_run_buttons()

    def _sync_run_buttons(self):
        for item in self.items.values():
            button = self.tree.itemWidget(item, 5)
            if isinstance(button, QPushButton) and button.text().startswith("Run"):
                button.setEnabled(not self.blocked())
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
        self.skip_btn.setVisible(False)
        self.skip_fn = None
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

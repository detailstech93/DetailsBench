"""Tests: every test this PC can run, grouped, with an installed mark, how it runs and a download link
for the ones that are missing. Ticking tests and pressing Run hands them to `run_requested`."""

import re
import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.catalog import ASSISTED, AUTOMATIC, GROUPS, INSTALLED, MISSING, TYPED_GROUP, TestEntry, counts

SCANNING_TEXT = "Scanning this PC for tests and programs... Tests can start when the scan ends."
COLUMNS = ["Test", "Installed", "How it runs", "Verified", "Time", ""]
MODE_TEXT = {AUTOMATIC: "Automatic", ASSISTED: "Assisted (you start it)", "manual": "Manual (you type the score)"}
STATE_TEXT = {INSTALLED: "✔ installed", MISSING: "⬇ not installed", "unusable": "⚠ cannot run"}
STATE_COLOR = {INSTALLED: "#15803d", MISSING: "#757575", "unusable": "#B26A00"}
EMULATED_COLOR = "#8E24AA"      # the name of a test that runs emulated (owner, 2026-10-23)
EMULATED_TIP = "Runs emulated: there is no build of it for this computer's processor, Windows runs another one"


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
    marks: Optional[Dict[str, str]] = None  # a plan: test id -> running / ok / failed / skipped (the dots)


def with_install_dot(state: RunState, test_id: Optional[str]) -> RunState:
    """A blue blinking dot by the test whose app is downloading or installing now (owner, 2026-10-22)."""
    return replace(state, marks={test_id: "installing"} if state.running and test_id else {})


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


def text_share(text: str) -> Optional[float]:
    """The part done that a step's text ends with ("Downloading Blender 5.2.0... 70 %"), or None."""
    found = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%\s*$", text or "")
    return min(1.0, float(found.group(1)) / 100) if found else None


def program_key(plan: dict, test_id: str) -> tuple:
    """What an install puts on the computer: tests that share a program (PassMark's marks, Cinebench's two runs,
    HandBrake's two encoders, Blender CPU and GPU) have the same key."""
    return (plan["kind"], plan.get("winget") or plan.get("folder") or plan.get("package") or plan.get("test_id")
            or test_id)


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


def lmstudio_one_click(host=None) -> bool:
    """Linux and Windows, x64/arm64: LM Studio's official install.sh / install.ps1 (app/ai/lmstudio_linux.py)."""
    from app.install.plans import this_host
    host = host or this_host()
    return host.os in ("linux", "windows") and host.arch in ("x64", "arm64")


def installable(entry: TestEntry, host=None, lms=None) -> bool:
    return install_plan(entry, host) is not None and not needs_lmstudio(entry, host, lms)


def state_text(entry: TestEntry) -> str:
    if not entry.compatible:
        return "✖ " + (entry.compatibility_reason or "not for this PC")
    return STATE_TEXT.get(entry.state, entry.state)


SKIP_TIPS = {"test": "Ends only the test going on (the report says how far it got); the other tests go on.",
             "download": "Ends only the download going on; the next one in line starts."}


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
    lmstudio_install_requested = Signal()   # Linux: LM Studio itself, for the AI models
    queue_requested = Signal(object)        # TestEntry: Install pressed during an install (it waits in line)
    queue_run_requested = Signal(list)      # [TestEntry]: "Queue next" while tests run (added to that run)
    install_many_requested = Signal(list)   # the ticked missing tests: installed one after the other (app/ui/run_all)

    def __init__(self, open_url: Optional[Callable[[str], object]] = None, parent=None):
        super().__init__(parent)
        self.entries: List[TestEntry] = []
        self.items: Dict[str, QTreeWidgetItem] = {}
        self.marks: Dict[str, str] = {}      # the dots of the last plan (show_marks)
        self.scrolled_to: Optional[str] = None   # the running test the list last scrolled to
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
        self.hint.setStyleSheet("color: palette(placeholder-text);")
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
        self.skip_what = "test"             # "download" during installs (owner, 2026-10-22)
        self.skip_btn.setToolTip(SKIP_TIPS["test"])
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
        # during an install, Install still works for downloads that can wait in line (owner, 2026-10-22); the window
        # sets this: can_queue(None) = an install is going on that takes more, can_queue(entry) = this one can wait
        self.can_queue: Callable[..., bool] = lambda entry=None: False
        self.can_add: Callable[[], bool] = lambda: False        # the window: a run that takes queued tests
        self.adding = False     # each test's own Run button says "Queue next" (owner, 2026-10-24; not Run selected)
        self.taking = False
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
        groups: Dict[tuple, List[str]] = {}
        for e in self.entries:              # tests of one program show its line and its dot together (Fedora,
            plan = install_plan(e, self.host) if e.state == MISSING and e.source != "steam" else None
            if plan:                        # 2026-10-22: only Blender CPU said "In line", Blender GPU still "Install")
                groups.setdefault(program_key(plan, e.id), []).append(e.id)
        self.siblings = {tid: ids for ids in groups.values() for tid in ids}
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
                if e.emulated:
                    item.setForeground(0, _brush(EMULATED_COLOR))
                    item.setToolTip(0, EMULATED_TIP + (f"\n{e.state_reason}" if e.state_reason else ""))
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
        self.show_marks(self.marks)
        self.update_run_button()

    def _action_button(self, e: TestEntry) -> Optional[QPushButton]:
        if e.can_run():
            b = QPushButton("Run alone..." if e.waits_for_typed_score() else "Run...")
            b.setProperty("run_text", b.text())
            b.clicked.connect(lambda checked=False, x=e: self.request_run([x]))
            b.setEnabled(not self.blocked())
            return b
        if e.compatible and e.state == MISSING and installable(e, self.host):
            b = QPushButton("Install")
            b.setToolTip("Download from the official source and install it (you are asked first)"
                         + (f"\nOfficial page: {e.official_url}" if e.official_url else ""))
            b.clicked.connect(lambda checked=False, x=e: self.request_install(x))
            b.setEnabled(not self.busy or self.can_queue(e))
            return b
        if e.compatible and e.state == MISSING and needs_lmstudio(e, self.host) and lmstudio_one_click(self.host):
            b = QPushButton("Install LM Studio")
            b.setToolTip("This model downloads through LM Studio, which is not installed here. Installs LM Studio's "
                         "server (llmster) with its official install script (you are asked first).")
            b.clicked.connect(lambda checked=False: self.lmstudio_install_requested.emit())
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
        return (e.compatible and e.state == MISSING and e.source != "steam"
                and (installable(e, self.host) or (needs_lmstudio(e, self.host) and lmstudio_one_click(self.host))))

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
        self.adding = self.busy and self.can_add()
        self.run_btn.setEnabled(n > 0 and not self.blocked())
        self.run_all_btn.setEnabled(bool(self.section_entries()) and not self.blocked())
        self.run_plan_btn.setEnabled(bool(self.entries) and not self.blocked())
        self.run_btn.setText(f"Run selected ({n})..." if n else "Run selected...")

    def queueable(self, e: TestEntry) -> bool:
        """Queue next takes an automatic test not in the run going on (games and typed-score tests run alone)."""
        return (e.can_run() and e.mode == AUTOMATIC and e.source == "workload" and not e.waits_for_typed_score()
                and e.id not in self.marks)

    def run_selected(self):
        chosen = self.selected()
        if chosen:
            self.request_run(chosen)

    def request_install(self, entry: TestEntry):
        if not self.busy:
            self.install_requested.emit(entry)
        elif self.can_queue(entry):
            self.queue_requested.emit(entry)

    def show_queue(self, ids: List[str]):
        """The downloads waiting in line: their Install button says "In line (n)" and is greyed out (with every
        other test of the same program)."""
        place = {}
        for n, test_id in enumerate(ids, 1):
            for other in getattr(self, "siblings", {}).get(test_id, [test_id]):
                place.setdefault(other, n)
        for test_id, item in self.items.items():
            button = self.tree.itemWidget(item, 5)
            if not isinstance(button, QPushButton) or not (button.text() == "Install"
                                                           or button.text().startswith("In line")):
                continue
            if test_id in place:
                button.setText(f"In line ({place[test_id]})")
                button.setEnabled(False)
            else:
                button.setText("Install")
                entry = self.entry(test_id)
                button.setEnabled(not self.busy or (entry is not None and self.can_queue(entry)))

    def request_run(self, entries: List[TestEntry]):
        if self.busy and self.can_add():        # tests are running: Run queues these into that run
            self.queue_run_requested.emit(list(entries))
        elif not self.blocked():    # one test (or plan) at a time, never while the scan goes on
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
              skip: Optional[Callable[[], object]] = None, skip_what: str = "test"):
        """Follow a run started from this screen until it ends (skip: the Skip button of a plan of several tests,
        or of downloads in line: skip_what "download" names it so)."""
        self.get_state, self.stop_fn, self.skip_fn = get_state, stop, skip
        self.skip_what = skip_what
        self.skip_btn.setText(f"Skip this {skip_what}")
        self.skip_btn.setToolTip(SKIP_TIPS[skip_what])
        self.skip_btn.setVisible(skip is not None)
        self.skip_btn.setEnabled(True)
        self.report_fn = None
        self.report_btn.setVisible(False)
        self.started_at = time.monotonic()
        self.marks = {}
        self.scrolled_to = None
        self.show_marks({})
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

    def allow_skip(self, skip: Callable[[], object]):
        """A test was queued into a run of one test: it now has a test to skip to."""
        if self.busy and self.skip_fn is None:
            self.skip_fn = skip
            self.skip_btn.setVisible(True)
            self.skip_btn.setEnabled(self.stop_btn.isEnabled())

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
        line = busy and self.can_queue()            # an install that takes more downloads in line
        for test_id, item in self.items.items():
            button = self.tree.itemWidget(item, 5)
            if isinstance(button, QPushButton) and not busy and (button.text().startswith("In line")
                                                                 or button.text() == "Installing..."):
                button.setText("Install")
            if isinstance(button, QPushButton) and button.text() == "Install":
                entry = self.entry(test_id)
                button.setEnabled(not busy or (line and entry is not None and self.can_queue(entry)))
        self.taking = busy and self.can_add()       # what the list was set up for (refresh_progress follows it)
        self.tree.setEnabled(not busy or line or self.taking)      # its Queue next buttons must be clickable
        self._sync_run_buttons()

    def _sync_run_buttons(self):
        self.update_run_button()
        for test_id, item in self.items.items():
            button = self.tree.itemWidget(item, 5)
            if not isinstance(button, QPushButton) or not button.property("run_text"):
                continue
            entry = self.entry(test_id)
            queue = self.adding and entry is not None and self.queueable(entry)
            button.setText("Queue next" if queue else button.property("run_text"))
            button.setEnabled(queue or not self.blocked())
            button.setToolTip("Adds this test to the run going on: it runs after its tests, with its numbers of "
                              "runs, in the same report." if queue else "")

    def show_marks(self, marks: Optional[Dict[str, str]]):
        """A dot by each test of a plan (owner, 2026-10-22): green blinking while it runs, green when it has a score,
        red when it failed, grey when skipped; the others have none. Kept after the run until the next one."""
        if marks is None:
            return
        self.marks = dict(marks)
        for test_id, mark in list(marks.items()):
            if mark == "installing":
                for other in getattr(self, "siblings", {}).get(test_id, []):
                    self.marks.setdefault(other, mark)
        lit = int(time.monotonic() * 2) % 2 == 0
        for test_id, item in self.items.items():
            mark = self.marks.get(test_id)
            blinks = mark in ("running", "installing")
            button = self.tree.itemWidget(item, 5)
            if isinstance(button, QPushButton) and self.busy:      # its program is downloading: no second Install
                if mark == "installing" and button.text() == "Install":
                    button.setText("Installing...")
                    button.setEnabled(False)
                elif mark != "installing" and button.text() == "Installing...":
                    button.setText("Install")
                    entry = self.entry(test_id)
                    button.setEnabled(entry is not None and self.can_queue(entry))
            color = MARK_COLOR.get(mark) if not blinks or lit else None
            item.setIcon(0, _dot(color) if color else _dot(None) if blinks else QIcon())
            if mark:
                item.setToolTip(0, MARK_TEXT[mark])
            if blinks and test_id != self.scrolled_to:
                self.tree.scrollToItem(item)       # follow the run once per test; the owner can still scroll away
                self.scrolled_to = test_id

    def refresh_progress(self):
        state = self.get_state()
        self.show_marks(state.marks)
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
            if self.can_add() != self.taking:
                self.set_busy(True)             # the plan has started (or ended): ticking and Queue next follow
            elif self.adding:
                self._sync_run_buttons()        # a test reached by the run (or queued) is not queued again
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


MARK_COLOR = {"running": "#2e7d32", "installing": "#1e88e5", "ok": "#2e7d32", "failed": "#c62828", "skipped": "#9e9e9e",
              "queued": "#f9a825"}
MARK_TEXT = {"running": "Running now", "installing": "Downloading or installing now", "ok": "Done: it has a score", "failed": "Failed: no score (see the report)",
             "skipped": "Skipped", "queued": "In line: runs after the tests before it"}
_DOTS: Dict[Optional[str], QIcon] = {}


def _dot(color: Optional[str]) -> QIcon:
    """A round dot (None: an empty one of the same size, so the name does not jump while it blinks)."""
    if color not in _DOTS:
        from PySide6.QtGui import QColor, QPainter, QPixmap
        pix = QPixmap(12, 12)
        pix.fill(Qt.transparent)
        if color:
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(1, 1, 10, 10)
            painter.end()
        icon = QIcon(pix)
        icon.addPixmap(pix, QIcon.Disabled)     # the rows are locked during a run; keep the colour, not grey
        _DOTS[color] = icon
    return _DOTS[color]


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(color))

"""Dialog for recording one manual run result (always stored with is_manual=1)."""

import math
from typing import Optional, Tuple

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)

from app.database import RESULT_STATUSES


def parse_score(status: str, text: str) -> Tuple[Optional[float], Optional[str]]:
    """Return (score, error). Completed runs need a finite number >= 0;
    any other status must have no score (failures are never stored as numbers)."""
    text = (text or "").strip().replace(",", ".")
    if status != "completed":
        return (None, None) if not text else (None, f"a {status} run must not have a score")
    if not text:
        return None, "enter the score shown by the benchmark"
    try:
        value = float(text)
    except ValueError:
        return None, f"not a number: {text}"
    if not math.isfinite(value) or value < 0:
        return None, "the score must be a finite number of 0 or more"
    return value, None


class RecordResultDialog(QDialog):
    """Ask for run type, status, score, unit and note of one run."""

    def __init__(self, benchmark_label: str, session_label: str, default_unit: str = "points", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Record result: {benchmark_label}")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Session: {session_label}\nRecorded as a MANUAL entry."))
        form = QFormLayout()
        self.run_type = QComboBox()
        self.run_type.addItems(["measured", "warmup"])
        self.status = QComboBox()
        self.status.addItems(list(RESULT_STATUSES))
        self.score = QLineEdit()
        self.score.setPlaceholderText("score exactly as displayed")
        self.unit = QLineEdit(default_unit)
        self.note = QLineEdit()
        form.addRow("Run type:", self.run_type)
        form.addRow("Status:", self.status)
        form.addRow("Score:", self.score)
        form.addRow("Unit:", self.unit)
        form.addRow("Note:", self.note)
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #C62828;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.status.currentTextChanged.connect(self._sync_score_enabled)
        self.values: Optional[dict] = None

    def _sync_score_enabled(self, status: str):
        self.score.setEnabled(status == "completed")
        if status != "completed":
            self.score.clear()

    def _try_accept(self):
        status = self.status.currentText()
        score, err = parse_score(status, self.score.text())
        if err:
            self.error.setText(err)
            return
        self.values = {
            "status": status, "score": score, "is_warmup": self.run_type.currentText() == "warmup",
            "unit": self.unit.text().strip() or "points", "note": self.note.text().strip() or None,
        }
        self.accept()


class PugetResultDialog(RecordResultDialog):
    """PugetBench for Creators: which application ran, its Overall Score and what ran (versions, preset)."""

    def __init__(self, session_label: str, puget_version=None, parent=None):
        from app.pro import pugetbench
        super().__init__("PugetBench for Creators", session_label, "points", parent)
        self.puget_version = puget_version
        self.application = QComboBox()
        self.application.addItems([app for _, app, _ in pugetbench.TESTS])
        self.app_version = QLineEdit()
        self.app_version.setPlaceholderText("e.g. Photoshop 27.0 (shown on PugetBench's results)")
        self.preset = QLineEdit()
        self.preset.setPlaceholderText("e.g. Standard")
        self.score.setPlaceholderText("Overall Score exactly as displayed")
        form = self.findChild(QFormLayout)
        form.insertRow(0, "Application:", self.application)
        form.addRow("Application version:", self.app_version)
        form.addRow("Preset:", self.preset)

    def _try_accept(self):
        from app.pro import pugetbench
        super()._try_accept()
        if self.values is None:
            return
        app = self.application.currentText()
        self.values["benchmark_name"] = pugetbench.test_for(app)
        self.values["display_name"] = f"PugetBench {app}"
        self.values["note"] = pugetbench.note_text(self.puget_version, self.app_version.text(), self.preset.text(),
                                                   self.values["note"] or "")


class ScoresDialog(QDialog):
    """A test whose score is typed, run several times (owner, 2026-10-21): one field per run, every score is stored
    as its own measured MANUAL run, so the report shows their average like any other test. An empty field is a run
    not done (nothing stored for it); failures are never typed as numbers."""

    def __init__(self, benchmark_label: str, session_label: str, count: int, default_unit: str = "points",
                 links=(), puget: bool = False, puget_version=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Scores: {benchmark_label}")
        self.puget_version = puget_version
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Session: {session_label}\nOne field per run, the score exactly as displayed; "
                                "leave a field empty for a run that did not finish. Recorded as MANUAL entries."))
        form = QFormLayout()
        self.application = self.app_version = self.preset = None
        if puget:
            from app.pro import pugetbench
            self.application = QComboBox()
            self.application.addItems([app for _, app, _ in pugetbench.TESTS])
            form.addRow("Application:", self.application)
        self.scores = []
        for n in range(count):
            field = QLineEdit()
            field.setPlaceholderText("score exactly as displayed")
            link = links[n] if n < len(links) else None
            form.addRow(f"Run {n + 1}:" if not link else f"Run {n + 1} ({link}):", field)
            self.scores.append(field)
        self.unit = QLineEdit(default_unit)
        self.note = QLineEdit()
        form.addRow("Unit:", self.unit)
        if puget:
            self.app_version = QLineEdit()
            self.app_version.setPlaceholderText("e.g. Photoshop 27.0 (shown on PugetBench's results)")
            self.preset = QLineEdit()
            self.preset.setPlaceholderText("e.g. Standard")
            form.addRow("Application version:", self.app_version)
            form.addRow("Preset:", self.preset)
        form.addRow("Note:", self.note)
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #C62828;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.values: Optional[dict] = None

    def _try_accept(self):
        scores = []
        for n, field in enumerate(self.scores):
            if not field.text().strip():
                continue
            score, err = parse_score("completed", field.text())
            if err:
                self.error.setText(f"Run {n + 1}: {err}")
                return
            scores.append((n, score))
        if not scores:
            self.error.setText("enter at least one score")
            return
        self.values = {"scores": [s for _, s in scores], "runs": scores, "unit": self.unit.text().strip() or "points",
                       "note": self.note.text().strip() or None}
        if self.application is not None:
            from app.pro import pugetbench
            app = self.application.currentText()
            self.values.update(benchmark_name=pugetbench.test_for(app), display_name=f"PugetBench {app}",
                               note=pugetbench.note_text(self.puget_version, self.app_version.text(),
                                                         self.preset.text(), self.values["note"] or ""))
        self.accept()

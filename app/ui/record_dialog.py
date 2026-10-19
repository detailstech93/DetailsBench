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

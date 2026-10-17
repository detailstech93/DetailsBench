"""Dialog to create or edit a run profile (name + run settings)."""

from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from app.profiles import LIMITS, RunProfile, validate_profile


class ProfileDialog(QDialog):
    def __init__(self, base: RunProfile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save run profile")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit("" if base.builtin else base.name)
        self.name.setPlaceholderText("e.g. Long soak")
        form.addRow("Name:", self.name)
        self.spins = {}
        labels = {"repetitions": "Measured runs:", "warmup_runs": "Warmup runs:",
                  "timeout_seconds": "Timeout (s):", "cooldown_seconds": "Cooldown (s):"}
        for key, (low, high) in LIMITS.items():
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setValue(getattr(base, key))
            self.spins[key] = spin
            form.addRow(labels[key], spin)
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #C62828;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.profile: Optional[RunProfile] = None

    def build(self) -> RunProfile:
        return RunProfile(self.name.text().strip(), **{k: s.value() for k, s in self.spins.items()})

    def _try_accept(self):
        profile = self.build()
        problems = validate_profile(profile)
        if problems:
            self.error.setText("; ".join(problems))
            return
        self.profile = profile
        self.accept()

"""llama-benchy settings before a plan (model, prompt/generated tokens, depths, concurrency, prefix caching)
and the run report viewer (llama_benchy_report.md from the run folder)."""

from typing import List, Mapping, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QTextBrowser,
    QVBoxLayout,
)

from app.ai.llama_benchy import WARNING, parse_ints


class LlamaBenchyDialog(QDialog):
    def __init__(self, models: List[Mapping], current: Optional[Mapping] = None, parent=None):
        super().__init__(parent)
        current = current or {}
        self.setWindowTitle("llama-benchy settings")
        self.models = list(models)
        form = QFormLayout()
        self.model = QComboBox()
        for m in self.models:
            self.model.addItem(f"{m['id']}  ({m.get('quantization', '?')}, {m.get('state', '?')})"
                               + ("  - most likely does not fit" if m.get("fit") else ""), m["id"])
            if m.get("fit"):
                self.model.setItemData(self.model.count() - 1, m["fit"], Qt.ToolTipRole)
        ids = [m["id"] for m in self.models]
        fitting = [m["id"] for m in self.models if not m.get("fit")]
        # the last choice when it fits, else the first model that fits (Fedora, 2026-11-19: the 27B was offered first
        # right after "Run without it")
        chosen = current.get("model") if current.get("model") in fitting else (fitting or [current.get("model")])[0]
        if chosen in ids:
            self.model.setCurrentIndex(ids.index(chosen))
        self.pp = QSpinBox()
        self.pp.setRange(1, 262144)
        self.pp.setValue(int(current.get("pp", 512)))
        self.tg = QSpinBox()
        self.tg.setRange(1, 65536)
        self.tg.setValue(int(current.get("tg", 128)))
        self.depths = QLineEdit(" ".join(str(d) for d in current.get("depths", [0, 4096])))
        self.depths.setToolTip("Context already in the conversation before the prompt, in tokens; several are "
                               "measured one after another (for example 0 4096 8192).")
        self.concurrency = QLineEdit(" ".join(str(c) for c in current.get("concurrency", [1])))
        self.concurrency.setToolTip("Parallel requests; several levels are measured one after another (for example 1 2 4).")
        self.prefix = QCheckBox("Prefix caching (measure the context load and the cached follow-up separately)")
        self.prefix.setChecked(bool(current.get("prefix_caching", False)))
        self.mtp = QComboBox()
        self.mtp.addItem("Off: the steady score of this PC", False)
        self.mtp.addItem("On: faster, but each run differs (compared only with MTP on)", True)
        self.mtp.setCurrentIndex(1 if current.get("mtp") else 0)
        self.mtp.setToolTip("MTP: the model's own draft head guesses tokens ahead (speculative decoding). Only some "
                            "models have it; LM Studio would turn it on by itself. Its speed follows the prompt's text.")
        form.addRow("Model (LM Studio):", self.model)
        form.addRow("Prompt tokens (pp):", self.pp)
        form.addRow("Generated tokens (tg):", self.tg)
        form.addRow("Context depths:", self.depths)
        form.addRow("Concurrency levels:", self.concurrency)
        form.addRow("", self.prefix)
        form.addRow("MTP (speculative decoding):", self.mtp)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        note = QLabel(WARNING + " Every depth runs at every concurrency level, so the run takes longer with more values.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #b91c1c;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> Optional[dict]:
        depths, conc = parse_ints(self.depths.text(), 0), parse_ints(self.concurrency.text(), 1)
        if depths is None:
            self.error.setText("Context depths: whole numbers of 0 or more, separated by spaces.")
            return None
        if conc is None:
            self.error.setText("Concurrency levels: whole numbers of 1 or more, separated by spaces.")
            return None
        return {"model": self.model.currentData(), "pp": self.pp.value(), "tg": self.tg.value(),
                "depths": depths, "concurrency": conc, "prefix_caching": self.prefix.isChecked(),
                "mtp": bool(self.mtp.currentData())}

    def _accept(self):
        if self.values() is not None and self.model.currentData():
            self.accept()


class ReportDialog(QDialog):
    def __init__(self, title: str, markdown: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 500)
        layout = QVBoxLayout(self)
        self.view = QTextBrowser()
        self.view.setMarkdown(markdown)
        layout.addWidget(self.view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

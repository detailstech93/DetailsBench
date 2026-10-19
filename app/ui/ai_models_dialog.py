""""AI models...": the fixed LM Studio models measured by llama-benchy (app/ai/model_set.py). The three built-in ones
stay; the owner adds any model + quantization (from what LM Studio has on disk, or typed as key@quant to download it
with the test's Install button) and removes their own again."""

from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)
from PySide6.QtCore import Qt

from app.ai import model_set


class AiModelsDialog(QDialog):
    def __init__(self, parent=None, data_dir: Optional[Path] = None,
                 on_disk: Optional[Callable[[], List[Tuple[str, str, str, str]]]] = None,
                 tell: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self.tell = tell or (lambda text: QMessageBox.information(self, "AI models", text))
        self.setWindowTitle("AI models (llama-benchy, fixed settings)")
        self.data_dir = data_dir
        self.changed = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Each model is its own test, always loaded the same way: context {model_set.CONTEXT}, every layer on "
            f"the graphics card, one request at a time, prompt {model_set.PP} / {model_set.TG} generated tokens, "
            f"{model_set.RUNS} runs. The three built-in models stay; add your own or remove them again. Earlier "
            "results are never deleted."))
        self.list = QListWidget()
        layout.addWidget(self.list)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.clicked.connect(self.remove)
        layout.addWidget(self.remove_btn)
        layout.addWidget(QLabel("Add a model: pick one LM Studio has on disk, or type key@quantization "
                                "(e.g. qwen/qwen3.5-9b@Q4_K_M); a model not on disk gets an Install button."))
        row = QHBoxLayout()
        self.choice = QComboBox()
        self.choice.setEditable(True)
        try:
            found = on_disk() if on_disk else model_set.on_disk(model_set.list_models(model_set.lmstudio.lms_path()))
        except Exception:
            found = []
        for key, quant, title, size in found:
            self.choice.addItem(f"{key}@{quant}  ({title}{', ' + size if size else ''})", (key, quant, title, size))
        self.choice.setCurrentIndex(-1)
        row.addWidget(self.choice, 1)
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self.add)
        row.addWidget(self.add_btn)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        own = model_set.custom_models(self.data_dir)
        for test_id, (title, key, quant, size) in model_set.all_models(self.data_dir).items():
            mine = test_id in own and test_id not in model_set.MODELS
            item = QListWidgetItem(f"{title}: {key} {quant}, {size}" + ("" if mine else "  (built in)"))
            item.setData(Qt.UserRole, test_id if mine else None)
            self.list.addItem(item)

    def selected_key(self) -> Tuple[str, str, str, str]:
        data = self.choice.currentData() if self.choice.currentIndex() >= 0 and \
            self.choice.currentText() == self.choice.itemText(self.choice.currentIndex()) else None
        if data:
            return data
        text = self.choice.currentText().strip().split()[0] if self.choice.currentText().strip() else ""
        key, _, quant = text.partition("@")
        return key, quant, "", ""

    def add(self) -> bool:
        key, quant, title, size = self.selected_key()
        try:
            model_set.add_model(key, quant, title, size, self.data_dir)
        except ValueError as e:
            self.tell(str(e))
            return False
        self.changed = True
        self.choice.setCurrentIndex(-1)
        self.choice.setEditText("")
        self.refresh()
        return True

    def remove(self) -> bool:
        item = self.list.currentItem()
        test_id = item.data(Qt.UserRole) if item else None
        if not test_id:
            self.tell("Pick one of your own models (the built-in ones stay).")
            return False
        self.changed = model_set.remove_model(test_id, self.data_dir) or self.changed
        self.refresh()
        return True

"""A question whose text may be longer than the screen (many tests, games and settings): the text scrolls and
the buttons stay visible. QMessageBox grows with its text and pushes its buttons off a laptop screen."""

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

SCREEN_SHARE = 0.85


def screen_room(widget: Optional[QWidget]) -> int:
    """Height a dialog may take on the screen of `widget` (or the primary screen)."""
    screen = (widget.screen() if widget is not None else None) or QApplication.primaryScreen()
    return int(screen.availableGeometry().height() * SCREEN_SHARE) if screen is not None else 800


class LongQuestion(QDialog):
    def __init__(self, parent, title: str, text: str, buttons: Sequence[Tuple[str, object]], default: int = 0):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.value = None
        outer = QVBoxLayout(self)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setMinimumWidth(560)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidget(self.label)
        outer.addWidget(self.scroll)
        row = QHBoxLayout()
        row.addStretch(1)
        self.buttons: List[QPushButton] = []
        for i, (label, value) in enumerate(buttons):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, v=value: self._pick(v))
            b.setDefault(i == default)
            b.setAutoDefault(i == default)
            row.addWidget(b)
            self.buttons.append(b)
        outer.addLayout(row)
        want = self.label.sizeHint().height() + 90
        height = min(want, screen_room(parent))
        self.scroll.setMinimumHeight(max(120, height - 90))
        self.resize(640, height)

    def _pick(self, value):
        self.value = value
        self.accept()


def ask_buttons(parent, title: str, text: str, buttons: Sequence[Tuple[str, object]], default: int = 0):
    """The value of the button pressed; None when the dialog is closed."""
    dialog = LongQuestion(parent, title, text, buttons, default)
    dialog.exec()
    return dialog.value


def ask_yes_no(parent, title: str, text: str) -> bool:
    """Yes / No; No is the default, as QMessageBox.question(..., QMessageBox.No) was."""
    return ask_buttons(parent, title, text, [("Yes", True), ("No", False)], default=1) is True

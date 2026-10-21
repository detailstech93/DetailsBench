"""This PC: the first screen. The PC's specs as cards; values come from the environment profile
(detected / not certain / not available) and, until it is ready, from the quick system summary."""

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from app.environment.profile import DETECTED, UNVERIFIED

NOT_AVAILABLE = "not available"


def _text(value: Any) -> Optional[str]:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _field(profile: Optional[dict], name: str) -> Tuple[Optional[str], str]:
    """(text, status) of one profile field; status is detected / unverified / unavailable."""
    f = (profile or {}).get(name)
    if not isinstance(f, dict):
        return None, "unavailable"
    return _text(f.get("value")), f.get("status") or "unavailable"


def spec_cards(system_info: dict, profile: Optional[dict]) -> List[Tuple[str, List[Tuple[str, Optional[str], str]]]]:
    """The cards as data: [(card title, [(row title, text or None, status)])]. Pure, for tests."""
    si = system_info or {}

    def pick(name: str, fallback: Any) -> Tuple[Optional[str], str]:
        text, status = _field(profile, name)
        if text is None and _text(fallback) is not None:
            return _text(fallback), UNVERIFIED if profile else DETECTED
        return text, status

    cpu = pick("cpu", si.get("cpu_brand"))
    cores, threads = si.get("core_count"), si.get("thread_count")
    ram = pick("ram_gb", si.get("ram_size_gb"))
    gpu = pick("gpu", si.get("gpu_name"))
    os_name = pick("os_name", si.get("operating_system"))
    version, build = _field(profile, "os_version"), _field(profile, "os_build")
    native, app_arch = _field(profile, "native_arch"), _field(profile, "process_arch")
    emulated = _field(profile, "x64_emulated")
    arch_rows = [("Architecture", *native)]
    if app_arch[0] and native[0] and app_arch[0] != native[0]:
        arch_rows.append(("This app runs as", f"{app_arch[0]} (emulated)", app_arch[1]))
    if emulated[0] is not None:
        arch_rows.append(("x64 programs run emulated", *emulated))
    return [
        ("Processor", [("CPU", *cpu),
                       ("Cores / threads", f"{cores} / {threads}" if cores and threads else None,
                        DETECTED if cores and threads else "unavailable")]),
        ("Memory", [("RAM (GB)", *ram)]),
        ("Graphics", [("GPU", *gpu), ("DirectX", *_field(profile, "directx")),
                      ("Vulkan", *_field(profile, "vulkan")), ("OpenGL", *_field(profile, "opengl"))]),
        ("Operating system", [("OS", *os_name), ("Version", *version), ("Build", *build),
                              ("Virtual machine", *_field(profile, "is_virtual_machine"))]),
        ("Platform", arch_rows),
    ]


def specs_text(cards) -> str:
    """Plain text for the clipboard (the computer name is never part of the specs)."""
    lines = []
    for title, rows in cards:
        lines.append(title)
        for name, text, status in rows:
            mark = " (not certain)" if status == UNVERIFIED and text else ""
            lines.append(f"  {name}: {text or NOT_AVAILABLE}{mark}")
    return "\n".join(lines)


class ThisPcPage(QWidget):
    choose_tests = Signal()

    def __init__(self, system_info: dict, parent=None):
        super().__init__(parent)
        self.system_info = system_info or {}
        self.profile: Optional[dict] = None
        self.value_labels: Dict[Tuple[str, str], QLabel] = {}

        layout = QVBoxLayout(self)
        head = QLabel("This PC")
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        head.setFont(font)
        layout.addWidget(head)
        self.note = QLabel("Reading the system... values marked \"not certain\" could not be confirmed.")
        self.note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.note)

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        layout.addLayout(self.grid)
        layout.addStretch(1)

        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy specs")
        self.copy_btn.clicked.connect(self.copy_specs)
        self.tests_btn = QPushButton("Choose tests  →")
        self.tests_btn.setMinimumHeight(40)
        self.tests_btn.setStyleSheet("QPushButton { font-size: 14px; font-weight: bold; padding: 6px 24px; }")
        self.tests_btn.clicked.connect(self.choose_tests.emit)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        row.addWidget(self.tests_btn)
        layout.addLayout(row)
        self.refresh()

    def set_profile(self, profile: Optional[dict]):
        self.profile = profile
        self.note.setText("Values marked \"not certain\" could not be confirmed; hover a value for details.")
        self.refresh()

    def cards(self):
        return spec_cards(self.system_info, self.profile)

    def refresh(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.value_labels.clear()
        for i, (title, rows) in enumerate(self.cards()):
            card = QFrame()
            card.setFrameShape(QFrame.StyledPanel)
            card.setStyleSheet("QFrame { border-radius: 6px; }")
            box = QVBoxLayout(card)
            t = QLabel(title)
            t.setStyleSheet("font-weight: bold; font-size: 13px;")
            box.addWidget(t)
            for name, text, status in rows:
                value = QLabel(f"{name}: {text or NOT_AVAILABLE}" + ("  (not certain)" if status == UNVERIFIED
                                                                      and text else ""))
                value.setWordWrap(True)
                value.setTextInteractionFlags(Qt.TextSelectableByMouse)
                value.setStyleSheet("" if status == DETECTED and text else
                                    "color: #B26A00;" if status == UNVERIFIED and text else "color: #757575;")
                f = (self.profile or {}).get({"CPU": "cpu", "GPU": "gpu"}.get(name, ""))
                if isinstance(f, dict) and f.get("source"):
                    value.setToolTip(f"source: {f['source']}")
                self.value_labels[(title, name)] = value
                box.addWidget(value)
            box.addStretch(1)
            self.grid.addWidget(card, i // 3, i % 3)

    def copy_specs(self) -> str:
        text = specs_text(self.cards())
        QGuiApplication.clipboard().setText(text)
        return text

"""Pick the smart plug this PC is on, through Home Assistant (owner, 2026-10-27). Its power is then read with every
test's other readings (app/telemetry/home_assistant.py)."""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from app.paths import save_setting
from app.telemetry import home_assistant as ha

HELP = ("Whole device power from a smart plug, read through Home Assistant: any plug it shows in W works, whatever "
        "its brand. In Home Assistant open your profile > Security > Long-lived access tokens > Create token, "
        "and paste it here. The app only reads the plug; it never switches it. The token stays on this PC only.")


class PlugDialog(QDialog):
    def __init__(self, data_dir: Path, parent=None, fetch: Callable = ha.power_sensors):
        super().__init__(parent)
        self.data_dir, self.fetch = Path(data_dir), fetch
        self.setWindowTitle("Whole device power (smart plug)")
        url, token, self.entity = ha.settings(self.data_dir)
        layout = QVBoxLayout(self)
        help_label = QLabel(HELP)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        form = QFormLayout()
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("http://homeassistant.local:8123")
        self.token = QLineEdit(token)
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("long-lived access token")
        self.plug = QComboBox()
        form.addRow("Home Assistant address:", self.url)
        form.addRow("Token:", self.token)
        form.addRow("Plug this PC is on:", self.plug)
        layout.addLayout(form)
        self.status = QLabel(f"Now: {self.entity}" if self.entity else "No plug picked: whole device power is not read.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect / refresh readings")
        self.connect_btn.clicked.connect(self.load)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        self.off_btn = QPushButton("Do not read a plug")
        self.off_btn.clicked.connect(self.turn_off)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for b in (self.connect_btn, self.save_btn, self.off_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        self.save_btn.setEnabled(False)

    def load(self) -> None:
        # shortcut: the read blocks this window for up to ha.TIMEOUT seconds; move to a thread if that annoys
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            sensors = self.fetch(self.url.text().strip(), self.token.text().strip())
        except Exception as e:
            self.status.setText(f"Home Assistant could not be read: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        chosen = self.plug.currentData() or self.entity
        self.plug.clear()
        for entity, name, watts in sensors:
            self.plug.addItem(f"{name}: {'-' if watts is None else f'{watts:.0f} W'}  ({entity})", entity)
        if chosen and self.plug.findData(chosen) >= 0:
            self.plug.setCurrentIndex(self.plug.findData(chosen))
        self.status.setText(f"{len(sensors)} power sensors found. The plug this PC is on draws about what this PC "
                            "draws now; refresh under load to be sure." if sensors else
                            "Home Assistant answered, but it shows no sensor measured in W.")
        self.save_btn.setEnabled(bool(sensors))

    def save(self) -> None:
        for key, value in ((ha.URL_KEY, self.url.text().strip()), (ha.TOKEN_KEY, self.token.text().strip()),
                           (ha.ENTITY_KEY, self.plug.currentData() or "")):
            save_setting(self.data_dir, key, value)
        self.accept()

    def turn_off(self) -> None:
        save_setting(self.data_dir, ha.ENTITY_KEY, "")
        self.accept()

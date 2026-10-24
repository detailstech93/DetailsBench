"""A result's readings over the test as a chart (owner, 2026-10-25): pick the reading from a list; the worst run of
it is shown (app/library/readings.py); Save HTML report keeps it as one page."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Optional

from PySide6.QtCore import QByteArray
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.library import readings

logger = logging.getLogger(__name__)


def _qt_svg(text: str) -> bytes:
    """Qt's SVG has no CSS variables: each var(--x, fallback) becomes its fallback (the light colours)."""
    return re.sub(r"var\(--[\w-]+,\s*([^)]+)\)", r"\1", text).encode("utf-8")


class ReadingsDialog(QDialog):
    def __init__(self, data: Mapping, title: str, parent=None, save_as: Optional[Callable[[str], str]] = None):
        super().__init__(parent)
        self.data, self.title, self.save_as = data, title, save_as
        self.setWindowTitle(f"Readings: {title}")
        self.resize(860, 480)
        layout = QVBoxLayout(self)
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Reading:"))
        self.reading = QComboBox()
        for key, label, unit in readings.choices(data):
            self.reading.addItem(label, (key, label, unit))
        self.reading.currentIndexChanged.connect(lambda _: self.draw())
        pick.addWidget(self.reading, 1)
        layout.addLayout(pick)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.chart = QSvgWidget()
        self.chart.setMinimumSize(760, 300)
        self.chart.setStyleSheet("background: white; border-radius: 6px;")
        layout.addWidget(self.chart, 1)
        bar = QHBoxLayout()
        self.save_btn = QPushButton("Save HTML report...")
        self.save_btn.clicked.connect(self.save_report)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bar.addWidget(self.save_btn)
        bar.addStretch()
        bar.addWidget(close)
        layout.addLayout(bar)
        self.draw()

    def _chosen(self):
        choice = self.reading.currentData()
        if not choice:
            return None, "", choice
        run, why = readings.worst(self.data, choice[0])
        return run, why, choice

    def draw(self) -> None:
        run, why, choice = self._chosen()
        self.save_btn.setEnabled(run is not None)
        if run is None:
            self.detail.setText("No readings were kept for this result.")
            self.chart.load(QByteArray())
            return
        key, label, unit = choice
        s = readings.summary(run, key)
        self.detail.setText(f"{label} over the test; {why}. Lowest {s['min']:,.1f}, average {s['avg']:,.1f}, "
                            f"highest {s['max']:,.1f} {unit}.")
        self.chart.load(QByteArray(_qt_svg(readings.svg(run, key, label, unit))))

    def save_report(self) -> None:
        run, why, choice = self._chosen()
        if run is None or self.save_as is None:
            return
        key, label, unit = choice
        start = Path.home() / "Desktop"
        name = re.sub(r'[<>:"/\\|?*]', "_", f"DetailsBench {self.title} {label} {datetime.now():%Y-%m-%d_%H%M}")
        path = self.save_as(str((start if start.is_dir() else Path.home()) / f"{name}.html"))
        if not path:
            return
        try:
            Path(path).write_text(readings.report_html(self.title, why, label, unit, run, key), encoding="utf-8")
        except OSError as e:
            self.detail.setText(f"Not saved: {e}")
            return
        logger.info("Results library: readings chart saved to %s", path)
        self.detail.setText(self.detail.text().split("\nSaved:")[0] + f"\nSaved: {path}")

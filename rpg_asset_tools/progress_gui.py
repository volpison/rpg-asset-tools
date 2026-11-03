"""progress_gui.py - the small window that shows what a long operation is doing."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)


class ProgressDialog(QDialog):
    """Appears only if the work takes longer than a moment (see MainWindow._run)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 320)
        self.started = time.monotonic()
        self.active = True
        lay = QVBoxLayout(self)
        self.heading = QLabel(f"<b>{title}</b>")
        lay.addWidget(self.heading)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)                 # "busy" until the first real numbers arrive
        lay.addWidget(self.bar)
        self.detail = QLabel("Starting...")
        lay.addWidget(self.detail)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        lay.addWidget(self.log, stretch=1)
        note = QLabel("Large projects can take a while the first time. Later openings reuse "
                      "what was already read (the cache in .asset_tools/cache).")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Hide")
        b.setToolTip("The work continues in the background.")
        b.clicked.connect(self.hide)
        row.addWidget(b)
        lay.addLayout(row)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def set_progress(self, done: int, total: int, text: str = ""):
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        else:
            self.bar.setRange(0, 0)
        bits = [text or "Working..."]
        if total > 0:
            bits.append(f"{done}/{total}")
        bits.append(f"{self.elapsed():.0f}s")
        self.detail.setText("  -  ".join(bits))

    def add_log(self, text: str):
        self.log.appendPlainText(str(text).rstrip())

    def finish(self):
        self.active = False
        self.close()

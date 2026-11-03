"""usage_gui.py - the "Where is this used?" window (read-only; see usage.py)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from fit_table import FitTable
import usage


class UsageDialog(QDialog):
    HEADERS = ["Where", "Event", "Page", "Line", "What"]

    def __init__(self, game: Path, title: str, items: list, total: int | None = None, parent=None):
        super().__init__(parent)
        self.game, self.title_text, self.items, self.total = Path(game), title, items, total
        self.setWindowTitle("Where is this used?")
        self.resize(900, 460)
        lay = QVBoxLayout(self)
        self.heading = QLabel(f"<b>{title}</b> - {len(items)} place(s)"
                              + (f" shown of {total}" if total and total > len(items) else ""))
        lay.addWidget(self.heading)
        hint = QLabel("RPG Maker cannot open a spot directly. Open the map, find the event by its "
                      "position, then the page and line shown here. Use <i>Copy</i> to paste the path.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.table = FitTable(len(items), len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(False)
        for r, it in enumerate(items):
            what = it.what + ("  (disabled plugin)" if it.optional else "")
            for c, v in enumerate([it.place, it.event, it.page, it.line, what]):
                cell = QTableWidgetItem(v)
                cell.setToolTip(str(v))
                self.table.setItem(r, c, cell)
        lay.addWidget(self.table, stretch=1)
        row = QHBoxLayout()
        b_copy = QPushButton("Copy selected")
        b_copy.clicked.connect(self.copy_selected)
        b_all = QPushButton("Copy all")
        b_all.clicked.connect(self.copy_all)
        b_open = QPushButton("Show file in folder")
        b_open.clicked.connect(self.open_folder)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for b in (b_copy, b_all, b_open):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)

    def selected_items(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.items[r] for r in rows]

    def copy_selected(self):
        chosen = self.selected_items()
        if chosen:
            self._clip(usage.usage_text(self.title_text, chosen))

    def copy_all(self):
        self._clip(usage.usage_text(self.title_text, self.items, self.total))

    def _clip(self, text: str):
        QApplication.clipboard().setText(text)

    def open_folder(self):
        chosen = self.selected_items()
        if len(chosen) != 1:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str((self.game / chosen[0].file).parent)))

"""Table whose columns share the available width sensibly.

- Columns start sized to their content, but no column may take more than its
  fair share when the window is too narrow (long text is elided; the full text
  is in the tooltip).
- When there is spare room it is shared proportionally, so the table fills the
  window instead of leaving one giant empty column.
- The user can drag column edges and drag headers to reorder; once they resize
  a column by hand we stop re-fitting so their layout is kept.
"""
from __future__ import annotations

from PySide6.QtWidgets import QHeaderView, QTableWidget

PAD = 14          # room for cell margins + sort/elide marks
MIN_W = 48


def fit_widths(wants: list[int], avail: int, min_w: int = MIN_W) -> list[int]:
    """Pure helper: final column widths. Sum == avail when avail allows it."""
    n = len(wants)
    if n == 0:
        return []
    wants = [max(min_w, int(w)) for w in wants]
    if avail <= min_w * n:
        return [min_w] * n
    if sum(wants) <= avail:                       # spread the extra room by weight
        extra = avail - sum(wants)
        tot = sum(wants)
        out = [w + extra * w // tot for w in wants]
        out[-1] += avail - sum(out)
        return out
    # too narrow: water-filling - small columns keep their size, big ones share the rest
    out = [0] * n
    left = avail
    todo = sorted(range(n), key=lambda i: wants[i])
    while todo:
        share = left // len(todo)
        i = todo[0]
        if wants[i] <= share:
            out[i] = wants[i]
            left -= wants[i]
            todo.pop(0)
        else:
            for j in todo:
                out[j] = max(min_w, share)
            break
    return out


class FitTable(QTableWidget):
    def __init__(self, rows: int, cols: int, *a):
        super().__init__(rows, cols, *a)
        self._manual = False
        self._fitting = False
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(False)
        h.setSectionsMovable(True)
        h.setMinimumSectionSize(MIN_W)
        h.sectionResized.connect(self._on_section_resized)
        self.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self._empty_text = ""
        self._empty_label = None

    def set_empty_text(self, text: str) -> None:
        """Message shown in the middle of the table while it has no rows."""
        self._empty_text = text
        self._update_empty()

    def _update_empty(self):
        try:
            if self._empty_label is None:
                if not self._empty_text:
                    return
                from PySide6.QtCore import Qt
                from PySide6.QtWidgets import QLabel
                lab = QLabel(self._empty_text, self.viewport())
                lab.setAlignment(Qt.AlignCenter)
                lab.setWordWrap(True)
                lab.setStyleSheet("color: gray; font-size: 11pt;")
                self._empty_label = lab
            lab = self._empty_label
            lab.setText(self._empty_text)
            show = self.rowCount() == 0 and bool(self._empty_text)
            lab.setVisible(show)
            if show:
                vp = self.viewport()
                w, h = vp.width(), vp.height()
                if isinstance(w, int) and isinstance(h, int):
                    lab.setGeometry(20, 10, max(50, w - 40), max(40, h - 20))
        except RuntimeError:                 # widget already deleted
            pass

    def _on_section_resized(self, *_):
        if not self._fitting:
            self._manual = True

    def reset_layout(self):
        self._manual = False
        self.refit()

    def refit(self):
        self._update_empty()
        if self._manual:
            return
        avail = self.viewport().width()
        if not isinstance(avail, int) or avail < 100:     # hidden tab / stub
            return
        h = self.horizontalHeader()
        n = self.columnCount()
        wants = [max(h.sectionSizeHint(c), self.sizeHintForColumn(c)) + PAD for c in range(n)]
        widths = fit_widths(wants, avail)
        self._fitting = True
        try:
            for c, w in enumerate(widths):
                self.setColumnWidth(c, w)
        finally:
            self._fitting = False

    def setRowCount(self, n):
        super().setRowCount(n)
        self._manual_data_refit()

    def _manual_data_refit(self):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.refit)               # after the caller filled the rows

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_empty()
        self.refit()

    def showEvent(self, e):
        super().showEvent(e)
        self.refit()

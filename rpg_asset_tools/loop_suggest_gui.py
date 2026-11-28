"""
loop_suggest_gui.py
-------------------
"Suggest loops": a small window that asks loopfind for the best loop points of the song in the
loop editor and lists them. The user picks one and the editor uses it (nothing is written to the
file until "Save tags").

Thin on purpose: the search is loopfind (in a background thread, with Cancel), the numbers shown
are loopmath / beatgrid; this file only draws a table and talks to Qt.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import beatgrid
import loopmath

MODES = ("Find start and end", "Keep the current start", "Keep the current end")
_TOLERANCE_SECONDS = 0.25         # how far the kept point may move (it is then refined to a sample)


class _SearchJob(QObject):
    done = Signal(object)             # list of loopfind.Candidate
    failed = Signal(str)

    def __init__(self, samples, rate, **options):
        super().__init__()
        self._samples = samples
        self._rate = rate
        self._options = options
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def start(self) -> None:
        threading.Thread(target=self._run, name="loop-editor-suggest", daemon=True).start()

    def _run(self) -> None:
        try:
            import loopfind
            result = loopfind.find_loops(self._samples, self._rate, cancel=lambda: self._cancelled,
                                         **self._options)
        except Exception as e:         # never leave the window waiting for ever
            if not self._cancelled:
                self.failed.emit(f"The search stopped: {e}")
            return
        finally:
            self._samples = None       # the song is not kept alive by a finished job
        if not self._cancelled:
            self.done.emit(result)


class SuggestDialog(QDialog):
    """`use(start, end, hear)` is called when the user picks a suggestion."""

    def __init__(self, parent, samples, rate, current, grid, use):
        super().__init__(parent)
        self.setWindowTitle("Suggest loops")
        self.resize(640, 420)
        self._samples = samples
        self._rate = rate
        self._current = current          # (start, end) now in the editor
        self._grid = grid
        self._use = use
        self._job = None
        self.candidates = []
        self.searching = False

        lay = QVBoxLayout(self)
        intro = QLabel("The song is compared with itself to find places where it repeats, so the jump "
                       "from the loop end back to the loop start is hard to hear. These are suggestions: "
                       "pick one and listen to it.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        opts = QHBoxLayout()
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(MODES)
        self.cmb_mode.setToolTip("Keep one of the current loop points and look only for the other one.")
        opts.addWidget(self.cmb_mode)
        opts.addWidget(QLabel("Shortest loop:"))
        self.spin_min = QDoubleSpinBox()
        self.spin_min.setRange(1.0, 600.0)
        self.spin_min.setValue(8.0)
        self.spin_min.setSuffix(" s")
        opts.addWidget(self.spin_min)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(lambda _c=False: self.search())
        opts.addWidget(self.btn_search)
        opts.addStretch(1)
        lay.addLayout(opts)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Start", "End", "Length", "Match"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.use_selected(hear=True))
        lay.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_use = QPushButton("Use")
        self.btn_use.setToolTip("Put these loop points in the editor (nothing is saved yet).")
        self.btn_use.clicked.connect(lambda _c=False: self.use_selected(hear=False))
        self.btn_hear = QPushButton("Use and hear the seam")
        self.btn_hear.clicked.connect(lambda _c=False: self.use_selected(hear=True))
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        for b in (self.btn_use, self.btn_hear, self.btn_close):
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self._update_buttons()

    # ----- searching
    def _options(self) -> dict:
        mode = self.cmb_mode.currentIndex()
        tol = int(_TOLERANCE_SECONDS * self._rate)
        s, e = self._current
        opts = {"min_length": float(self.spin_min.value()), "count": 8}
        if mode == 1:
            opts["start_range"] = (max(0, s - tol), s + tol)
        elif mode == 2:
            opts["end_range"] = (max(0, e - tol), e + tol)
        return opts

    def search(self) -> None:
        if self.searching or self._samples is None:
            return
        self._stop_job()
        self.searching = True
        self.btn_search.setEnabled(False)
        self.table.setRowCount(0)
        self.candidates = []
        import loopfind
        note = ""
        if loopfind.coarse_factor(len(self._samples), self._rate) > 1:
            note = " This song is very long, so it is compared in a coarser way to save memory."
        self.status.setText("Searching... (a few seconds for a long song)" + note)
        self._job = _SearchJob(self._samples, self._rate, **self._options())
        self._job.done.connect(self._on_done)
        self._job.failed.connect(self._on_failed)
        self._job.start()

    def _stop_job(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self._job = None

    def _on_done(self, found) -> None:
        if self._job is None:                     # the window was closed meanwhile
            return
        self.searching = False
        self.btn_search.setEnabled(True)
        self.candidates = list(found)
        self.table.setRowCount(len(self.candidates))
        for row, c in enumerate(self.candidates):
            length = loopmath.format_time(c.length, self._rate)
            if self._grid is not None:
                try:
                    length += f"  ({beatgrid.frames_to_bars(self._grid, self._rate, c.length):.2f} bars)"
                except Exception:
                    pass
            cells = (str(row + 1), loopmath.format_time(c.start, self._rate),
                     loopmath.format_time(c.end, self._rate), length,
                     f"{round(100 * (0.5 * c.score + 0.5 * c.seam))} %")
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        if self.candidates:
            self.table.selectRow(0)
            self.status.setText(f"{len(self.candidates)} suggestions, best first. 'Match' is how alike the "
                                "music and the waveform are at the jump. Double-click to use one and "
                                "hear the seam.")
        else:
            self.status.setText("Nothing convincing found. Songs without a repeating part (or very "
                                "short ones) cannot be matched. Try a shorter 'Shortest loop', or set "
                                "the points by hand.")
        self._update_buttons()

    def _on_failed(self, message: str) -> None:
        if self._job is None:
            return
        self.searching = False
        self.btn_search.setEnabled(True)
        self.status.setText(message)
        self._update_buttons()

    # ----- choosing
    def selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self.candidates):
            return None
        return self.candidates[rows[0].row()]

    def _update_buttons(self) -> None:
        on = self.selected() is not None
        self.btn_use.setEnabled(on)
        self.btn_hear.setEnabled(on)

    def use_selected(self, hear: bool) -> bool:
        c = self.selected()
        if c is None:
            return False
        self._use(c.start, c.end, hear)
        return True

    def done(self, result: int) -> None:
        self._stop_job()
        self.searching = False
        self._samples = None              # a closed window is only hidden: do not keep the song in memory
        super().done(result)

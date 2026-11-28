"""
bpm_finder_gui.py
-----------------
"BPM finder": a small window with two ways to find the tempo of the song in the loop editor:

* **Detect** listens to the song (bpmdetect, in a background thread, with Cancel by closing) and
  lists the most likely tempi with a first beat for each;
* **Tap** works out the tempo from the user's taps in time with the music (taptempo).

The user picks one and the editor uses it (nothing is written to the audio file; the grid is kept
per song in the project's .asset_tools folder, like any other beat grid).

Thin on purpose: the maths is bpmdetect / taptempo; this file only draws and talks to Qt.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

import loopmath
import taptempo


class _DetectJob(QObject):
    done = Signal(object)             # list of bpmdetect.Tempo
    failed = Signal(str)

    def __init__(self, samples, rate, first, last, beats_per_bar):
        super().__init__()
        self._args = (samples, rate)
        self._kw = {"first": first, "last": last, "beats_per_bar": beats_per_bar}
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def start(self) -> None:
        threading.Thread(target=self._run, name="loop-editor-bpm", daemon=True).start()

    def _run(self) -> None:
        try:
            import bpmdetect
            result = bpmdetect.detect(*self._args, cancel=lambda: self._cancelled, **self._kw)
        except Exception as e:         # never leave the window waiting for ever
            if not self._cancelled:
                self.failed.emit(f"The detection stopped: {e}")
            return
        if not self._cancelled:
            self.done.emit(result)


class BpmFinderDialog(QDialog):
    """`use(bpm, offset)` is called when the user picks a tempo; offset is None to keep the first beat."""

    def __init__(self, parent, samples, rate, loop_part, beats_per_bar, use):
        super().__init__(parent)
        self.setWindowTitle("BPM finder")
        self.resize(560, 470)
        self._samples = samples
        self._rate = rate
        self._loop_part = loop_part       # (start, end) of the loop, or None when there is no loop
        self._bpb = beats_per_bar
        self._use = use
        self._job = None
        self.tempos = []
        self.searching = False
        self._tapper = taptempo.TapTempo()
        self.tapped_bpm = None

        lay = QVBoxLayout(self)
        intro = QLabel("Two ways to find the tempo. <b>Detect</b> listens to the song and lists the likely "
                       "tempi; the half or double of the right tempo is a common mistake, so check the "
                       "answer with the metronome. Or <b>tap</b> the button in time with the music.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        lay.addWidget(intro)

        opts = QHBoxLayout()
        self.chk_loop_only = QCheckBox("Only the loop part")
        self.chk_loop_only.setToolTip("Listen only to the part between the loop markers (for a song whose "
                                      "intro has another tempo).")
        self.chk_loop_only.setEnabled(loop_part is not None)
        opts.addWidget(self.chk_loop_only)
        self.btn_detect = QPushButton("Detect")
        self.btn_detect.clicked.connect(lambda _c=False: self.detect())
        opts.addWidget(self.btn_detect)
        opts.addStretch(1)
        lay.addLayout(opts)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["BPM", "First beat", "Sure", "Note"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.use_selected(with_offset=True))
        lay.addWidget(self.table, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_use = QPushButton("Use tempo and first beat")
        self.btn_use.setToolTip("Set the BPM and put the first beat where the detection found it "
                                "(nothing is saved in the audio file).")
        self.btn_use.clicked.connect(lambda _c=False: self.use_selected(with_offset=True))
        self.btn_use_tempo = QPushButton("Use tempo only")
        self.btn_use_tempo.setToolTip("Set the BPM and keep the first beat as it is now.")
        self.btn_use_tempo.clicked.connect(lambda _c=False: self.use_selected(with_offset=False))
        row.addWidget(self.btn_use)
        row.addWidget(self.btn_use_tempo)
        lay.addLayout(row)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)

        tap = QHBoxLayout()
        self.btn_tap = QPushButton("Tap")
        self.btn_tap.setMinimumHeight(48)
        self.btn_tap.setMinimumWidth(120)
        self.btn_tap.setToolTip("Tap in time with the music (a mouse click or the space bar while the button "
                                "has the focus). Three taps give a tempo; it gets better with more.")
        self.btn_tap.clicked.connect(lambda _c=False: self.tap())
        tap.addWidget(self.btn_tap)
        self.lbl_tap = QLabel("Tap in time with the music.")
        tap.addWidget(self.lbl_tap, 1)
        self.btn_tap_reset = QPushButton("Start again")
        self.btn_tap_reset.clicked.connect(lambda _c=False: self.reset_taps())
        tap.addWidget(self.btn_tap_reset)
        self.btn_use_tap = QPushButton("Use tapped tempo")
        self.btn_use_tap.setToolTip("Set the BPM and keep the first beat as it is now.")
        self.btn_use_tap.clicked.connect(lambda _c=False: self.use_tapped())
        tap.addWidget(self.btn_use_tap)
        lay.addLayout(tap)

        close = QHBoxLayout()
        close.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        close.addWidget(self.btn_close)
        lay.addLayout(close)
        self._update_buttons()

    # ----- detecting
    def detect(self) -> None:
        if self.searching:
            return
        self._stop_job()
        first, last = 0, int(self._samples.shape[0])
        if self.chk_loop_only.isChecked() and self._loop_part is not None:
            first, last = self._loop_part
        self.searching = True
        self.btn_detect.setEnabled(False)
        self.table.setRowCount(0)
        self.tempos = []
        self.status.setText("Listening... (a few seconds for a long song)")
        self._job = _DetectJob(self._samples, self._rate, first, last, self._bpb)
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
        self.btn_detect.setEnabled(True)
        self.tempos = list(found)
        self.table.setRowCount(len(self.tempos))
        for row, t in enumerate(self.tempos):
            note = {"half": "half of the best", "double": "double the best"}.get(t.note, "best" if row == 0 else "")
            cells = (f"{t.bpm:g}", loopmath.format_time(t.offset, self._rate), f"{round(100 * t.confidence)} %", note)
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        if self.tempos:
            self.table.selectRow(0)
            self.status.setText("Best first. 'First beat' is a guess of where beat 1 of a bar falls: if the bar "
                                "lines are on the wrong beat, move the cursor to the right place and press "
                                "'First beat = cursor'. If the tempo feels twice or half as fast, pick that row.")
        else:
            self.status.setText("No clear beat found. Very short songs, ambient music or a changing tempo cannot "
                                "be measured. Try 'Only the loop part', tap the tempo, or type it.")
        self._update_buttons()

    def _on_failed(self, message: str) -> None:
        if self._job is None:
            return
        self.searching = False
        self.btn_detect.setEnabled(True)
        self.status.setText(message)
        self._update_buttons()

    # ----- tapping
    def tap(self, now: float | None = None) -> None:
        bpm = self._tapper.tap(time.monotonic() if now is None else now)
        self.tapped_bpm = bpm
        n = self._tapper.count
        if bpm is None:
            self.lbl_tap.setText("Keep tapping..." if n else "Tap in time with the music.")
        else:
            self.lbl_tap.setText(f"{bpm:.1f} BPM  ({n} taps)")
        self._update_buttons()

    def reset_taps(self) -> None:
        self._tapper.reset()
        self.tapped_bpm = None
        self.lbl_tap.setText("Tap in time with the music.")
        self._update_buttons()

    def use_tapped(self) -> bool:
        if self.tapped_bpm is None:
            return False
        self._use(round(self.tapped_bpm, 1), None)
        return True

    # ----- choosing
    def selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self.tempos):
            return None
        return self.tempos[rows[0].row()]

    def _update_buttons(self) -> None:
        on = self.selected() is not None
        self.btn_use.setEnabled(on)
        self.btn_use_tempo.setEnabled(on)
        self.btn_use_tap.setEnabled(self.tapped_bpm is not None)

    def use_selected(self, with_offset: bool) -> bool:
        t = self.selected()
        if t is None:
            return False
        self._use(t.bpm, t.offset if with_offset else None)
        return True

    def done(self, result: int) -> None:
        self._stop_job()
        self.searching = False
        super().done(result)

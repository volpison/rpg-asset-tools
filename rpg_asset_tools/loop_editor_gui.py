"""
loop_editor_gui.py
------------------
The loop editor window (step 1: view and listen; nothing is written to the file).

It shows the waveform of an Ogg Vorbis file, the loop stored in its LOOPSTART / LOOPLENGTH
tags as a bar above the wave, and plays the file the way the game would: from the start,
then round and round between the loop start and the loop end, with a sample-exact jump.

Thin on purpose: reading the tags is oggmeta, the loop numbers are loopmath, the drawing
data is waveform and the "which sample comes next" rules are loopplayback. This file only
draws them and talks to Qt. numpy is imported lazily, so the module imports without it
(open_loop_editor then explains what to install).
"""

from __future__ import annotations

import math
import threading
from html import escape
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollBar, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

import audiodecode
import enginetags
import loopedit
import loopmath
import loopwrite
import oggmeta
import oggwrite

_RULER_H = 22
_BAR_H = 18
_LOOP_COLOR = QColor(255, 150, 30)
_NONE_COLOR = QColor(130, 130, 130)
_CURSOR_COLOR = QColor(230, 60, 60)
_GRAB_PX = 7                          # how close the mouse must be to grab a loop marker
_SNAP_SECONDS = 0.02                  # how far (each way) a marker may move to reach a zero crossing


def _np():
    """The numpy-based helper modules, imported on first use."""
    import loopplayback
    import waveform
    return waveform, loopplayback


# --------------------------------------------------------------------------
# decoding in the background
# --------------------------------------------------------------------------

class _DecodeJob(QObject):
    done = Signal(object, object)         # DecodedAudio, Pyramid
    failed = Signal(str)

    def __init__(self, path: Path):
        super().__init__()
        self._path = path

    def start(self) -> None:
        threading.Thread(target=self._run, name="loop-editor-decode", daemon=True).start()

    def _run(self) -> None:
        try:
            audio = audiodecode.decode(self._path)
            waveform, _ = _np()
            pyr = waveform.build_pyramid(audio.samples)
        except audiodecode.DecodeError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:             # never leave the window waiting for ever
            self.failed.emit(f"Something went wrong while reading the sound: {e}")
            return
        self.done.emit(audio, pyr)


# --------------------------------------------------------------------------
# the sound source the audio device pulls from
# --------------------------------------------------------------------------

class _Feeder:
    """Cuts the sound into pieces for the sound device, looping exactly like the game. It lives in
    the window's thread; a timer calls `fill` to top up the device's buffer (push mode)."""

    MAX_FRAMES = 16384

    def __init__(self, samples):
        self._samples = samples
        self._pos = 0
        self._loop_start = 0
        self._loop_end = None
        self._pending = b""               # bytes the device did not take yet
        self.finished = False             # a play-once run has handed over its last sample
        self.fed = 0                      # bytes given to the device (for the report)
        self.feeds = 0

    def configure(self, pos: int, loop_start: int, loop_end) -> None:
        self._pos, self._loop_start, self._loop_end = pos, loop_start, loop_end
        self._pending = b""
        self.finished = False

    def fill(self, io, free_bytes: int) -> None:
        """Write as much as the device has room for."""
        _, loopplayback = _np()
        frame_bytes = 2 * self._samples.shape[1]
        if self._pending:
            took = int(io.write(self._pending))
            self._pending = self._pending[max(took, 0):]
            self.fed += max(took, 0)
            free_bytes -= max(took, 0)
            if self._pending:
                return
        while free_bytes >= frame_bytes and not self.finished:
            n = min(free_bytes // frame_bytes, self.MAX_FRAMES)
            chunk, self._pos = loopplayback.render(
                self._samples, self._pos, n, self._loop_start, self._loop_end)
            if len(chunk) < n:
                self.finished = True
            if len(chunk) == 0:
                break
            data = loopplayback.to_pcm16(chunk)
            data = data.tobytes() if hasattr(data, "tobytes") else bytes(data)
            took = max(int(io.write(data)), 0)
            self.feeds += 1
            self.fed += took
            free_bytes -= len(data)
            if took < len(data):
                self._pending = data[took:]
                return


# --------------------------------------------------------------------------
# the waveform widget
# --------------------------------------------------------------------------

class WaveformView(QWidget):
    hovered = Signal(int)                 # sample under the mouse, -1 when none
    seek_requested = Signal(int)          # the user clicked here
    view_changed = Signal()               # zoom or scroll position changed
    marker_dragged = Signal(str, int)     # "start" or "end" moved to this sample (while the button is down)
    marker_released = Signal()            # the mouse button was let go after dragging a marker

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pyr = None
        self._rate = 44100
        self._loop = None
        self._start = 0.0
        self._span = 1.0
        self._cursor = None
        self._hover = -1
        self._editable = False
        self._drag = None                 # "start" / "end" while a marker is being dragged
        self._message = "Reading the sound..."

    # ----- data
    def release(self) -> None:
        """Drop the waveform data (the window is closed)."""
        self._pyr = None
        self._loop = None
        self._cursor = None
        self.update()

    def set_editable(self, on: bool) -> None:
        self._editable = bool(on)

    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    def set_audio(self, pyr, rate: int) -> None:
        self._pyr, self._rate = pyr, rate
        self._start, self._span = 0.0, float(max(pyr.frames, 1))
        self._cursor = None
        self.update()
        self.view_changed.emit()

    def set_loop(self, loop) -> None:
        self._loop = loop
        self.update()

    def set_cursor(self, frame) -> None:
        if frame != self._cursor:
            self._cursor = frame
            self.update()

    # ----- view
    def total(self) -> int:
        return self._pyr.frames if self._pyr is not None else 0

    def view_range(self) -> tuple[float, float]:
        return self._start, self._span

    def _min_span(self) -> float:
        return max(16.0, self.width() / 8.0)            # at most 8 pixels per sample

    def set_view(self, start: float, span: float) -> None:
        total = self.total()
        if total <= 0:
            return
        span = min(max(span, min(self._min_span(), total)), total)
        start = min(max(start, 0.0), total - span)
        if start != self._start or span != self._span:
            self._start, self._span = start, span
            self.update()
            self.view_changed.emit()

    def fit(self) -> None:
        self.set_view(0, self.total())

    def zoom(self, factor: float, anchor=None) -> None:
        if self._span <= 0 or self.total() <= 0:
            return
        if anchor is None:
            anchor = self._start + self._span / 2
        new_span = min(max(self._span * factor, min(self._min_span(), self.total())), self.total())
        ratio = new_span / self._span
        self.set_view(anchor - (anchor - self._start) * ratio, new_span)

    def x_of(self, frame: float) -> float:
        return (frame - self._start) / self._span * self.width()

    def frame_at(self, x: float) -> int:
        f = int(self._start + x / max(self.width(), 1) * self._span)
        return min(max(f, 0), max(self.total() - 1, 0))

    # ----- mouse
    def _marker_at(self, x: float):
        """"start" or "end" when the mouse is on a loop marker, else None."""
        loop = self._loop
        if not self._editable or loop is None or loop.status == loopmath.NONE:
            return None
        d_start, d_end = abs(x - self.x_of(loop.start)), abs(x - self.x_of(loop.end))
        if min(d_start, d_end) > _GRAB_PX:
            return None
        return "start" if d_start <= d_end else "end"

    def _frame_edge(self, x: float) -> int:
        """Sample under x, where the end of the file counts too (a loop may end exactly there)."""
        f = int(round(self._start + x / max(self.width(), 1) * self._span))
        return min(max(f, 0), self.total())

    def mouseMoveEvent(self, ev):
        if self._pyr is None:
            return
        x = ev.position().x()
        if self._drag:
            self.marker_dragged.emit(self._drag, self._frame_edge(x))
        else:
            self.setCursor(Qt.SizeHorCursor if self._marker_at(x) else Qt.ArrowCursor)
        self._hover = self.frame_at(x)
        self.hovered.emit(self._hover)
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag:
            self._drag = None
            self.marker_released.emit()

    def leaveEvent(self, ev):
        self._hover = -1
        self.hovered.emit(-1)
        self.update()

    def mousePressEvent(self, ev):
        if self._pyr is not None and ev.button() == Qt.LeftButton:
            which = self._marker_at(ev.position().x())
            if which:
                self._drag = which
                return
            self.seek_requested.emit(self.frame_at(ev.position().x()))

    def wheelEvent(self, ev):
        if self._pyr is None:
            return
        dy, dx = ev.angleDelta().y(), ev.angleDelta().x()
        if ev.modifiers() & Qt.ShiftModifier or (dx and not dy):
            step = self._span * 0.1 * (1 if (dy or dx) < 0 else -1)
            self.set_view(self._start + step, self._span)
        elif dy:
            anchor = self._start + ev.position().x() / max(self.width(), 1) * self._span
            self.zoom(0.8 if dy > 0 else 1.25, anchor)
        ev.accept()

    # ----- painting
    def paintEvent(self, ev):
        p = QPainter(self)
        pal = self.palette()
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        text_color = pal.color(QPalette.ColorRole.Text)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
        p.setFont(small)
        if self._pyr is None or self.total() == 0:
            p.setPen(text_color)
            p.drawText(self.rect(), Qt.AlignCenter, self._message)
            p.end()
            return
        wave_top = _RULER_H + _BAR_H + 2
        wave_h = max(h - wave_top - 2, 10)
        mid = wave_top + wave_h / 2
        self._paint_loop_region(p, w, wave_top, wave_h)
        self._paint_wave(p, w, wave_top, wave_h, mid, pal)
        self._paint_ruler(p, w, text_color)
        self._paint_loop_bar(p, w)
        if self._hover >= 0:
            x = self.x_of(self._hover)
            p.setPen(QPen(QColor(128, 128, 128, 160), 1))
            p.drawLine(QPointF(x, _RULER_H), QPointF(x, h))
        if self._cursor is not None:
            x = self.x_of(self._cursor)
            if -2 <= x <= w + 2:
                p.setPen(QPen(_CURSOR_COLOR, 2))
                p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.end()

    def _paint_loop_region(self, p, w, top, height):
        loop = self._loop
        if loop is None or loop.status == loopmath.NONE:
            return
        x0, x1 = self.x_of(loop.start), self.x_of(loop.end)
        x0, x1 = max(x0, -1.0), min(x1, w + 1.0)
        if x1 > x0:
            fill = QColor(_LOOP_COLOR)
            fill.setAlpha(40)
            p.fillRect(QRectF(x0, top, x1 - x0, height), fill)

    def _paint_wave(self, p, w, top, height, mid, pal):
        waveform, _ = _np()
        start, span = self._start, self._span
        scale = height / 2 * 0.95
        color = pal.color(QPalette.ColorRole.Highlight)
        p.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 1))
        p.drawLine(0, int(mid), w, int(mid))
        spp = span / max(w, 1)
        if spp < 1.5:                                        # a line through the single samples
            frames, vals = waveform.samples_in_view(self._pyr, start, start + span)
            if len(frames):
                pts = [QPointF(self.x_of(f), mid - v * scale) for f, v in zip(frames.tolist(), vals.tolist())]
                p.setPen(QPen(color, 1.5))
                p.drawPolyline(QPolygonF(pts))
                if spp < 0.25:
                    p.setBrush(color)
                    for pt in pts:
                        p.drawEllipse(pt, 2.5, 2.5)
            return
        lo, hi = waveform.peaks(self._pyr, start, start + span, w)
        y_top = (mid - hi * scale).tolist()
        y_bot = (mid - lo * scale).tolist()
        p.setPen(QPen(color, 1))
        for x, (a, b) in enumerate(zip(y_top, y_bot)):
            if lo[x] == 0 and hi[x] == 0:
                continue                                     # silence or outside the audio
            p.drawLine(QPointF(x + 0.5, a), QPointF(x + 0.5, max(b, a + 1)))

    def _paint_ruler(self, p, w, text_color):
        rate, start, span = self._rate, self._start, self._span
        step = loopmath.nice_tick_seconds(span / rate, w)
        k0 = math.ceil(start / rate / step)
        k1 = math.floor((start + span) / rate / step)
        p.setPen(QPen(text_color, 1))
        if k1 - k0 > 400:
            return
        for k in range(k0, k1 + 1):
            x = self.x_of(k * step * rate)
            p.drawLine(QPointF(x, _RULER_H - 6), QPointF(x, _RULER_H))
            label = loopmath.format_time(int(round(k * step * rate)), rate)
            if step >= 1:
                label = label[:-4]                           # whole seconds: no ".000"
            p.drawText(QPointF(x + 3, _RULER_H - 8), label)

    def _paint_loop_bar(self, p, w):
        loop = self._loop
        if loop is None:
            return
        whole = loop.status == loopmath.NONE
        x0, x1 = self.x_of(loop.start), self.x_of(loop.end)
        rect = QRectF(max(x0, -1.0), _RULER_H, min(x1, w + 1.0) - max(x0, -1.0), _BAR_H)
        if rect.width() <= 0:
            return
        color = _NONE_COLOR if whole else _LOOP_COLOR
        p.fillRect(rect, color)
        p.save()
        p.setClipRect(rect)
        p.setPen(QColor(0, 0, 0))
        if whole:
            p.drawText(rect.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       "no loop tags: the whole file repeats")
        else:
            if rect.width() >= 130:
                p.drawText(rect.adjusted(6, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           f"LOOPSTART {loop.start}")
            if rect.width() >= 330:
                p.drawText(rect, Qt.AlignCenter, f"LOOPLENGTH {loop.length}")
        p.restore()
        if not whole:
            p.setPen(QPen(_LOOP_COLOR, 2))
            for x in (x0, x1):
                if 0 <= x <= w:
                    p.drawLine(QPointF(x, _RULER_H), QPointF(x, self.height()))
            if self._editable:                               # a grip on each marker: they can be dragged
                p.setBrush(_LOOP_COLOR)
                p.setPen(QPen(QColor(0, 0, 0), 1))
                for x in (x0, x1):
                    if -_GRAB_PX <= x <= w + _GRAB_PX:
                        p.drawRect(QRectF(x - 4, _RULER_H + 1, 8, _BAR_H - 2))


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

class LoopEditorDialog(QDialog):
    def __init__(self, path: Path, title: str, info: oggmeta.OggInfo, parent=None,
                 game=None, on_saved=None):
        super().__init__(parent)
        self.setWindowTitle(f"Loop editor - {title}")
        self.resize(1000, 560)
        self._path = Path(path)
        self._info = info
        self._tags = oggmeta.loop_tags(info)
        try:
            self._engine = enginetags.read_engine_tags_from_file(self._path)
        except OSError:
            self._engine = enginetags.EngineTags(info.sample_rate, self._tags.start or 0,
                                                 self._tags.length or 0)
        self._audio = None
        self._loop = None
        self._sink = None
        self._io = None
        self._dev = None                  # the _Feeder of the current play run
        self._sink_error = ""
        self._sink_fmt = None             # (rate, channels) the current sink was made for
        self._run_loop = (0, None)        # loop settings of the current play run
        self._origin = 0                  # where the current play run started
        self._head = 0                    # where the cursor is while stopped or paused
        self._shut = False
        self.ready = False
        self.error = ""
        self._game = Path(game) if game else None      # the open project: needed to save tags
        self._on_saved = on_saved                     # called after the tags were written
        self._saved = None                            # (start, length) the file has now, None = no loop
        self._edit = None                             # (start, length) being edited, None = not edited
        self._discard = False

        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{escape(title)}</b><br>{escape(self._path.name)} &nbsp;-&nbsp; "
                      f"{info.sample_rate} Hz &nbsp;-&nbsp; "
                      f"{'stereo' if info.channels == 2 else str(info.channels) + ' channel(s)'}")
        head.setTextFormat(Qt.RichText)
        lay.addWidget(head)

        self.view = WaveformView()
        self.view.hovered.connect(self._on_hover)
        self.view.seek_requested.connect(self._on_seek)
        self.view.view_changed.connect(self._sync_scroll)
        self.view.marker_dragged.connect(self._on_marker_dragged)
        self.view.marker_released.connect(self._on_marker_released)
        lay.addWidget(self.view, stretch=1)
        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.valueChanged.connect(self._on_scroll)
        lay.addWidget(self.scroll)

        row = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_play.setToolTip("Play from the cursor, or pause.")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setToolTip("Stop and put the cursor back where playing started.")
        self.btn_stop.clicked.connect(self.stop)
        self.btn_from_loop = QPushButton("From loop start")
        self.btn_from_loop.clicked.connect(self.play_from_loop_start)
        self.btn_seam = QPushButton("Hear the seam")
        self.btn_seam.setToolTip("Play the last 3 seconds before the loop end, then the jump back to the loop start.")
        self.btn_seam.clicked.connect(self.play_seam)
        self.chk_loop = QCheckBox("Loop like the game")
        self.chk_loop.setChecked(True)
        self.chk_loop.toggled.connect(self._on_loop_toggled)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(self._on_volume)
        for wdg in (self.btn_play, self.btn_stop, self.btn_from_loop, self.btn_seam, self.chk_loop):
            row.addWidget(wdg)
        row.addWidget(QLabel("Volume"))
        row.addWidget(self.volume)
        row.addStretch(1)
        self.zoom_btns = []
        for text, tip, fn in (("-", "Zoom out", lambda: self.view.zoom(1.5)),
                              ("+", "Zoom in", lambda: self.view.zoom(1 / 1.5)),
                              ("Fit", "Show the whole file", self.view.fit),
                              ("Zoom to loop", "Zoom to the loop", self._zoom_to_loop)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
            self.zoom_btns.append(b)
        lay.addLayout(row)

        grid = QGridLayout()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._vals = {}
        names = [("LOOPSTART", "LOOPSTART"), ("LOOPLENGTH", "LOOPLENGTH"), ("plays", "Loop plays"),
                 ("length", "File length"), ("cursor", "Cursor"), ("mouse", "Under the mouse")]
        for i, (key, label) in enumerate(names):
            r, c = divmod(i, 2)
            grid.addWidget(QLabel(label + ":"), r, c * 2)
            v = QLabel("-")
            v.setFont(mono)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(v, r, c * 2 + 1)
            self._vals[key] = v
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)

        edit = QHBoxLayout()
        self.ed = {}
        for key, label, tip in (
                ("start", "Loop start", "LOOPSTART. Samples (132300), seconds (3 s or 3.5) or minutes:seconds (1:23.5)."),
                ("length", "Length", "LOOPLENGTH. Changing it moves the loop end."),
                ("end", "Loop end", "Where the loop jumps back (start + length). Changing it changes the length.")):
            edit.addWidget(QLabel(label + ":"))
            box = QLineEdit()
            box.setFont(mono)
            box.setFixedWidth(120)
            box.setToolTip(tip)
            box.editingFinished.connect(lambda k=key: self._on_field(k))
            edit.addWidget(box)
            self.ed[key] = box
        self.btn_set_start = QPushButton("Start = cursor")
        self.btn_set_start.setToolTip("Put the loop start at the cursor (click the wave to place the cursor).")
        self.btn_set_start.clicked.connect(lambda: self._set_from_cursor("start"))
        self.btn_set_end = QPushButton("End = cursor")
        self.btn_set_end.setToolTip("Put the loop end at the cursor.")
        self.btn_set_end.clicked.connect(lambda: self._set_from_cursor("end"))
        self.chk_snap = QCheckBox("Snap to zero crossing")
        self.chk_snap.setChecked(True)
        self.chk_snap.setToolTip("When you drag a marker or use Start/End = cursor, move it to the nearest "
                                 "place where the sound crosses zero (within 20 ms). Jumping between such "
                                 "places does not click. Typed values are always used exactly.")
        self.btn_revert = QPushButton("Revert")
        self.btn_revert.setToolTip("Go back to the loop stored in the file.")
        self.btn_revert.clicked.connect(lambda _checked=False: self.revert())
        self.btn_save = QPushButton("Save tags...")
        self.btn_save.setToolTip("Write LOOPSTART and LOOPLENGTH into the file. A backup is made first "
                                 "and Undo in the main window can restore it.")
        self.btn_save.clicked.connect(lambda _checked=False: self.save_tags())
        for wdg in (self.btn_set_start, self.btn_set_end, self.chk_snap):
            edit.addWidget(wdg)
        edit.addStretch(1)
        edit.addWidget(self.btn_revert)
        edit.addWidget(self.btn_save)
        lay.addLayout(edit)

        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        lay.addWidget(self.notes)
        self.hint = QLabel(self._HINT)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        self._set_controls_enabled(False)
        self._show_tag_values()
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        self._job = _DecodeJob(self._path)
        self._job.done.connect(self._on_decoded)
        self._job.failed.connect(self._on_failed)
        self._job.start()

    _HINT = ("Mouse wheel: zoom   Shift + wheel: scroll   Click: move the cursor   Drag the orange "
             "markers: move the loop. Nothing is written to the file until you press Save tags.")

    # ----- loading
    @Slot(object, object)
    def _on_decoded(self, audio, pyr):
        if self._shut:
            return
        if audio.frames == 0:
            self._on_failed("The file contains no audio.")
            return
        self._audio = audio
        self._loop = loopmath.resolve_loop(self._engine, audio.frames, self._tags)
        self.view.set_audio(pyr, audio.sample_rate)
        self.view.set_loop(self._loop)
        self.ready = True
        self._head = 0
        self.view.set_cursor(0)
        self._show_tag_values()
        notes = list(self._tags.notes) + list(self._loop.notes)
        if audio.dropped_channels:
            notes.append(f"The file has more than {audiodecode.MAX_CHANNELS} channels; "
                         "only the first two are shown and played.")
        self.notes.setText("\n".join(notes))
        self._saved = self._saved_pair()
        self.view.set_editable(True)
        self._set_controls_enabled(True)
        self._refresh_edit_ui()
        self._sync_scroll()

    @Slot(str)
    def _on_failed(self, message: str):
        if self._shut:
            return
        self.error = message
        self.view.set_message(message)
        self.notes.setText(message)

    def _set_controls_enabled(self, on: bool) -> None:
        for wdg in (self.btn_play, self.btn_stop, self.btn_from_loop, self.btn_seam,
                    self.chk_loop, self.volume, self.scroll, *self.zoom_btns,
                    self.btn_set_start, self.btn_set_end, self.chk_snap, *self.ed.values()):
            wdg.setEnabled(on)
        if not on:
            self.btn_save.setEnabled(False)
            self.btn_revert.setEnabled(False)

    def _show_tag_values(self) -> None:
        rate = self._info.sample_rate
        t = self._tags

        def fmt(v):
            return "not set" if v is None else f"{v}  ({loopmath.format_time(v, rate)})"
        self._vals["LOOPSTART"].setText(fmt(t.start))
        self._vals["LOOPLENGTH"].setText(fmt(t.length))
        if self._audio is not None:
            n = self._audio.frames
            self._vals["length"].setText(f"{n}  ({loopmath.format_time(n, rate)})")
            lp = self._loop
            self._vals["plays"].setText(
                f"{lp.start} to {lp.end}  ({loopmath.format_time(lp.start, rate)} to "
                f"{loopmath.format_time(lp.end, rate)})")
            self._vals["cursor"].setText(self._fmt_pos(self._head))

    def _fmt_pos(self, frame: int) -> str:
        return f"{frame}  ({loopmath.format_time(frame, self._info.sample_rate)})"

    # ----- editing the loop
    def _saved_pair(self):
        """(start, length) of the loop the file holds for the game, None when it has no usable loop."""
        lp = self._loop
        return (lp.start, lp.length) if lp is not None and lp.status == loopmath.COMPLETE else None

    def loop_points(self):
        """(start, end) of the loop now shown and played (the edited one, or the file's own)."""
        return (self._loop.start, self._loop.end) if self._loop is not None else (0, 0)

    @property
    def dirty(self) -> bool:
        return self._edit is not None and self._edit != self._saved

    def set_loop_points(self, start: int, end: int) -> bool:
        """Use this loop (samples). Returns False and explains when it does not fit the file."""
        if not self.ready:
            return False
        total = self._audio.frames
        if not (0 <= start < end <= total):
            self._problem(f"A loop must lie inside the file: from 0 to {total} samples, "
                          "and it must end after it starts.")
            return False
        self._edit = (start, end - start)
        self._loop = loopmath.LoopView(start, end, loopmath.COMPLETE, ())
        self.view.set_loop(self._loop)
        self._refresh_edit_ui()
        self._show_tag_values()
        return True

    def _problem(self, text: str) -> None:
        self.hint.setText(f"<span style='color:#c0392b'>{escape(text)}</span>")
        self.hint.setTextFormat(Qt.RichText)

    def _snap(self, frame: int) -> int:
        if not self.chk_snap.isChecked() or self._audio is None:
            return frame
        return loopedit.snap_to_zero_crossing(
            self._audio.samples, frame, int(self._audio.sample_rate * _SNAP_SECONDS))

    def _current_or_whole(self):
        """The loop being edited; for a file without a loop, the whole file."""
        if self._loop is not None and self._loop.status != loopmath.NONE:
            return self._loop.start, self._loop.end
        return 0, self._audio.frames

    def _move_marker(self, which: str, frame: int) -> bool:
        s, e = self._current_or_whole()
        total = self._audio.frames
        if which == "start":
            s = max(0, min(frame, e - 1))
        else:
            e = min(total, max(frame, s + 1))
        return self.set_loop_points(s, e)

    def _on_marker_dragged(self, which: str, frame: int) -> None:
        if self.ready:
            self.hint.setTextFormat(Qt.PlainText)
            self.hint.setText(self._HINT)
            self._move_marker(which, self._snap(frame))

    def _on_marker_released(self) -> None:
        self._restart_if_playing()

    def _set_from_cursor(self, which: str) -> None:
        if self.ready and self._move_marker(which, self._snap(self._head)):
            self._restart_if_playing()

    def _on_field(self, key: str) -> None:
        """A typed value was accepted (Enter, or leaving the box)."""
        if not self.ready:
            return
        try:
            self._apply_field(key)
        finally:
            self._refresh_edit_ui(force=True)                # show the numbers as samples again

    def _apply_field(self, key: str) -> None:
        s, e = self._current_or_whole()
        try:
            value = loopedit.parse_position(self.ed[key].text(), self._info.sample_rate)
        except ValueError as ex:
            self._problem(str(ex))
            return
        if self._edit is None and self._loop.status != loopmath.NONE and \
                value == {"start": s, "end": e, "length": e - s}[key]:
            return                                           # nothing changed
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setText(self._HINT)
        if key == "start":
            s = value
        elif key == "end":
            e = value
        else:
            e = s + value
        if self.set_loop_points(s, e):
            self._restart_if_playing()

    def revert(self) -> None:
        """Back to the loop stored in the file."""
        if not self.ready:
            return
        self._edit = None
        self._loop = loopmath.resolve_loop(self._engine, self._audio.frames, self._tags)
        self.view.set_loop(self._loop)
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setText(self._HINT)
        self._refresh_edit_ui()
        self._show_tag_values()
        self._restart_if_playing()

    def _restart_if_playing(self) -> None:
        if self._sink is not None:                          # carry on from here with the new loop
            self._head = self._current_head()
            self._start_sink(self._head)

    def _refresh_edit_ui(self, force: bool = False) -> None:
        rate = self._info.sample_rate
        if self._loop is None:
            return
        s, e = self._current_or_whole()
        whole = self._loop.status == loopmath.NONE and self._edit is None
        values = {"start": "" if whole else str(s), "length": "" if whole else str(e - s),
                  "end": "" if whole else str(e)}
        for key, box in self.ed.items():
            if force or not box.hasFocus():
                box.setText(values[key])
            box.setPlaceholderText("no loop" if whole else "")
        self.btn_revert.setEnabled(self.dirty)
        self.btn_save.setEnabled(self.dirty and self._game is not None)
        if self.dirty and self._game is None:
            self.btn_save.setToolTip("Open the loop editor from a project to save tags.")
        times = ("" if whole else f"start {loopmath.format_time(s, rate)}   end {loopmath.format_time(e, rate)}   "
                 f"length {loopmath.format_time(e - s, rate)}")
        self.ed["end"].setToolTip(times or "Where the loop jumps back (start + length).")

    def save_tags(self, ask: bool = True) -> bool:
        """Write the edited loop into the file (backup first). Returns True when it was saved."""
        if not self.ready or not self.dirty:
            return False
        if self._game is None:
            QMessageBox.information(self, "Save tags", "Open the loop editor from a project to save tags.")
            return False
        start, length = self._edit
        rate = self._info.sample_rate
        old = self._saved
        if ask:
            old_txt = "none (the whole file repeats)" if old is None else \
                f"LOOPSTART {old[0]}  ({loopmath.format_time(old[0], rate)}),  LOOPLENGTH {old[1]}"
            answer = QMessageBox.question(
                self, "Save loop tags",
                f"Write these tags into {self._path.name}?\n\n"
                f"Now in the file:  {old_txt}\n"
                f"New:  LOOPSTART {start}  ({loopmath.format_time(start, rate)}),  LOOPLENGTH {length}\n\n"
                "The sound itself is not changed. A backup is made first, and Undo in the main window "
                "restores it. Close RPG Maker MZ and the game before saving.")
            if answer != QMessageBox.Yes:
                return False
        was_playing = self._sink is not None
        try:
            loopwrite.save_loop_tags(self._game, self._path, start, length, lambda *_: None)
        except oggwrite.OggWriteError as e:
            QMessageBox.warning(self, "Save loop tags", str(e))
            return False
        except OSError as e:
            QMessageBox.warning(self, "Save loop tags", f"The file could not be saved: {e.strerror or e}")
            return False
        self._reload_tags()
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setText("Saved. Undo in the main window puts the old tags back.")
        if self._on_saved:
            self._on_saved()
        if was_playing:
            self._restart_if_playing()
        return True

    def _reload_tags(self) -> None:
        """Read the file again after saving, the way the game will."""
        self._info = oggmeta.read_ogg_info(self._path)
        self._tags = oggmeta.loop_tags(self._info)
        self._engine = enginetags.read_engine_tags_from_file(self._path)
        self._edit = None
        self._loop = loopmath.resolve_loop(self._engine, self._audio.frames, self._tags)
        self._saved = self._saved_pair()
        self.view.set_loop(self._loop)
        notes = list(self._tags.notes) + list(self._loop.notes)
        self.notes.setText("\n".join(notes))
        self._refresh_edit_ui()
        self._show_tag_values()

    def discard_changes(self) -> None:
        """Forget unsaved edits (so closing the window does not ask)."""
        self._edit = None
        self._discard = True

    def _confirm_close(self) -> bool:
        if not self.dirty or self._discard:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved loop")
        box.setText("The loop has changes that are not saved. Save the tags before closing?")
        save = box.addButton("Save tags", QMessageBox.ButtonRole.AcceptRole)
        drop = box.addButton("Discard changes", QMessageBox.ButtonRole.DestructiveRole)
        stay = box.addButton("Keep editing", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return self.save_tags(ask=False)
        return clicked is drop

    # ----- view helpers
    def _zoom_to_loop(self) -> None:
        if self._loop is None:
            return
        n = max(self._loop.length, 1)
        self.view.set_view(self._loop.start - n * 0.15, n * 1.3)

    def _sync_scroll(self) -> None:
        total = self.view.total()
        start, span = self.view.view_range()
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, max(int(total - span), 0))
        self.scroll.setPageStep(max(int(span), 1))
        self.scroll.setSingleStep(max(int(span / 10), 1))
        self.scroll.setValue(int(start))
        self.scroll.blockSignals(False)

    def _on_scroll(self, value: int) -> None:
        self.view.set_view(float(value), self.view.view_range()[1])

    def _on_hover(self, frame: int) -> None:
        self._vals["mouse"].setText("-" if frame < 0 else self._fmt_pos(frame))

    # ----- playing
    def is_playing(self) -> bool:
        """True while a play run is going (a play-once run counts until its last sample is out)."""
        return self._sink is not None and self._sink.state() != QAudio.State.StoppedState

    def playback_report(self) -> str:
        """One line of facts about the sound output, for error messages and bug reports."""
        if self._sink is None:
            return f"no sink ({self._sink_error or 'not started'})"
        d = self._dev
        try:
            err = self._sink.error().name
            dev = QMediaDevices.defaultAudioOutput().description()
            return (f"state={self._sink.state().name} error={err} device='{dev}' "
                    f"buffer={self._sink.bufferSize()}B free={self._sink.bytesFree()}B "
                    f"processed={self._sink.processedUSecs()}us fed={d.fed}B in {d.feeds} writes "
                    f"finished={d.finished}")
        except RuntimeError as e:
            return f"sink gone ({e})"

    def _feed(self) -> None:
        if self._sink is None or self._io is None or self._dev is None:
            return
        self._dev.fill(self._io, self._sink.bytesFree())

    def _loop_args(self):
        if self.chk_loop.isChecked():
            return self._loop.start, self._loop.end
        return 0, None

    def _say(self, text: str) -> None:
        self.hint.setText(text)

    def _start_sink(self, pos: int) -> bool:
        audio = self._audio
        fmt_key = (audio.sample_rate, audio.channels)
        if self._sink is not None and self._sink_fmt == fmt_key:
            # Moving the cursor or changing the loop while playing: keep the same sink and only
            # drop what is still waiting in its buffer. A second sink would play that buffer
            # (about 0.4 s) on top of the new sound, which is heard as a short burst of loudness.
            self._timer.stop()
            self._sink.reset()
            return self._begin_run(pos)
        self._stop_sink()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self._say("No sound output device was found, so nothing can be played.")
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(audio.sample_rate)
        fmt.setChannelCount(audio.channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            self._say(f"Your sound device does not accept {audio.sample_rate} Hz, "
                      f"{audio.channels} channel audio. Change the device's format in the system "
                      "sound settings and try again.")
            return False
        self._sink = QAudioSink(device, fmt, self)
        self._sink.setVolume(self.volume.value() / 100.0)
        self._sink.setBufferSize(int(audio.sample_rate * audio.channels * 2 * 0.4))     # about 0.4 s
        self._sink_fmt = fmt_key
        return self._begin_run(pos)

    def _begin_run(self, pos: int) -> bool:
        """Start feeding the (new or reset) sink from sample `pos`."""
        audio = self._audio
        self._dev = _Feeder(audio.samples)
        ls, le = self._loop_args()
        self._run_loop = (ls, le)          # the settings this run really plays with
        self._dev.configure(pos, ls, le)
        self._origin = pos
        self._io = self._sink.start()
        if self._io is None or self._sink.state() == QAudio.State.StoppedState:
            self._sink_failed("the sound device did not start")
            return False
        self._sink_error = ""
        self._feed()
        self._timer.start()
        self._say("Mouse wheel: zoom   Shift + wheel: scroll   Click: move the cursor. "
                  "This window only shows and plays; it does not change the file.")
        self.btn_play.setText("Pause")
        return True

    def _sink_failed(self, what: str) -> None:
        """Say why playing failed (the error is read before the sink is thrown away)."""
        err = self._sink.error().name if self._sink is not None else "?"
        self._sink_error = f"{what} (error: {err})"
        self._stop_sink()
        self._say("Playing stopped: " + self._sink_error + ". Check the sound device in the system "
                  "settings and press Play again.")

    def _stop_sink(self) -> None:
        self._timer.stop()
        if self._sink is not None:
            self._sink.reset()                  # nothing left in the buffer to play after it is gone
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
            self._sink_fmt = None
        self._io = None
        self._dev = None
        self.btn_play.setText("Play")

    def _current_head(self) -> int:
        """Where the sound is right now (the cursor position)."""
        if self._sink is None:
            return self._head
        _, loopplayback = _np()
        rate = self._audio.sample_rate
        played = int(self._sink.processedUSecs() * rate / 1_000_000)
        ls, le = self._run_loop
        return loopplayback.advance(self._origin, played, self._audio.frames, ls, le)

    def _tick(self) -> None:
        if self._sink is None:
            return
        if self._sink.state() == QAudio.State.StoppedState:       # the device gave up while playing
            self._head = min(self._current_head(), self._audio.frames - 1)
            self._sink_failed("the sound device stopped")
            self.view.set_cursor(self._head)
            return
        self._feed()
        if self._dev.finished and not self._dev._pending and self._sink.state() == QAudio.State.IdleState:
            self._head = self._audio.frames - 1
            self._stop_sink()
            self.view.set_cursor(self._head)
            self._vals["cursor"].setText(self._fmt_pos(self._head))
            return
        self._head = min(self._current_head(), self._audio.frames - 1)
        self.view.set_cursor(self._head)
        self._vals["cursor"].setText(self._fmt_pos(self._head))
        self._keep_cursor_visible()

    def _keep_cursor_visible(self) -> None:
        start, span = self.view.view_range()
        if span >= self.view.total():
            return
        if not (start <= self._head <= start + span):
            self.view.set_view(self._head - span * 0.1, span)

    def toggle_play(self) -> None:
        if not self.ready:
            return
        if self._sink is not None:
            self._head = self._current_head()                  # pause here
            self._stop_sink()
            self.view.set_cursor(self._head)
        else:
            self._start_sink(self._head)

    def play(self, pos=None) -> bool:
        if not self.ready:
            return False
        if pos is not None:
            self._head = max(0, min(int(pos), self._audio.frames - 1))
            self.view.set_cursor(self._head)
        return self._start_sink(self._head)

    def stop(self) -> None:
        if not self.ready:
            return
        if self._sink is not None:
            self._stop_sink()
        self._head = self._origin                              # also after a pause: back to where it started
        self.view.set_cursor(self._head)
        self._vals["cursor"].setText(self._fmt_pos(self._head))

    def play_from_loop_start(self) -> None:
        if self.ready:
            self.play(self._loop.start)

    def play_seam(self) -> None:
        if not self.ready:
            return
        self.chk_loop.blockSignals(True)
        self.chk_loop.setChecked(True)
        self.chk_loop.blockSignals(False)
        pos = loopmath.seam_start(self._loop, self._audio.sample_rate)
        self.play(pos)
        self._keep_cursor_visible()

    def _on_seek(self, frame: int) -> None:
        if not self.ready:
            return
        was_playing = self._sink is not None
        self._head = frame
        self.view.set_cursor(frame)
        self._vals["cursor"].setText(self._fmt_pos(frame))
        if was_playing:
            self._start_sink(frame)

    def _on_loop_toggled(self, _on: bool) -> None:
        if self._sink is not None:                              # carry on from here with the new setting
            self._head = self._current_head()
            self._start_sink(self._head)

    def _on_volume(self, value: int) -> None:
        if self._sink is not None:
            self._sink.setVolume(value / 100.0)

    # ----- closing
    def _shutdown(self) -> None:
        self._shut = True                 # a late decode result is ignored from now on
        self._stop_sink()
        self.ready = False
        self._audio = None                # a closed window is only hidden; do not keep the song in memory
        self.view.release()

    def reject(self) -> None:
        if self._confirm_close():
            super().reject()

    def done(self, result: int) -> None:
        self._shutdown()
        super().done(result)

    def closeEvent(self, ev) -> None:
        if not self._confirm_close():
            ev.ignore()
            return
        self._shutdown()
        super().closeEvent(ev)


# --------------------------------------------------------------------------
# entry point used by the main window
# --------------------------------------------------------------------------

def open_loop_editor(path, title: str, parent=None, game=None, on_saved=None):
    """Open the loop editor for an .ogg file. Returns the window, or None after explaining
    to the user why it cannot be opened."""
    path = Path(path)
    if path.suffix.lower() != ".ogg":
        QMessageBox.information(parent, "Loop editor",
                                f"The loop editor works on Ogg Vorbis (.ogg) files.\n\n"
                                f"'{path.name}' is a {path.suffix or 'unknown'} file.")
        return None
    missing = audiodecode.missing_dependencies()
    if missing:
        QMessageBox.information(parent, "Loop editor", audiodecode.missing_message(missing))
        return None
    try:
        info = oggmeta.read_ogg_info(path)
    except oggmeta.OggError as e:
        QMessageBox.warning(parent, "Loop editor", f"'{path.name}' cannot be opened.\n\n{e}")
        return None
    dlg = LoopEditorDialog(path, title, info, parent, game=game, on_saved=on_saved)
    dlg.show()
    return dlg

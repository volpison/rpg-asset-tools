"""
audio_preview.py
----------------
RPG Maker–style audio preview popup.

If QtMultimedia cannot decode the file (common for .ogg on Windows),
offer to open it in the system default external player.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QMessageBox, QGroupBox, QGridLayout,
)

AUDIO_SUFFIXES = {".ogg", ".m4a", ".wav", ".mp3", ".aac", ".flac"}


def is_audio_path(path: Path | str | None) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in AUDIO_SUFFIXES


def resolve_audio_path(game_folder: Path, asset: dict) -> Path | None:
    if not asset or not game_folder:
        return None
    rel = asset.get("path")
    if not rel:
        return None
    p = Path(game_folder) / rel
    if p.is_file() and is_audio_path(p):
        return p
    if p.suffix:
        parent = p.parent
        stem = p.stem
        for ext in (".ogg", ".m4a", ".wav", ".mp3"):
            cand = parent / (stem + ext)
            if cand.is_file():
                return cand
    return None


def open_in_external_player(path: Path, parent=None, ask: bool = True) -> bool:
    """
    Open audio with the OS default app.
    Returns True if the request was sent to the OS.
    """
    path = Path(path)
    if ask:
        ret = QMessageBox.question(
            parent,
            "Open external player",
            "This audio file will be opened by an external application "
            "(the default player registered in your system).\n\n"
            f"{path}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret != QMessageBox.Yes:
            return False
    ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
    return bool(ok)


def _fmt_ms(ms: int) -> str:
    if ms < 0:
        ms = 0
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class AudioPreviewDialog(QDialog):
    def __init__(self, audio_path: Path, title: str = "Audio preview", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(500, 320)
        self.setModal(False)
        self._path = Path(audio_path)
        self._player = None
        self._audio_out = None
        self._dragging_seek = False
        self._qt_ok = False

        layout = QVBoxLayout(self)
        self.path_label = QLabel(str(audio_path))
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        transport = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        self.btn_play.clicked.connect(self._play)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_stop.clicked.connect(self._stop)
        transport.addWidget(self.btn_play)
        transport.addWidget(self.btn_pause)
        transport.addWidget(self.btn_stop)
        transport.addStretch()
        layout.addLayout(transport)

        seek_row = QHBoxLayout()
        self.lbl_pos = QLabel("0:00")
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_dragging_seek", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek.sliderMoved.connect(self._seek_moved)
        self.lbl_dur = QLabel("0:00")
        seek_row.addWidget(self.lbl_pos)
        seek_row.addWidget(self.seek, stretch=1)
        seek_row.addWidget(self.lbl_dur)
        layout.addLayout(seek_row)

        box = QGroupBox("Playback (RPG Maker style)")
        grid = QGridLayout(box)

        grid.addWidget(QLabel("Volume"), 0, 0)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(90)
        self.lbl_vol = QLabel("90%")
        self.vol.valueChanged.connect(self._on_volume)
        grid.addWidget(self.vol, 0, 1)
        grid.addWidget(self.lbl_vol, 0, 2)

        grid.addWidget(QLabel("Pitch"), 1, 0)
        self.pitch = QSlider(Qt.Horizontal)
        self.pitch.setRange(50, 150)
        self.pitch.setValue(100)
        self.lbl_pitch = QLabel("100")
        self.pitch.valueChanged.connect(self._on_pitch)
        grid.addWidget(self.pitch, 1, 1)
        grid.addWidget(self.lbl_pitch, 1, 2)
        grid.addWidget(
            QLabel("Pitch changes speed (not time-stretched)."),
            2, 0, 1, 3,
        )

        grid.addWidget(QLabel("Pan"), 3, 0)
        self.pan = QSlider(Qt.Horizontal)
        self.pan.setRange(-100, 100)
        self.pan.setValue(0)
        self.lbl_pan = QLabel("0")
        self.pan.valueChanged.connect(self._on_pan)
        grid.addWidget(self.pan, 3, 1)
        grid.addWidget(self.lbl_pan, 3, 2)
        self.pan_note = QLabel(
            "Pan: −100 left … 0 center … +100 right "
            "(display only — Qt audio output has no balance API)."
        )
        self.pan_note.setWordWrap(True)
        grid.addWidget(self.pan_note, 4, 0, 1, 3)
        layout.addWidget(box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        ext_row = QHBoxLayout()
        self.btn_external = QPushButton("Open in external player…")
        self.btn_external.setToolTip(
            "Open this file with the default system audio application"
        )
        self.btn_external.clicked.connect(self._open_external)
        ext_row.addWidget(self.btn_external)
        ext_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        ext_row.addWidget(close_btn)
        layout.addLayout(ext_row)

        self._init_player()

    def _set_transport_enabled(self, enabled: bool):
        self.btn_play.setEnabled(enabled)
        self.btn_pause.setEnabled(enabled)
        self.btn_stop.setEnabled(enabled)
        self.seek.setEnabled(enabled)
        self.vol.setEnabled(enabled)
        self.pitch.setEnabled(enabled)

    def _init_player(self):
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        except ImportError:
            self._qt_ok = False
            self._set_transport_enabled(False)
            self.status.setText(
                "QtMultimedia is not available.\n"
                "Use “Open in external player…” to hear this file."
            )
            return

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.setSource(QUrl.fromLocalFile(str(self._path.resolve())))
        self._audio_out.setVolume(0.90)

        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        self._on_pitch(self.pitch.value())
        self._qt_ok = True
        self.status.setText(
            "Ready. If Play fails (e.g. unsupported .ogg on Windows), "
            "use “Open in external player…”."
        )

    def _play(self):
        if not self._player:
            self._offer_external_after_failure("No internal player.")
            return
        self._player.play()

    def _pause(self):
        if self._player:
            self._player.pause()

    def _stop(self):
        if self._player:
            self._player.stop()

    def _on_duration(self, dur: int):
        self.seek.setRange(0, max(dur, 0))
        self.lbl_dur.setText(_fmt_ms(dur))

    def _on_position(self, pos: int):
        if not self._dragging_seek:
            self.seek.blockSignals(True)
            self.seek.setValue(pos)
            self.seek.blockSignals(False)
        self.lbl_pos.setText(_fmt_ms(pos))

    def _seek_moved(self, pos: int):
        self.lbl_pos.setText(_fmt_ms(pos))

    def _seek_released(self):
        self._dragging_seek = False
        if self._player:
            self._player.setPosition(self.seek.value())

    def _on_volume(self, v: int):
        self.lbl_vol.setText(f"{v}%")
        if self._audio_out:
            self._audio_out.setVolume(v / 100.0)

    def _on_pitch(self, v: int):
        self.lbl_pitch.setText(str(v))
        if self._player:
            self._player.setPlaybackRate(v / 100.0)

    def _on_pan(self, v: int):
        self.lbl_pan.setText(str(v))

    def _on_state(self, state):
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            names = {
                QMediaPlayer.PlaybackState.PlayingState: "Playing",
                QMediaPlayer.PlaybackState.PausedState: "Paused",
                QMediaPlayer.PlaybackState.StoppedState: "Stopped",
            }
            self.status.setText(names.get(state, str(state)))
        except Exception:
            pass

    def _on_media_status(self, status):
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            if status == QMediaPlayer.MediaStatus.InvalidMedia:
                self._handle_unsupported(
                    "Invalid or unsupported media (codec/container not available to Qt)."
                )
        except Exception:
            pass

    def _on_error(self, *args):
        err = ""
        if self._player:
            err = self._player.errorString() or ""
        if not err and args:
            err = str(args[0])
        self._handle_unsupported(err or "Playback error")

    def _handle_unsupported(self, message: str):
        self._qt_ok = False
        self._set_transport_enabled(False)
        self.status.setText(
            f"Error: {message}\n\n"
            "Qt cannot decode this file on this system "
            "(common with .ogg on Windows).\n"
            "Use “Open in external player…” to hear it in another app."
        )

    def _offer_external_after_failure(self, message: str):
        self.status.setText(message)
        ret = QMessageBox.question(
            self,
            "Cannot play inside Asset Tools",
            f"{message}\n\n"
            "Open this audio file with an external application?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret == QMessageBox.Yes:
            open_in_external_player(self._path, parent=self, ask=False)

    def _open_external(self):
        open_in_external_player(self._path, parent=self, ask=True)

    def closeEvent(self, event):
        if self._player:
            self._player.stop()
        super().closeEvent(event)

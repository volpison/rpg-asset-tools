"""
audio_preview.py
----------------
Helpers for audio files in the main window: which file belongs to an asset, and opening it in the
system's default player. (The small in-app "Play audio" window was removed: its controls never worked
for Ogg on Windows. The loop editor plays music itself.)
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

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

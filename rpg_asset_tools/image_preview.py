"""
image_preview.py
----------------
Popup image preview with checkerboard background (for transparency).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QScrollArea,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}


def is_image_path(path: Path | str | None) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def make_checkerboard_pixmap(width: int, height: int, cell: int = 12) -> QPixmap:
    pm = QPixmap(width, height)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    c1 = QColor(255, 255, 255)
    c2 = QColor(200, 200, 200)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            painter.fillRect(x, y, cell, cell, c1 if ((x // cell) + (y // cell)) % 2 == 0 else c2)
    painter.end()
    return pm


class CheckerboardImageLabel(QLabel):
    """QLabel that paints a checkerboard behind a pixmap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 240)

    def set_image(self, pixmap: QPixmap):
        self._pixmap = pixmap
        self.update()
        if not pixmap.isNull():
            self.setMinimumSize(
                min(pixmap.width() + 16, 900),
                min(pixmap.height() + 16, 700),
            )

    def paintEvent(self, event):
        painter = QPainter(self)
        # checkerboard full widget
        cell = 12
        w, h = self.width(), self.height()
        for y in range(0, h, cell):
            for x in range(0, w, cell):
                color = QColor(255, 255, 255) if ((x // cell) + (y // cell)) % 2 == 0 else QColor(190, 190, 190)
                painter.fillRect(x, y, cell, cell, color)

        if self._pixmap.isNull():
            painter.setPen(QColor(80, 80, 80))
            painter.drawText(self.rect(), Qt.AlignCenter, "No image")
            return

        # scale to fit while keeping aspect ratio
        scaled = self._pixmap.scaled(
            self.size() - QSize(16, 16),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)


class ImagePreviewDialog(QDialog):
    def __init__(self, image_path: Path, title: str = "Image preview", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 520)
        self.setModal(False)

        layout = QVBoxLayout(self)
        self.path_label = QLabel(str(image_path))
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.image_label = CheckerboardImageLabel()
        scroll.setWidget(self.image_label)
        layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.clicked.connect(lambda *_: self.close())
        layout.addWidget(buttons)

        pix = QPixmap(str(image_path))
        if pix.isNull():
            self.path_label.setText(f"Could not load:\n{image_path}")
        else:
            self.image_label.set_image(pix)
            self.setWindowTitle(f"{title} ({pix.width()}×{pix.height()})")


def resolve_preview_path(game_folder: Path, asset: dict) -> Path | None:
    """Return absolute path to an existing image file for this asset, or None."""
    if not asset or not game_folder:
        return None
    rel = asset.get("path")
    if rel:
        p = Path(game_folder) / rel
        if p.is_file() and is_image_path(p):
            return p
    return None


class ThumbnailLabel(CheckerboardImageLabel):
    """Small inline preview for the details panel (does not resize itself)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)

    def set_image(self, pixmap: QPixmap):
        self._pixmap = pixmap
        self.update()

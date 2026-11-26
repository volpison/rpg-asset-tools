"""
metronome.py
------------
The metronome click of the loop editor, added to the music's own sample buffer so it is exactly
in time with the grid and with the loop jump (it follows the file position of every sample, not a
separate timer). Needs numpy; no Qt.
"""

from __future__ import annotations

import numpy as np

from beatgrid import Grid

CLICK_SECONDS = 0.03


def make_click(rate: int, frequency: float) -> np.ndarray:
    """A short decaying sine burst (mono float32)."""
    n = max(8, int(rate * CLICK_SECONDS))
    t = np.arange(n) / rate
    env = np.exp(-t * 180.0) * np.minimum(1.0, np.arange(n) / 8.0)     # fast fade-in avoids a pop
    return (np.sin(2 * np.pi * frequency * t) * env).astype(np.float32)


class Metronome:
    """Clicks on every beat of `grid`; the first beat of each bar is higher and louder."""

    def __init__(self, grid: Grid, rate: int, volume: float = 0.5):
        self.grid = grid
        self.rate = rate
        self.volume = float(volume)
        self.enabled = True
        self._normal = make_click(rate, 1000.0) * 0.6
        self._accent = make_click(rate, 1500.0)

    @property
    def active(self) -> bool:
        return self.enabled and self.volume > 0

    def mix_into(self, chunk: np.ndarray, positions: np.ndarray) -> None:
        """Add the clicks to `chunk` (frames x channels) in place. `positions[i]` is the file
        position of frame i of the chunk."""
        if len(positions) == 0 or not self.active:
            return
        bl = self.grid.beat_length(self.rate)
        rel = positions.astype(np.float64) - self.grid.offset
        k = np.floor(rel / bl)
        since = rel - k * bl                                   # frames since the last beat
        hit = since < len(self._normal)
        if not hit.any():
            return
        idx = np.clip(since[hit].astype(np.int64), 0, len(self._normal) - 1)
        first = (k[hit].astype(np.int64) % self.grid.beats_per_bar) == 0
        value = np.where(first, self._accent[idx], self._normal[idx]) * self.volume
        chunk[hit] += value[:, None]

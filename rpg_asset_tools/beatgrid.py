"""
beatgrid.py
-----------
A beat grid for a song: a fixed tempo (BPM), the sample where a bar starts (the offset)
and how many beats make a bar. Used by the loop editor to draw beat and bar lines, to snap
markers to them, to set a loop length in bars and to place the metronome clicks.

Pure logic, no Qt, no numpy. Positions are in samples per channel ("frames").
The tempo is fixed: a song whose tempo changes needs a grid per section (not supported yet).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_BPM, MAX_BPM = 20.0, 400.0
MAX_BEATS_PER_BAR = 16


class GridError(ValueError):
    """The grid values are not usable. The text is meant for the user."""


@dataclass(frozen=True)
class Grid:
    bpm: float
    offset: int                  # frame where beat 1 of a bar falls (the grid also runs backwards from it)
    beats_per_bar: int = 4

    def __post_init__(self):
        if not (MIN_BPM <= self.bpm <= MAX_BPM) or math.isnan(self.bpm):
            raise GridError(f"The tempo must be between {MIN_BPM:g} and {MAX_BPM:g} BPM.")
        if not (1 <= self.beats_per_bar <= MAX_BEATS_PER_BAR):
            raise GridError(f"Beats per bar must be between 1 and {MAX_BEATS_PER_BAR}.")
        if self.offset < 0:
            raise GridError("The first beat cannot be before the start of the file.")

    def beat_length(self, rate: int) -> float:
        """Frames in one beat (not rounded: the grid stays exact over a long song)."""
        return 60.0 * rate / self.bpm

    def bar_length(self, rate: int) -> float:
        return self.beat_length(rate) * self.beats_per_bar

    def beat_frame(self, rate: int, k: int) -> float:
        """Frame of beat number k (0 is the offset; negative numbers are before it)."""
        return self.offset + k * self.beat_length(rate)


def beats_in_range(grid: Grid, rate: int, start: float, end: float) -> list[tuple[int, bool]]:
    """(frame, is_first_beat_of_bar) for every beat in [start, end), in order."""
    bl = grid.beat_length(rate)
    if end <= start or bl <= 0:
        return []
    k0 = math.ceil((start - grid.offset) / bl)
    k1 = math.ceil((end - grid.offset) / bl)          # exclusive
    return [(round(grid.offset + k * bl), k % grid.beats_per_bar == 0) for k in range(k0, k1)]


def nearest_beat(grid: Grid, rate: int, frame: float, per_bar: bool = False) -> int:
    """The frame of the beat (or bar start) nearest to `frame`."""
    unit = grid.bar_length(rate) if per_bar else grid.beat_length(rate)
    k = round((frame - grid.offset) / unit)
    return max(0, round(grid.offset + k * unit))


def bars_to_frames(grid: Grid, rate: int, bars: float) -> int:
    """Length in frames of `bars` bars (may be fractional, e.g. 0.5)."""
    return round(bars * grid.bar_length(rate))


def frames_to_bars(grid: Grid, rate: int, frames: float) -> float:
    return frames / grid.bar_length(rate)


def bpm_from_loop(length_frames: int, rate: int, bars: float, beats_per_bar: int = 4) -> float:
    """The tempo at which a loop of `length_frames` lasts exactly `bars` bars."""
    if length_frames <= 0 or bars <= 0 or rate <= 0:
        raise GridError("The loop and the number of bars must be more than 0.")
    return 60.0 * rate * bars * beats_per_bar / length_frames

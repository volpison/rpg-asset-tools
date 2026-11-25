"""
waveform.py
-----------
Fast waveform drawing data for long audio: a pyramid of min/max values, so that any
zoom level can be drawn from a few thousand numbers instead of millions of samples.

Needs numpy; no Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BASE = 64          # samples per bucket on the finest level
FACTOR = 4         # each further level is this many times coarser


@dataclass
class Pyramid:
    samples: np.ndarray            # (frames, channels) float32, kept for the deepest zoom
    sizes: list[int]               # bucket size of each level, in samples
    mins: list[np.ndarray]
    maxs: list[np.ndarray]

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])


def build_pyramid(samples: np.ndarray) -> Pyramid:
    """`samples` is (frames, channels). All channels are folded into one min/max envelope."""
    if samples.ndim == 1:
        samples = samples[:, None]
    n = samples.shape[0]
    lo = samples.min(axis=1) if samples.shape[1] > 1 else samples[:, 0]
    hi = samples.max(axis=1) if samples.shape[1] > 1 else samples[:, 0]
    sizes: list[int] = []
    mins: list[np.ndarray] = []
    maxs: list[np.ndarray] = []
    size = BASE
    cur_lo, cur_hi = _reduce(lo, hi, BASE)
    while True:
        sizes.append(size)
        mins.append(cur_lo)
        maxs.append(cur_hi)
        if len(cur_lo) <= 2 or size * FACTOR > max(n, BASE):
            break
        cur_lo, cur_hi = _reduce(cur_lo, cur_hi, FACTOR)
        size *= FACTOR
    return Pyramid(samples, sizes, mins, maxs)


def _reduce(lo: np.ndarray, hi: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Combine every k values into one (min of mins, max of maxs); the tail may be shorter."""
    n = len(lo)
    if n == 0:
        return lo[:0].copy(), hi[:0].copy()
    full = (n // k) * k
    out_lo = lo[:full].reshape(-1, k).min(axis=1) if full else lo[:0]
    out_hi = hi[:full].reshape(-1, k).max(axis=1) if full else hi[:0]
    if full < n:
        out_lo = np.append(out_lo, lo[full:].min())
        out_hi = np.append(out_hi, hi[full:].max())
    return out_lo, out_hi


def _folded(pyr: Pyramid) -> tuple[np.ndarray, np.ndarray]:
    s = pyr.samples
    if s.shape[1] == 1:
        return s[:, 0], s[:, 0]
    return s.min(axis=1), s.max(axis=1)


def peaks(pyr: Pyramid, start: float, end: float, width: int) -> tuple[np.ndarray, np.ndarray]:
    """Min and max for each of `width` pixel columns covering samples [start, end).

    Columns outside the audio come back as 0 / 0. When there is less than one sample per
    column, each column shows the sample under it."""
    width = max(1, int(width))
    total = pyr.frames
    out_lo = np.zeros(width, dtype=np.float32)
    out_hi = np.zeros(width, dtype=np.float32)
    if total == 0 or end <= start:
        return out_lo, out_hi
    spp = (end - start) / width                                   # samples per pixel column
    edges = start + spp * np.arange(width + 1)

    if spp < 1:                                                   # deep zoom: one sample per column
        lo_src, hi_src = _folded(pyr)
        at = np.floor(edges[:-1]).astype(np.int64)
        ok = (at >= 0) & (at < total)
        out_lo[ok] = lo_src[at[ok]]
        out_hi[ok] = hi_src[at[ok]]
        return out_lo, out_hi

    if spp < BASE:                                                # raw samples
        src_lo, src_hi = _folded(pyr)
        size = 1
    else:                                                         # coarsest level that is still fine enough
        level = max(i for i, s in enumerate(pyr.sizes) if s <= spp)
        size = pyr.sizes[level]
        src_lo, src_hi = pyr.mins[level], pyr.maxs[level]

    limit = len(src_lo)
    ec = np.clip(np.floor(edges / size).astype(np.int64), 0, limit)
    nonempty = np.flatnonzero(ec[1:] > ec[:-1])
    if len(nonempty) == 0:
        return out_lo, out_hi
    v0, v1 = int(nonempty[0]), int(nonempty[-1]) + 1              # these columns are contiguous
    base, stop = int(ec[v0]), int(ec[v1])
    starts = ec[v0:v1] - base
    out_lo[v0:v1] = np.minimum.reduceat(src_lo[base:stop], starts)
    out_hi[v0:v1] = np.maximum.reduceat(src_hi[base:stop], starts)
    return out_lo, out_hi


def samples_in_view(pyr: Pyramid, start: float, end: float) -> tuple[np.ndarray, np.ndarray]:
    """Frame numbers and values (mean of the channels) of the samples inside [start, end),
    for drawing a line through individual samples when zoomed in far."""
    a = max(0, int(np.floor(start)))
    b = min(pyr.frames, int(np.ceil(end)) + 1)
    if b <= a:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
    frames = np.arange(a, b, dtype=np.int64)
    return frames, pyr.samples[a:b].mean(axis=1)

"""
loopfind.py
-----------
Suggests loop points by comparing a song with itself.

A good loop is a pair (start, end) such that the music that follows `end` in the file is much like
the music that follows `start`, and the music before `end` is much like the music before `start`:
then the jump from `end` back to `start` is hard to hear. This module looks for such pairs:

1. the sound is reduced to short frames (about 0.09 s), each described by a few dozen log-energy
   bands (what the music "sounds like" at that moment, not its exact waveform);
2. every frame is compared with every other frame (a self-similarity matrix);
3. for each loop length, the similarity along the diagonal `frame i` vs `frame i + length` is
   averaged over a window of about 4 s around each position: a high average means the two places
   run in parallel for a while, which is what a seamless loop needs;
4. the best, well-separated pairs are refined to the exact sample by correlating the raw waveform
   around the end point, and given a "seam" score (how alike the waveform is right at the jump).

It is a helper, not an oracle: it suggests, the user listens (Hear the seam) and decides.
Needs numpy; no Qt. Positions are in samples per channel ("frames" in loopmath's sense).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FRAME = 2048                    # analysis window, in samples at the reduced rate
HOP = 1024
BANDS = 32
TARGET_RATE = 11025             # the song is reduced to about this rate for the comparison
WINDOW_SECONDS = 2.0            # half-width of the averaging window along a diagonal
MIN_SEAM = 0.3                  # candidates whose jump is plainly audible are not offered
MAX_FRAMES = 4000               # the similarity matrix is at most this many frames square (64 MB)
REFINE = 4096                   # samples compared when refining an end point (full rate)


@dataclass(frozen=True)
class Candidate:
    start: int                  # suggested LOOPSTART, in samples
    end: int                    # suggested loop end (start + LOOPLENGTH), in samples
    score: float                # 0..1: how alike the music around the two points is
    seam: float                 # 0..1: how alike the waveform is right at the jump

    @property
    def length(self) -> int:
        return self.end - self.start


def _mono(samples: np.ndarray) -> np.ndarray:
    s = np.asarray(samples, dtype=np.float32)
    return s.mean(axis=1) if s.ndim == 2 else s


def _reduce(mono: np.ndarray, rate: int) -> tuple[np.ndarray, int, int]:
    """(reduced signal, its rate, the reduction factor): block means, which also low-pass it."""
    factor = max(1, int(round(rate / TARGET_RATE)))
    n = (len(mono) // factor) * factor
    if factor == 1:
        return mono, rate, 1
    return mono[:n].reshape(-1, factor).mean(axis=1), rate // factor, factor


def frame_features(mono: np.ndarray, rate: int) -> tuple[np.ndarray, int]:
    """(one unit vector per frame (frames x BANDS), samples of `mono` per hop). Silent frames are
    all zeros, so they match nothing."""
    x, r, factor = _reduce(mono, rate)
    if len(x) < FRAME + HOP:
        return np.zeros((0, BANDS), np.float32), HOP * factor
    n_frames = 1 + (len(x) - FRAME) // HOP
    window = np.hanning(FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(FRAME, 1.0 / r)
    edges = np.geomspace(60.0, min(5000.0, r / 2 - 1), BANDS + 1)
    bin_edges = np.unique(np.clip(np.searchsorted(freqs, edges), 1, len(freqs) - 1))
    if len(bin_edges) < 3:
        return np.zeros((0, BANDS), np.float32), HOP * factor
    feats = np.zeros((n_frames, len(bin_edges) - 1), np.float32)
    view = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::HOP]
    for a in range(0, n_frames, 256):                      # in chunks: bounded memory
        chunk = view[a:a + 256] * window
        power = np.abs(np.fft.rfft(chunk, axis=1)) ** 2
        band = np.add.reduceat(power, bin_edges[:-1], axis=1)[:, :len(bin_edges) - 1]
        feats[a:a + len(chunk)] = np.log(band + 1e-7)
    loud = feats.max(axis=1) > np.log(1e-7) + 6.0         # frames with something in them
    if loud.any():
        feats -= feats[loud].mean(axis=0)                  # what is typical of the whole song counts for nothing
    norms = np.linalg.norm(feats, axis=1, keepdims=True)
    feats = np.where((norms > 1e-6) & loud[:, None], feats / np.maximum(norms, 1e-6), 0.0)
    return feats.astype(np.float32), HOP * factor


def coarse_factor(total_samples: int, rate: int) -> int:
    """How many analysis frames are merged into one so that a long song stays within MAX_FRAMES
    (1 = not at all). The matrix grows with the square of the song's length, so without this a
    20-minute song would need about 670 MB."""
    factor = max(1, int(round(rate / TARGET_RATE)))
    n = max(0, (total_samples // factor - FRAME) // HOP + 1)
    return max(1, -(-n // MAX_FRAMES))


def _coarsen(feats: np.ndarray, k: int) -> np.ndarray:
    """Average every k frames and make them unit vectors again (silent ones stay zero)."""
    if k <= 1 or len(feats) == 0:
        return feats
    n = (len(feats) // k) * k
    merged = feats[:n].reshape(-1, k, feats.shape[1]).mean(axis=1)
    norms = np.linalg.norm(merged, axis=1, keepdims=True)
    return np.where(norms > 1e-6, merged / np.maximum(norms, 1e-6), 0.0).astype(np.float32)


def _window_mean(values: np.ndarray, half: int) -> np.ndarray:
    """Mean of `values` over [i - half, i + half]. Near the ends the window is cut, but it always
    counts at least half a window, so a short stretch at the very edge cannot score high."""
    n = len(values)
    csum = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
    lo = np.clip(np.arange(n) - half, 0, n)
    hi = np.clip(np.arange(n) + half + 1, 0, n)
    return (csum[hi] - csum[lo]) / np.maximum(hi - lo, half + 1)


def find_loops(samples: np.ndarray, rate: int, *, min_length: float = 4.0, count: int = 8,
               start_range: tuple[int, int] | None = None, end_range: tuple[int, int] | None = None,
               cancel=None) -> list[Candidate]:
    """The best loops of the song, best first.

    min_length   shortest loop wanted, in seconds
    count        how many to return
    start_range  only starts inside this (low, high) range of samples (to keep the current start)
    end_range    only ends inside this range (to keep the current end)
    cancel       a function returning True to stop early (the result is then empty)"""
    mono = _mono(samples)
    total = len(mono)
    feats, hop = frame_features(mono, rate)
    k = coarse_factor(total, rate)                         # a long song is compared more coarsely
    if k > 1:
        feats, hop = _coarsen(feats, k), hop * k
    n = len(feats)
    if n < 8:
        return []
    min_d = max(1, int(min_length * rate / hop))
    if n - min_d < 2:
        return []
    sim = feats @ feats.T                                  # frames x frames, cosine similarity
    half = max(2, int(WINDOW_SECONDS * rate / hop))
    a_lo, a_hi = (0, n - 1) if start_range is None else (max(0, start_range[0] // hop), min(n - 1, start_range[1] // hop))
    b_lo, b_hi = (0, n - 1) if end_range is None else (max(0, end_range[0] // hop), min(n - 1, end_range[1] // hop))

    found: list[tuple[float, int, int]] = []              # (score, start frame, length in frames)
    for k, d in enumerate(range(min_d, n - 1)):
        if cancel is not None and k % 64 == 0 and cancel():
            return []
        i_lo, i_hi = max(a_lo, b_lo - d), min(a_hi, b_hi - d, n - 1 - d)
        if i_hi < i_lo:
            continue
        along = _window_mean(np.diagonal(sim, d), half)    # similarity of "i" and "i + d" over ~4 s
        seg = along[i_lo:i_hi + 1]
        j = int(np.argmax(seg))
        found.append((float(seg[j]), i_lo + j, d))
    found.sort(reverse=True)

    picked: list[tuple[float, int, int]] = []
    for score, a, d in found:                              # keep well-separated candidates only
        if score <= 0:
            break
        if all(abs(d - d2) > half // 2 or abs(a - a2) > half for _s, a2, d2 in picked):
            picked.append((score, a, d))
            if len(picked) >= count:
                break

    out = []
    for score, a, d in picked:
        if cancel is not None and cancel():
            return []
        start, end, seam = _refine(mono, a * hop, (a + d) * hop, total, hop,
                                   end_fixed=end_range is not None and start_range is None)
        if end - start >= int(min_length * rate * 0.9) and 0 <= start < end <= total and seam >= MIN_SEAM:
            out.append(Candidate(start, end, max(0.0, min(1.0, score)), seam))
    out.sort(key=lambda c: (-(0.5 * c.score + 0.5 * c.seam), c.start))
    return out


def _norm_corr(x: np.ndarray, y: np.ndarray) -> float:
    d = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
    return float(np.dot(x, y) / d) if d > 1e-9 else 0.0


def _refine(mono: np.ndarray, start: int, end: int, total: int, hop: int,
            end_fixed: bool = False) -> tuple[int, int, float]:
    """Move one point (the end, or the start when the end must stay) to the exact sample where
    the waveform after it matches the waveform after the other one best; return both points and
    the seam score (forward and backward waveform match at the jump)."""
    start = int(min(max(start, 0), total - 1))
    end = int(min(max(end, 1), total))
    length = min(REFINE, total - start, total - end)
    if length < 256:
        return start, end, 0.0
    search = min(hop, 8192)
    fixed, moving = (end, start) if end_fixed else (start, end)
    ref = mono[fixed:fixed + length]
    lo = max(0, moving - search)
    hi = min(total - length, moving + search)
    if hi <= lo:
        return start, end, 0.0
    region = mono[lo:hi + length]
    raw = np.correlate(region, ref, mode="valid")              # one value per shift
    energy = np.concatenate(([0.0], np.cumsum(region.astype(np.float64) ** 2)))
    win = energy[length:length + len(raw)] - energy[:len(raw)]
    score = raw / np.sqrt(np.maximum(win * float(np.dot(ref, ref)), 1e-12))
    best = int(np.argmax(score))
    moved = lo + best
    if end_fixed:
        start, end = moved, fixed
    else:
        start, end = fixed, moved
    back = min(REFINE, start, end)
    forward = max(0.0, _norm_corr(mono[start:start + length], mono[end:end + length]))
    backward = max(0.0, _norm_corr(mono[start - back:start], mono[end - back:end])) if back >= 256 else forward
    return start, end, float(0.5 * (forward + backward))

"""
bpmdetect.py
------------
Finds the tempo (BPM) and the first beat of a song from the audio.

How it works, in plain steps:
1. The song is reduced to about 11 kHz (block means, in chunks, so a long stereo song is never
   copied whole) and cut into very short frames (about 12 ms apart);
2. each frame is described by 24 band energies; "onset strength" is how much the energy rose since
   the previous frame (a drum hit or a note start makes a peak), with the slow background removed;
3. the repetition of those peaks is measured (autocorrelation); tempi near 120 BPM are preferred a
   little, because "60" and "120" look alike to a computer;
4. each rough tempo is refined: the onset strengths are folded on a beat period and the period that
   piles the peaks up best wins (so the tempo is exact to about 0.01 BPM, which matters: 1 % off
   drifts a whole beat in 100 beats);
5. the folded picture also says where the beats fall. Which beat is "beat 1" of a bar is a guess
   (the strongest of the beats-per-bar choices), and the user can move it.

It is a helper, not an oracle: it gives the best few answers (the half and double tempo too) and
the user listens with the metronome. Needs numpy; no Qt. Positions are samples per channel.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FRAME = 1024                   # analysis window, in samples at the reduced rate
HOP = 128                      # about 11.6 ms at 11 kHz
BANDS = 24
TARGET_RATE = 11025
MIN_BPM, MAX_BPM = 50.0, 220.0     # the range searched; half and double are offered besides
PRIOR_CENTRE, PRIOR_WIDTH = 120.0, 1.0   # a gentle preference (log2 scale) for tempi near 120
LAG = 0.81                     # where in its window a frame "hears" a hit (calibrated on click tracks: tests)
DOUBLE_RATIO = 0.5             # half-way peak at least this much of the beat peak: the real tempo is double
FOLD_BINS = 96                 # resolution of the beat picture: a beat is cut in this many parts
REFINE_SPAN = 0.03             # the tempo is searched +-3 % around each rough value
REFINE_STEPS = 241
MIN_SECONDS = 6.0              # shorter music has too few beats to tell


@dataclass(frozen=True)
class Tempo:
    bpm: float
    offset: int                # frame of a beat (the strongest "beat 1" guess), 0 <= offset < one bar
    confidence: float          # 0..1: how clearly the beats stand out
    note: str = ""             # "", "half" or "double" (the alternatives to the best answer)


def _reduced_mono(samples: np.ndarray, rate: int, first: int, last: int) -> tuple[np.ndarray, int]:
    """(mono at about TARGET_RATE of samples[first:last], the reduction factor), done in chunks."""
    factor = max(1, int(round(rate / TARGET_RATE)))
    n = ((last - first) // factor) * factor
    out = np.empty(n // factor, np.float32)
    step = factor * 65536
    for a in range(0, n, step):
        b = min(n, a + step)
        part = np.asarray(samples[first + a:first + b], dtype=np.float32)
        if part.ndim == 2:
            part = part.mean(axis=1)
        out[a // factor:b // factor] = part.reshape(-1, factor).mean(axis=1)
    return out, factor


def onset_strength(x: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray, float]:
    """(onset strength per frame, low-pitch accent per frame, frames per second) of a reduced mono
    signal. The first is for the tempo; the second (the rise of the energy below about 200 Hz,
    not compressed, so a loud kick stands out from a soft one) only helps to guess beat 1."""
    if len(x) < FRAME + HOP:
        return np.zeros(0, np.float32), np.zeros(0, np.float32), rate / HOP
    n_frames = 1 + (len(x) - FRAME) // HOP
    window = np.hanning(FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(FRAME, 1.0 / rate)
    edges = np.geomspace(50.0, min(5000.0, rate / 2 - 1), BANDS + 1)
    bin_edges = np.unique(np.clip(np.searchsorted(freqs, edges), 1, len(freqs) - 1))
    if len(bin_edges) < 3:
        return np.zeros(0, np.float32), np.zeros(0, np.float32), rate / HOP
    low_top = max(2, int(np.searchsorted(freqs, 200.0)))
    low = np.empty(n_frames, np.float32)
    view = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::HOP]
    logband = np.empty((n_frames, len(bin_edges) - 1), np.float32)
    for a in range(0, n_frames, 512):                      # in chunks: bounded memory
        chunk = view[a:a + 512] * window
        power = np.abs(np.fft.rfft(chunk, axis=1)) ** 2
        band = np.add.reduceat(power, bin_edges[:-1], axis=1)[:, :len(bin_edges) - 1]
        logband[a:a + len(chunk)] = np.log1p(band * 1e3)
        low[a:a + len(chunk)] = np.sqrt(power[:, 1:low_top].sum(axis=1))
    rise = np.diff(logband, axis=0, prepend=logband[:1])
    flux = np.maximum(rise, 0.0).sum(axis=1)
    fps = rate / HOP
    half = max(1, int(0.4 * fps))                          # remove the slow background
    csum = np.concatenate(([0.0], np.cumsum(flux, dtype=np.float64)))
    idx = np.arange(len(flux))
    lo, hi = np.clip(idx - half, 0, len(flux)), np.clip(idx + half + 1, 0, len(flux))
    local = (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)
    env = np.maximum(flux - local, 0.0)
    top = float(np.percentile(env, 99.5)) if len(env) else 0.0
    if top > 1e-9:
        env = np.minimum(env / top, 1.5)                   # one huge hit must not decide everything
    low_rise = np.maximum(np.diff(low, prepend=low[:1]), 0.0)
    return env.astype(np.float32), low_rise.astype(np.float32), fps


def _rough_tempi(env: np.ndarray, fps: float, count: int) -> list[float]:
    """The strongest tempi (BPM) by autocorrelation of the onset strength, best first."""
    n = len(env)
    lag_lo = max(2, int(fps * 60.0 / MAX_BPM))
    lag_hi = min(n // 2, int(np.ceil(fps * 60.0 / MIN_BPM)) + 1)
    if lag_hi <= lag_lo + 2:
        return []
    e = env - env.mean()
    size = 1 << int(np.ceil(np.log2(2 * n)))
    spec = np.fft.rfft(e, size)
    ac = np.fft.irfft(spec * np.conj(spec), size)[:lag_hi + 2]
    ac = ac / np.maximum(ac[0], 1e-12)
    lags = np.arange(lag_lo, lag_hi + 1)
    bpm = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / PRIOR_CENTRE) / PRIOR_WIDTH) ** 2)
    score = ac[lag_lo:lag_hi + 1] * (0.5 + 0.5 * prior)
    found: list[float] = []
    order = np.argsort(score)[::-1]
    for i in order:
        if score[i] <= 0:
            break
        if i == 0 or i == len(score) - 1 or not (score[i] >= score[i - 1] and score[i] >= score[i + 1]):
            continue                                       # a peak only, not a slope
        a, b, c = score[i - 1], score[i], score[i + 1]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        lag = lags[i] + shift                              # sub-frame position of the peak
        t = 60.0 * fps / lag
        if all(abs(t - f) / f > 0.04 for f in found):
            found.append(float(t))
            if len(found) >= count:
                break
    return found


def _fold(env: np.ndarray, period_frames: float) -> np.ndarray:
    """The onset strengths piled up on one beat period, FOLD_BINS parts (circular, lightly smoothed)."""
    pos = (np.arange(len(env)) % period_frames) / period_frames
    bins = np.minimum((pos * FOLD_BINS).astype(np.int64), FOLD_BINS - 1)
    h = np.bincount(bins, weights=env, minlength=FOLD_BINS)
    return h + 0.5 * (np.roll(h, 1) + np.roll(h, -1))


def _refine(env: np.ndarray, fps: float, bpm: float) -> tuple[float, float, int]:
    """(refined BPM, its score = how peaked the folded picture is, the phase bin of the peak)."""
    best = (-1.0, bpm, 0)
    for k in range(REFINE_STEPS):
        t = bpm * (1.0 + REFINE_SPAN * (2.0 * k / (REFINE_STEPS - 1) - 1.0))
        h = _fold(env, fps * 60.0 / t)
        peak = int(np.argmax(h))
        s = float(h[peak] / max(h.mean(), 1e-9))
        if s > best[0]:
            best = (s, t, peak)
    return best[1], best[0], best[2]


def detect(samples: np.ndarray, rate: int, first: int = 0, last: int | None = None,
           beats_per_bar: int = 4, count: int = 3, cancel=None) -> list[Tempo]:
    """The most likely tempi of samples[first:last], best first (then its half and double tempo
    when they are in range). The offsets are relative to the whole file. [] if nothing is found."""
    total = int(samples.shape[0])
    last = total if last is None else min(last, total)
    first = max(0, first)
    if last - first < MIN_SECONDS * rate:
        return []
    x, factor = _reduced_mono(samples, rate, first, last)
    if cancel is not None and cancel():
        return []
    env, low, fps = onset_strength(x, rate // factor if factor > 1 else rate)
    if len(env) < 16 or float(env.max()) <= 0:
        return []
    rough = _rough_tempi(env, fps, count)
    scored: list[tuple[float, float, int]] = []
    for t in rough:
        if cancel is not None and cancel():
            return []
        bpm, score, phase = _refine(env, fps, t)
        scored.append((score, bpm, phase))
    if not scored:
        return []
    scored.sort(reverse=True)
    for _ in range(2):                                     # a sparse pattern scores high: prefer the double
        score, bpm, phase = scored[0]                      # tempo when the half-way position is as busy
        h = _fold(env, fps * 60.0 / bpm)
        mid = (phase + FOLD_BINS // 2) % FOLD_BINS
        opposite = max(h[(mid + d) % FOLD_BINS] for d in (-2, -1, 0, 1, 2))
        if bpm * 2 > MAX_BPM or opposite < DOUBLE_RATIO * h[phase]:
            break
        d_bpm, d_score, d_phase = _refine(env, fps, bpm * 2)
        scored = [(d_score * (1.0 + 1e-6), d_bpm, d_phase)] + \
                 [s for s in scored if abs(s[1] - d_bpm) / d_bpm > 0.04]
    top_score = scored[0][0]
    out: list[Tempo] = []
    for score, bpm, phase in scored:
        out.append(_make(env, low, fps, rate, first, bpm, phase, beats_per_bar, score, top_score, ""))
    best = out[0]
    for factor_, note in ((0.5, "half"), (2.0, "double")):
        t = best.bpm * factor_
        if MIN_BPM * 0.8 <= t <= MAX_BPM * 1.25 and all(abs(t - o.bpm) / o.bpm > 0.04 for o in out):
            out.append(_make(env, low, fps, rate, first, t, None, beats_per_bar, 0.0, top_score, note))
    return out


def _make(env, low, fps, rate, first, bpm, phase, bpb, score, top_score, note) -> Tempo:
    """A Tempo with its offset (the strongest of the `bpb` beat-1 choices) and confidence."""
    period = fps * 60.0 / bpm                              # frames of `env` per beat
    if phase is None:                                      # half/double: refold at the new period
        h = _fold(env, period)
        phase = int(np.argmax(h))
        score = float(h[phase] / max(h.mean(), 1e-9))
    first_beat = (phase + 0.5) / FOLD_BINS * period        # env frame of the first beat (the bin's middle)
    beats = np.arange(first_beat, len(env), period)
    idx = np.minimum(np.round(beats).astype(np.int64), len(env) - 1)
    strength = env[idx] + low[idx] / max(float(np.percentile(low, 99.5)), 1e-9)
    k = np.arange(len(beats))
    best_j, best_sum = 0, -1.0
    for j in range(max(1, bpb)):                           # which beat of a bar is the strongest on average
        sel = strength[k % bpb == j]
        s = float(sel.mean()) if len(sel) else 0.0
        if s > best_sum:
            best_j, best_sum = j, s
    offset_env = first_beat + best_j * period              # the first "beat 1" of the song
    # a frame of env starts at i*HOP samples (reduced) and its window is centred half a FRAME later
    offset = first + int(round((offset_env * HOP + LAG * FRAME) * (rate / (fps * HOP))))
    peak = score / max(top_score, 1e-9)
    confidence = float(max(0.0, min(1.0, (score - 1.0) / 6.0)) * min(1.0, peak))
    return Tempo(round(bpm, 2), max(0, offset), confidence, note)

"""
loopplayback.py
---------------
The playing rules of the loop editor, without any sound device: which samples come next,
including the jump from the loop end back to the loop start, and where the play cursor
is after a number of samples have been played. Needs numpy; no Qt.

`loop_end=None` means "play once": playback stops at the end of the file.
"""

from __future__ import annotations

import numpy as np


def _check(total: int, loop_start: int, loop_end: int | None) -> None:
    if loop_end is not None and not (0 <= loop_start < loop_end <= total):
        raise ValueError(f"bad loop: start={loop_start} end={loop_end} total={total}")


def render(samples: np.ndarray, pos: int, n: int,
           loop_start: int, loop_end: int | None) -> tuple[np.ndarray, int]:
    """The next `n` samples from position `pos`, and the position after them.

    When the loop end is reached the playback continues at the loop start, in the middle of
    a chunk if need be, so the jump is sample-exact. Without a loop the chunk is shorter
    than `n` once the end of the file is reached (an empty chunk means "finished")."""
    total = samples.shape[0]
    _check(total, loop_start, loop_end)
    out = np.empty((n, samples.shape[1]), dtype=np.float32)
    filled = 0
    limit = total if loop_end is None else loop_end
    if loop_end is not None and pos >= loop_end:
        pos = loop_start
    pos = max(0, pos)
    while filled < n:
        take = min(n - filled, limit - pos)
        if take <= 0:
            break
        out[filled:filled + take] = samples[pos:pos + take]
        filled += take
        pos += take
        if loop_end is not None and pos >= loop_end:
            pos = loop_start
    return out[:filled], pos


def render_rate(samples: np.ndarray, pos: float, n: int, loop_start: int, loop_end: int | None,
                rate: float) -> tuple[np.ndarray, float, np.ndarray]:
    """Like `render`, but stepping `rate` file samples per output sample (1.5 = a fifth higher and faster),
    with linear interpolation between samples. `pos` and the returned position are fractional.
    Also returns the file position of each output sample (for the metronome). The loop jump is exact:
    the first output sample at or after the loop end reads from the loop start."""
    total = samples.shape[0]
    _check(total, loop_start, loop_end)
    pos = float(pos)
    if loop_end is not None and pos >= loop_end:
        pos = float(loop_start)
    pos = max(0.0, pos)
    idx = pos + rate * np.arange(n, dtype=np.float64)
    if loop_end is None:
        idx = idx[idx <= total - 1]                     # increasing: what remains is the readable start
        new_pos = pos + rate * len(idx)
        i0 = np.floor(idx).astype(np.int64)
        i1 = np.minimum(i0 + 1, total - 1)
    else:
        length = loop_end - loop_start
        idx = np.where(idx >= loop_end, loop_start + (idx - loop_end) % length, idx)
        new_pos = pos + rate * n
        if new_pos >= loop_end:
            new_pos = loop_start + (new_pos - loop_end) % length
        i0 = np.floor(idx).astype(np.int64)
        i1 = i0 + 1
        i1 = np.where(i1 >= loop_end, loop_start, i1)    # after the last sample of the loop comes its first
    frac = (idx - i0).astype(np.float32)[:, None]
    chunk = samples[i0] * (1.0 - frac) + samples[i1] * frac
    return chunk.astype(np.float32), float(new_pos), idx


def positions(pos: int, n: int, loop_start: int, loop_end: int | None, total: int) -> np.ndarray:
    """The file position of each of the `n` samples `render` returns from `pos` (so something
    that depends on the position, like the metronome, can be added to the chunk). Its length is
    shorter than `n` at the end of the file when there is no loop, exactly like `render`."""
    _check(total, loop_start, loop_end)
    out = np.empty(n, dtype=np.int64)
    filled = 0
    limit = total if loop_end is None else loop_end
    if loop_end is not None and pos >= loop_end:
        pos = loop_start
    pos = max(0, pos)
    while filled < n:
        take = min(n - filled, limit - pos)
        if take <= 0:
            break
        out[filled:filled + take] = np.arange(pos, pos + take)
        filled += take
        pos += take
        if loop_end is not None and pos >= loop_end:
            pos = loop_start
    return out[:filled]


def advance(pos: int, played: int, total: int, loop_start: int, loop_end: int | None,
            rate: float = 1.0) -> int:
    """Where the play cursor is `played` output samples after position `pos` (for drawing the cursor).
    `rate` is the pitch step (file samples per output sample)."""
    _check(total, loop_start, loop_end)
    if rate != 1.0:
        played = int(played * rate)
    if loop_end is None:
        return min(pos + played, total)
    if pos >= loop_end:
        pos = loop_start
    pos += played
    if pos < loop_end:
        return pos
    return loop_start + (pos - loop_end) % (loop_end - loop_start)


def to_pcm16(chunk: np.ndarray) -> bytes:
    """Interleaved little-endian 16-bit samples for the sound device."""
    return np.clip(chunk * 32767.0, -32768, 32767).astype("<i2").tobytes()

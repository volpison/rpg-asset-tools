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


def advance(pos: int, played: int, total: int, loop_start: int, loop_end: int | None) -> int:
    """Where the play cursor is `played` samples after position `pos` (for drawing the cursor)."""
    _check(total, loop_start, loop_end)
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

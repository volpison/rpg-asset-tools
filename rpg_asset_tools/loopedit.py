"""
loopedit.py
-----------
Helpers for editing loop points: reading a position the user typed (samples, seconds or
minutes:seconds) and snapping a position to a zero crossing of the sound.

Pure logic, no Qt. Positions are in samples per channel ("frames"), like LOOPSTART/LOOPLENGTH.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


_NUMBER = re.compile(r"\d+\.?\d*|\.\d+")


def _dec(text: str) -> Decimal:
    """A plain decimal number (no exponent, no 'nan'/'inf', no sign)."""
    text = text.strip()
    if not _NUMBER.fullmatch(text):
        raise ValueError
    return Decimal(text)


def parse_position(text: str, rate: int) -> int:
    """Turn what the user typed into a sample number. Accepted forms:

        132300        a whole number: samples
        3 s  3.0s     a number followed by s: seconds ("3,5s" works too)
        3.5           a number with a decimal point: seconds
        1:23.5        minutes:seconds (also h:mm:ss)

    Raises ValueError with a message that can be shown to the user."""
    t = text.strip().lower().replace(",", ".")
    if t.startswith("-"):
        raise ValueError("A position cannot be negative.")
    if not t:
        raise ValueError("Type a position: samples (132300), seconds (3.5 s) or minutes:seconds (1:23.5).")
    if rate <= 0:
        raise ValueError("The sample rate of the file is not known.")
    try:
        if ":" in t:
            parts = t.rstrip("s").strip().split(":")
            if len(parts) > 3 or any(not p.strip() for p in parts):
                raise ValueError
            seconds = Decimal(0)
            for p in parts:
                seconds = seconds * 60 + _dec(p)
        elif t.endswith("s"):
            seconds = _dec(t[:-1])
        elif re.fullmatch(r"\d+", t):
            return int(t)
        else:
            seconds = _dec(t)
    except (InvalidOperation, ValueError):
        raise ValueError(f"'{text.strip()}' is not a position. Use samples (132300), "
                         "seconds (3.5 s) or minutes:seconds (1:23.5).") from None
    return int((seconds * rate).to_integral_value(rounding="ROUND_HALF_UP"))


def snap_to_zero_crossing(samples, pos: int, max_distance: int, rising: bool = True) -> int:
    """The sample nearest to `pos` (within `max_distance`) where the sound (all channels averaged)
    goes from below zero to zero or above. Jumping between two such points makes no click, and with
    `rising` both ends of a loop have the same direction, which also keeps the seam smooth.
    Returns `pos` unchanged when there is none in range. `samples` is a (frames, channels) array."""
    import numpy as np
    n = len(samples)
    if n < 2 or max_distance <= 0:
        return pos
    pos = max(0, min(int(pos), n - 1))
    lo = max(1, pos - int(max_distance))
    hi = min(n - 1, pos + int(max_distance))
    if hi < lo:
        return pos
    m = np.asarray(samples[lo - 1:hi + 1], dtype=np.float32)
    m = m.mean(axis=1) if m.ndim == 2 else m
    before, after = m[:-1], m[1:]
    if rising:
        hit = np.flatnonzero((before < 0) & (after >= 0))
    else:
        hit = np.flatnonzero(((before < 0) & (after >= 0)) | ((before > 0) & (after <= 0)))
    if len(hit) == 0:
        return pos
    frames = lo + hit                                    # the sample after the crossing
    best = frames[np.argmin(np.abs(frames - pos))]       # ties go to the earlier one
    return int(best)

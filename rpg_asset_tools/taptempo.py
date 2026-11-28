"""
taptempo.py
-----------
Tap tempo: the user taps a button in time with the music and this works out the BPM.

Pure logic, no Qt, no numpy. Times are seconds from any clock (the caller passes
`time.monotonic()`), so it can be tested without waiting.

The tempo is the average interval between the first and last of the recent taps, not of
single pairs, so one late tap hardly matters. A pause longer than MAX_GAP starts a new count.
"""

from __future__ import annotations

MAX_GAP = 2.0          # seconds: a longer pause starts again (60 BPM is a 1 s beat)
KEEP = 16              # only the latest taps count, so a tempo change is followed
MIN_TAPS = 3           # three taps (two intervals) before a tempo is shown


class TapTempo:
    def __init__(self):
        self._taps: list[float] = []

    def reset(self) -> None:
        self._taps.clear()

    @property
    def count(self) -> int:
        return len(self._taps)

    def tap(self, now: float) -> float | None:
        """Register a tap at time `now`; return the BPM so far, or None while it is too early."""
        if self._taps and (now - self._taps[-1] > MAX_GAP or now < self._taps[-1]):
            self._taps.clear()
        self._taps.append(now)
        del self._taps[:-KEEP]
        return self.bpm

    @property
    def bpm(self) -> float | None:
        if len(self._taps) < MIN_TAPS:
            return None
        span = self._taps[-1] - self._taps[0]
        if span <= 0:
            return None
        return 60.0 * (len(self._taps) - 1) / span

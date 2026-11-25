"""
loopmath.py
-----------
Numbers for the loop editor: turns the tags found in a file into the loop that is
shown and played, and converts between samples and time. Pure Python.

All positions are counted in samples per channel ("frames"), the unit LOOPSTART
and LOOPLENGTH use.
"""

from __future__ import annotations

from dataclasses import dataclass

from oggmeta import LoopTags

NONE = "none"                  # no loop tags: the whole file repeats
COMPLETE = "complete"          # LOOPSTART and LOOPLENGTH both present
START_ONLY = "start_only"      # LOOPSTART without LOOPLENGTH
LENGTH_ONLY = "length_only"    # LOOPLENGTH without LOOPSTART
INVALID = "invalid"            # values that do not fit the file


@dataclass(frozen=True)
class LoopView:
    start: int                 # first sample of the loop
    end: int                   # sample where playback jumps back to `start` (exclusive)
    status: str
    notes: tuple[str, ...] = ()

    @property
    def length(self) -> int:
        return self.end - self.start


def resolve_loop(tags: LoopTags, total: int) -> LoopView:
    """The loop that the editor shows and plays for these tags in a file of `total` samples.

    A loop that does not fit the file is reported as INVALID and shown as the whole file,
    so playing never breaks."""
    if total <= 0:
        return LoopView(0, 0, NONE, ("The file has no audio.",))
    whole = (0, total)
    start, length = tags.start, tags.length
    if start is None and length is None:
        return LoopView(*whole, NONE, ("No loop tags: the whole file is the loop.",))
    if start is None:
        start = 0
        status = LENGTH_ONLY
        notes = ["LOOPLENGTH is present but LOOPSTART is not; the loop is shown from sample 0."]
    elif length is None:
        status = START_ONLY
        notes = ["LOOPLENGTH is missing; the loop is shown until the end of the file."]
    else:
        status = COMPLETE
        notes = []
    end = total if length is None else start + length
    if start >= total:
        return LoopView(*whole, INVALID,
                        (f"LOOPSTART ({start}) is beyond the end of the file ({total} samples).",))
    if length is not None and length <= 0:
        return LoopView(*whole, INVALID, ("LOOPLENGTH is 0, so there is no loop to show.",))
    if end > total:
        notes.append(f"The loop ends at sample {end}, after the end of the file ({total}); "
                     "it is shown until the end.")
        return LoopView(start, total, INVALID, tuple(notes))
    return LoopView(start, end, status, tuple(notes))


def frames_to_seconds(frames: int, rate: int) -> float:
    return frames / rate if rate else 0.0


def format_time(frames: int, rate: int) -> str:
    """m:ss.mmm for a sample position, e.g. 66150 samples at 44100 Hz -> 0:01.500."""
    if rate <= 0:
        return "0:00.000"
    ms = round(abs(frames) * 1000 / rate)
    sign = "-" if frames < 0 else ""
    return f"{sign}{ms // 60000}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def seam_start(view: LoopView, rate: int, lead_seconds: float = 3.0) -> int:
    """Where to start playing to hear the jump from the loop end back to the loop start."""
    return max(0, view.end - int(lead_seconds * rate))


def nice_tick_seconds(span_seconds: float, width_px: int, min_px: int = 90) -> float:
    """A round time step (1 ms ... 10 min) that gives ticks at least `min_px` apart."""
    if span_seconds <= 0 or width_px <= 0:
        return 1.0
    want = span_seconds * min_px / width_px
    for step in (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5,
                 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if step >= want:
            return float(step)
    return 600.0

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

from enginetags import EngineTags, engine_loop
from oggmeta import LoopTags

NONE = "none"                  # no loop tags: the whole file repeats
COMPLETE = "complete"          # LOOPSTART and LOOPLENGTH both present
START_ONLY = "start_only"      # LOOPSTART without LOOPLENGTH: the game ignores it
LENGTH_ONLY = "length_only"    # LOOPLENGTH without LOOPSTART: the loop starts at 0
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


def resolve_loop(engine: EngineTags, total: int, standard: LoopTags | None = None) -> LoopView:
    """The loop the game plays for this file, as RPG Maker MZ's own reader sees it (enginetags),
    for a file of `total` samples. `standard` is the same file read by the Vorbis rules (oggmeta);
    when the two disagree a note says why.

    A loop that does not fit the file is reported as INVALID and shown as the whole file or
    until the end, so playing never breaks."""
    if total <= 0:
        return LoopView(0, 0, NONE, ("The file has no audio.",))
    notes = list(_disagreements(standard, engine)) if standard is not None else []
    el = engine_loop(engine, total)
    whole = (0, total)
    if not el.from_tags:
        if engine.loop_start and not engine.loop_length:
            notes.append("LOOPSTART is set but LOOPLENGTH is not. RPG Maker ignores LOOPSTART on its own "
                         "and repeats the whole file.")
            return LoopView(*whole, START_ONLY, tuple(notes))
        notes.append("No usable loop tags: the game repeats the whole file.")
        return LoopView(*whole, NONE, tuple(notes))
    start, length = el.start, el.length
    end = start + length
    status = COMPLETE
    if standard is not None and standard.start is None:
        status = LENGTH_ONLY
        notes.append("LOOPSTART is missing; the loop starts at sample 0.")
    if start < 0:
        notes.append(f"LOOPSTART ({start}) is negative; the whole file is shown.")
        return LoopView(*whole, INVALID, tuple(notes))
    if start >= total:
        notes.append(f"LOOPSTART ({start}) is beyond the end of the file ({total} samples); "
                     "the whole file is shown.")
        return LoopView(*whole, INVALID, tuple(notes))
    if end > total:
        notes.append(f"The loop ends at sample {end}, after the end of the file ({total}); "
                     "it is shown until the end.")
        return LoopView(start, total, INVALID, tuple(notes))
    return LoopView(start, end, status, tuple(notes))


def _disagreements(standard: LoopTags, engine: EngineTags) -> list[str]:
    """Plain-words notes where the game's reader and the Vorbis reading of the file differ."""
    out: list[str] = []
    for name, std, eng in (("LOOPSTART", standard.start, engine.loop_start),
                           ("LOOPLENGTH", standard.length, engine.loop_length)):
        if std is not None and eng != std:
            if not eng:
                out.append(f"RPG Maker does not find {name} in this file. It only finds names written in "
                           "upper case, inside the first part of the comment block.")
            elif str(eng).startswith(str(std)):
                out.append(f"RPG Maker reads {name} as {eng}, but the file says {std}: its reader runs on "
                           "into the next bytes and a digit there is added to the number.")
            else:
                out.append(f"RPG Maker reads {name} as {eng}, but the file says {std}.")
        elif std is None and eng:
            out.append(f"RPG Maker finds {name} = {eng} in the header, but not as a tag of its own.")
    return out


def frames_to_seconds(frames: int, rate: int) -> float:
    return frames / rate if rate else 0.0


def format_time(frames: int, rate: int) -> str:
    """m:ss.mmm for a sample position, e.g. 66150 samples at 44100 Hz -> 0:01.500."""
    if rate <= 0:
        return "0:00.000"
    ms = (abs(frames) * 1000 + rate // 2) // rate             # integers only: huge tag values must not overflow
    sign = "-" if frames < 0 else ""
    return f"{sign}{ms // 60000}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def seam_start(view: LoopView, rate: int, lead_seconds: float = 3.0) -> int:
    """Where to start playing to hear the jump from the loop end back to the loop start."""
    return max(0, view.end - int(lead_seconds * rate))


SEAM_TAIL_SECONDS = 15.0          # the seam view shows at most this much of the loop's beginning after the end


def seam_tail(view: LoopView, rate: int) -> int:
    """How many samples of the loop's beginning the seam view shows after the loop end."""
    return max(0, min(view.length, int(SEAM_TAIL_SECONDS * rate)))


def seam_extent(view: LoopView, rate: int) -> int:
    """Length of the seam view's timeline: the file up to the loop end, then the loop's beginning
    (what the game plays after the jump) instead of whatever the file holds after the loop end."""
    return view.end + seam_tail(view, rate)


def seam_source(view: LoopView, frame: int) -> int:
    """The sample of the file that is heard at `frame` of the seam view's timeline."""
    return frame if frame < view.end else view.start + (frame - view.end)


def seam_window(view: LoopView, rate: int, seconds: float = 2.0) -> tuple[int, int]:
    """(first sample, number of samples) of a view centred on the jump, `seconds` each side."""
    half = max(1, int(seconds * rate))
    return view.end - half, 2 * half


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

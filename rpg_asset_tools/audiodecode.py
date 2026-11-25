"""
audiodecode.py
--------------
The one place that decodes an Ogg Vorbis file into samples. It needs two optional
packages, numpy and soundfile; the rest of the app works without them.

numpy is imported only when a file is decoded, so importing this module is always safe.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("numpy", "soundfile")
INSTALL_COMMAND = "pip install numpy soundfile"
MAX_CHANNELS = 2               # RPG Maker music is stereo or mono; extra channels are dropped


class DecodeError(Exception):
    """The file could not be decoded. The text is meant for the user."""


@dataclass
class DecodedAudio:
    samples: "object"          # numpy array, shape (frames, channels), float32
    sample_rate: int
    dropped_channels: int = 0

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])

    @property
    def channels(self) -> int:
        return int(self.samples.shape[1])


def missing_dependencies() -> list[str]:
    """Names of the optional packages that are not installed."""
    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


def missing_message(missing: list[str]) -> str:
    return ("The loop editor needs " + " and ".join(missing) + " to read the sound.\n\n"
            f"Install with:   {INSTALL_COMMAND}\n\n"
            "Everything else in the app works without it.")


def decode(path: str | Path) -> DecodedAudio:
    """Decode the whole file to float32 samples."""
    missing = missing_dependencies()
    if missing:
        raise DecodeError(missing_message(missing))
    import numpy as np
    import soundfile
    try:
        data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    except (RuntimeError, OSError) as e:                       # soundfile.LibsndfileError is a RuntimeError
        raise DecodeError(f"The sound could not be read: {e}") from e
    dropped = 0
    if data.shape[1] > MAX_CHANNELS:
        dropped = data.shape[1] - MAX_CHANNELS
        data = data[:, :MAX_CHANNELS]
    return DecodedAudio(np.ascontiguousarray(data), int(rate), dropped)

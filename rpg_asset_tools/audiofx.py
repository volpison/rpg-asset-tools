"""
audiofx.py
----------
Pitch and pan for the loop editor's playback, the way RPG Maker MZ's audio settings work, without any
sound device. Needs numpy; no Qt.

* pitch  50 .. 150 (100 = normal). MZ plays the sound faster or slower, so the pitch and the speed change
  together; here that is `rate_from_pitch` (the step through the file) used by `loopplayback.render_rate`.
* pan   -100 (left) .. 100 (right), 0 = centre. For a stereo file this is the Web Audio "stereo panner"
  (the engine's panner): moving to one side folds the other channel into it. Other channel counts are
  not panned.
"""

from __future__ import annotations

import math

import numpy as np

PITCH_MIN, PITCH_MAX, PITCH_NORMAL = 50, 150, 100
PAN_MIN, PAN_MAX = -100, 100


def rate_from_pitch(pitch: float) -> float:
    """How many file samples one output sample steps through: 1.0 at pitch 100."""
    return max(PITCH_MIN, min(PITCH_MAX, float(pitch))) / 100.0


def apply_pan(chunk: np.ndarray, pan: float) -> np.ndarray:
    """`chunk` (frames x 2) panned; any other shape, or pan 0, comes back unchanged."""
    if not pan or chunk.ndim != 2 or chunk.shape[1] != 2 or len(chunk) == 0:
        return chunk
    x = max(PAN_MIN, min(PAN_MAX, float(pan))) / 100.0
    left, right = chunk[:, 0], chunk[:, 1]
    if x <= 0:
        a = (x + 1.0) * math.pi / 2.0
        out_l = left + right * math.cos(a)
        out_r = right * math.sin(a)
    else:
        a = x * math.pi / 2.0
        out_l = left * math.cos(a)
        out_r = right + left * math.sin(a)
    return np.stack([out_l, out_r], axis=1).astype(np.float32)

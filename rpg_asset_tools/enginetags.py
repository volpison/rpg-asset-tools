"""
enginetags.py
-------------
What RPG Maker MZ's own reader finds in an .ogg file: a line-by-line port of
`WebAudio._readLoopComments`, `WebAudio._readMetaData` and the loop part of
`WebAudio._onDecode` from js/rmmz_core.js (the file of MZ 1.0.0 that was checked).

Why a port and not "the Vorbis standard": the game does not parse the comment block, it scans
bytes. That has consequences the standard does not have, and all of them are kept here on purpose:

* only the upper-case text "LOOP" starts a match, so `loopstart=` is never found;
* each Ogg page is looked at separately, and a comment block that runs on to a second page
  is not scanned at all (its size comes out as NaN in JavaScript), so tags in it are lost;
* the text of a tag runs until the next zero byte, not until the end of the comment. The number
  is followed by the next comment's length bytes, so if the next byte is an ASCII digit
  ('0' to '9', a comment of 48 to 57 bytes) it becomes part of the number;
* LOOPSTART without LOOPLENGTH is ignored when the file is decoded: the whole file repeats.

Pure Python, read-only. Positions are in samples of the file.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

MAX_READ = 8 * 1024 * 1024          # the headers are at the start; this is far more than enough


@dataclass(frozen=True)
class EngineTags:
    sample_rate: int                # from the Vorbis identification header; 0 when not found
    loop_start: int | None          # None = not a number (JavaScript NaN); 0 when the tag is absent
    loop_length: int | None


def _js_parse_int(text: str) -> int | None:
    """JavaScript parseInt(text): leading digits only, None (NaN) when there are none."""
    m = re.match(r"\s*([+-]?[0-9]+)", text)
    return _to_int(m.group(1)) if m else None


def _to_int(digits: str) -> int | None:
    """int(), but a number too long for Python's digit limit counts as unusable (None)."""
    try:
        return int(digits)
    except ValueError:
        return None


def read_engine_tags(data: bytes) -> EngineTags:
    """Run the engine's header scan over the first bytes of an .ogg file."""
    n = len(data)

    def u8(i: int) -> int:
        # JavaScript would throw a RangeError past the end; stopping the scan is the closest thing
        return data[i] if 0 <= i < n else 0

    def four(i: int) -> str:
        return data[i:i + 4].decode("latin-1") if 0 <= i <= n - 4 else ""

    state = {"rate": 0, "start": 0, "length": 0}      # _loopStart / _loopLength start as 0

    def read_metadata(index: int, size: int | None) -> None:
        if size is None:                              # NaN size: the for-loop never runs
            return
        i = index
        while i < index + size - 10:
            if four(i) == "LOOP":
                text = ""
                while u8(i) > 0:
                    text += chr(u8(i))
                    i += 1
                m = re.search(r"LOOPSTART=([0-9]+)", text)
                if m:
                    state["start"] = _to_int(m.group(1))
                m = re.search(r"LOOPLENGTH=([0-9]+)", text)
                if m:
                    state["length"] = _to_int(m.group(1))
                if text in ("LOOPSTART", "LOOPLENGTH"):
                    text2 = ""
                    i += 16
                    while u8(i) > 0:
                        text2 += chr(u8(i))
                        i += 1
                    state["start" if text == "LOOPSTART" else "length"] = _js_parse_int(text2)
            i += 1

    index: int | None = 0
    while index is not None and index < n - 30:
        if four(index) != "OggS":
            break
        index += 26
        num_segments = u8(index)
        index += 1
        segments = [u8(index + k) for k in range(num_segments)]
        index += num_segments
        packets: list[int | None] = []
        while segments:
            size: int | None = 0
            while segments and segments[0] == 255:
                size += segments.pop(0)
            if segments:
                size += segments.pop(0)
            else:
                size = None                           # JavaScript: number + undefined = NaN
            packets.append(size)
        vorbis_header_found = False
        for size in packets:
            if four(index + 1) == "vorb":
                header_type = u8(index)
                if header_type == 1:
                    state["rate"] = struct.unpack_from("<I", data, index + 12)[0] \
                        if index + 16 <= n else 0
                elif header_type == 3:
                    read_metadata(index, size)
                vorbis_header_found = True
            index = None if size is None else index + size
            if index is None:
                break
        if not vorbis_header_found:
            break
    return EngineTags(state["rate"], state["start"], state["length"])


def read_engine_tags_from_file(path: str | Path) -> EngineTags:
    with open(path, "rb") as f:
        return read_engine_tags(f.read(MAX_READ))


@dataclass(frozen=True)
class EngineLoop:
    start: int                      # samples
    length: int                     # samples
    from_tags: bool                 # False: no usable loop, the whole file repeats


def engine_loop(tags: EngineTags, total_frames: int) -> EngineLoop:
    """The loop the game uses after decoding (WebAudio._onDecode): the tags count only when
    LOOPLENGTH is a positive number and the sample rate is known; otherwise the whole file."""
    ok = (tags.loop_length is not None and tags.loop_length > 0
          and tags.loop_start is not None and tags.sample_rate > 0)
    if ok:
        return EngineLoop(tags.loop_start, tags.loop_length, True)
    return EngineLoop(0, total_frames, False)

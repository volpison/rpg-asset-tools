"""
oggmeta.py
----------
Reads the header of an Ogg Vorbis file: sample rate, channel count and the
comment tags (where RPG Maker keeps LOOPSTART and LOOPLENGTH).

Pure Python, standard library only, read-only. It reads pages from the start of
the file until the comment packet is complete, so it is fast on big files and
never touches the audio data.

Errors are raised as OggError with a message that can be shown to the user as is.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

MAX_PACKET = 16 * 1024 * 1024        # a header packet bigger than this is not a real header


class OggError(Exception):
    """The file is not a usable Ogg Vorbis file. The text is meant for the user."""


@dataclass(frozen=True)
class OggInfo:
    sample_rate: int
    channels: int
    vendor: str
    comments: tuple[tuple[str, str], ...]       # (name as stored, value), in file order


@dataclass(frozen=True)
class LoopTags:
    start: int | None                           # LOOPSTART in samples, None when absent or unusable
    length: int | None                          # LOOPLENGTH in samples
    notes: tuple[str, ...]                      # plain-words remarks about what was found


# --------------------------------------------------------------------------
# Ogg container
# --------------------------------------------------------------------------

def _packets(f: BinaryIO) -> Iterator[bytes]:
    """Yield the packets of the stream in order, joining packets that span several pages."""
    cur = bytearray()
    while True:
        head = f.read(27)
        if not head:
            return
        if len(head) < 27 or head[:4] != b"OggS":
            raise OggError("This is not an Ogg file (or it is damaged).")
        nseg = head[26]
        table = f.read(nseg)
        if len(table) < nseg:
            raise OggError("The file ends in the middle of its header.")
        body = f.read(sum(table))
        if len(body) < sum(table):
            raise OggError("The file ends in the middle of its header.")
        off = 0
        for lace in table:
            cur += body[off:off + lace]
            off += lace
            if len(cur) > MAX_PACKET:
                raise OggError("The header of this file is damaged (it is far too large).")
            if lace < 255:                       # a lacing value below 255 ends the packet
                yield bytes(cur)
                cur = bytearray()


def _parse_comments(packet: bytes) -> tuple[str, tuple[tuple[str, str], ...]]:
    data = packet[7:]
    bad = OggError("The comment block of this file is damaged.")

    def u32(pos: int) -> int:
        if pos + 4 > len(data):
            raise bad
        return struct.unpack_from("<I", data, pos)[0]

    vlen = u32(0)
    if 4 + vlen > len(data):
        raise bad
    vendor = data[4:4 + vlen].decode("utf-8", "replace")
    pos = 4 + vlen
    count = u32(pos)
    pos += 4
    out: list[tuple[str, str]] = []
    for _ in range(count):
        n = u32(pos)
        pos += 4
        if pos + n > len(data):
            raise bad
        text = data[pos:pos + n].decode("utf-8", "replace")
        pos += n
        name, sep, value = text.partition("=")
        if sep:
            out.append((name, value))
    return vendor, tuple(out)


def read_ogg_info(path: str | Path) -> OggInfo:
    """Sample rate, channels and comments of an Ogg Vorbis file."""
    try:
        with open(path, "rb") as f:
            packets = _packets(f)
            ident = next(packets, None)
            if ident is None:
                raise OggError("The file is empty.")
            if ident.startswith(b"OpusHead"):
                raise OggError("This is an Ogg Opus file, not Ogg Vorbis. "
                               "RPG Maker MZ expects Ogg Vorbis (.ogg).")
            if ident[:7] != b"\x01vorbis" or len(ident) < 16:
                raise OggError("This is an Ogg file, but not Ogg Vorbis audio.")
            channels = ident[11]
            rate = struct.unpack_from("<I", ident, 12)[0]
            comment = next(packets, None)
            if comment is None or comment[:7] != b"\x03vorbis":
                raise OggError("The comment block of this file is missing or damaged.")
            vendor, comments = _parse_comments(comment)
    except OSError as e:
        raise OggError(f"The file could not be read: {e.strerror or e}") from e
    return OggInfo(rate, channels, vendor, comments)


# --------------------------------------------------------------------------
# loop tags
# --------------------------------------------------------------------------

def loop_tags(info: OggInfo) -> LoopTags:
    """Pick LOOPSTART and LOOPLENGTH out of the comments (field names are case-insensitive in
    the Vorbis format; a note says when a name is not stored in upper case)."""
    found: dict[str, int | None] = {"LOOPSTART": None, "LOOPLENGTH": None}
    seen: set[str] = set()
    notes: list[str] = []
    for name, value in info.comments:
        key = name.upper()
        if key not in found:
            continue
        if key in seen:
            notes.append(f"{key} appears more than once; the first one is used.")
            continue
        seen.add(key)
        if name != key:
            notes.append(f"{key} is stored as '{name}' (not in upper case).")
        text = value.strip()
        if text.isascii() and text.isdigit():
            try:
                found[key] = int(text)
            except ValueError:                   # Python limits very long digit strings
                notes.append(f"{key} has a number with {len(text)} digits, which is not a real "
                             "sample position; it is ignored.")
        else:
            notes.append(f"{key} has the value '{value}', which is not a whole number; it is ignored.")
    return LoopTags(found["LOOPSTART"], found["LOOPLENGTH"], tuple(notes))

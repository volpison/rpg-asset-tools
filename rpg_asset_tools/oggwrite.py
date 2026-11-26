"""
oggwrite.py
-----------
Writes LOOPSTART / LOOPLENGTH into an Ogg Vorbis file by rewriting only its comment header.
The audio pages are copied byte for byte (when the number of header pages stays the same, which
is the usual case, they are not touched at all).

The result is made to be read correctly by RPG Maker MZ's own scanner (see enginetags.py):

* the names are written in upper case;
* the comment packet gets a page of its own and must fit in it (a comment block that runs on to
  a second page hides its tags from the game, so such a file is refused with a plain message);
* LOOPSTART and LOOPLENGTH are the last comments, so the byte after the LOOPSTART number is the
  first length byte of LOOPLENGTH (11 + digits, never an ASCII digit) and after LOOPLENGTH
  comes the framing byte 0x01;
* every other comment is kept exactly as it was.

`write_loop_tags` never writes over its source. `verify_written` checks the new file against
the old one (identical audio, identical identification and setup headers, the tags the game
reads), and the caller is expected to refuse to use the file when that fails.

Pure Python, standard library only. Errors are OggWriteError with a message for the user.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

import enginetags

MAX_VALUE = 10 ** 15                    # far beyond any sample count; keeps the length byte below '0'
MAX_COMMENT_PACKET = 254 * 255 + 254    # the most one Ogg page can hold of a packet that ends on it
LOOP_NAMES = (b"LOOPSTART", b"LOOPLENGTH")


class OggWriteError(Exception):
    """The tags could not be written. The text is meant for the user."""


# --------------------------------------------------------------------------
# CRC of an Ogg page (CRC-32, polynomial 0x04C11DB7, not reflected, start value 0)
# --------------------------------------------------------------------------

def _make_table() -> list[int]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if r & 0x80000000 else (r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


_TABLE = _make_table()


def page_crc(page: bytes) -> int:
    """CRC of a whole page whose own CRC field is taken as zero."""
    crc = 0
    t = _TABLE
    for b in page[:22]:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ t[(crc >> 24) ^ b]
    for _ in range(4):
        crc = ((crc << 8) & 0xFFFFFFFF) ^ t[crc >> 24]
    for b in page[26:]:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ t[(crc >> 24) ^ b]
    return crc


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------

@dataclass
class Page:
    header_type: int
    granule: int                        # unsigned 64 bit as stored
    serial: int
    seq: int
    lacing: bytes
    body: bytes
    raw: bytes                          # the page exactly as it was in the file

    @property
    def packet_ends(self) -> int:
        """How many packets finish on this page."""
        return sum(1 for v in self.lacing if v < 255)

    @property
    def last_packet_complete(self) -> bool:
        return bool(self.lacing) and self.lacing[-1] < 255


def read_pages(data: bytes) -> list[Page]:
    pages: list[Page] = []
    pos, n = 0, len(data)
    while pos < n:
        if data[pos:pos + 4] != b"OggS" or pos + 27 > n:
            raise OggWriteError("The file is damaged: it is not made of Ogg pages all the way.")
        nseg = data[pos + 26]
        end_table = pos + 27 + nseg
        if end_table > n:
            raise OggWriteError("The file ends in the middle of a page.")
        lacing = data[pos + 27:end_table]
        end = end_table + sum(lacing)
        if end > n:
            raise OggWriteError("The file ends in the middle of a page.")
        header_type = data[pos + 5]
        granule, serial, seq = struct.unpack_from("<QII", data, pos + 6)
        pages.append(Page(header_type, granule, serial, seq, bytes(lacing),
                          data[end_table:end], data[pos:end]))
        pos = end
    return pages


def build_page(header_type: int, granule: int, serial: int, seq: int, lacing: bytes, body: bytes) -> bytes:
    head = (b"OggS\x00" + bytes([header_type]) + struct.pack("<QII", granule, serial, seq)
            + b"\x00\x00\x00\x00" + bytes([len(lacing)]) + lacing)
    page = head + body
    return page[:22] + struct.pack("<I", page_crc(page)) + page[26:]


def _lacing_for(length: int) -> bytes:
    """Lacing values for one packet of `length` bytes (a multiple of 255 needs a closing 0)."""
    return bytes([255] * (length // 255) + [length % 255])


# --------------------------------------------------------------------------
# the header packets
# --------------------------------------------------------------------------

@dataclass
class _Layout:
    ident: Page
    comment: bytes                      # the comment packet
    setup: bytes
    header_pages: int                   # pages the three headers occupy in the file
    serial: int


def _layout(pages: list[Page]) -> _Layout:
    if not pages:
        raise OggWriteError("The file is empty.")
    ident = pages[0]
    if not ident.body.startswith(b"\x01vorbis"):
        if ident.body.startswith(b"OpusHead"):
            raise OggWriteError("This is an Ogg Opus file, not Ogg Vorbis, so loop tags cannot be written.")
        raise OggWriteError("This is not an Ogg Vorbis file.")
    if ident.packet_ends != 1 or not ident.last_packet_complete or len(ident.body) < 16:
        raise OggWriteError("The identification header of this file is unusual; it was left alone.")
    serial = ident.serial
    packets: list[bytes] = []
    cur = bytearray()
    used = 1
    for page in pages[1:]:
        if page.serial != serial:
            raise OggWriteError("This file holds more than one audio stream; it was left alone.")
        off = 0
        for lace in page.lacing:
            cur += page.body[off:off + lace]
            off += lace
            if lace < 255:
                packets.append(bytes(cur))
                cur = bytearray()
        used += 1
        if len(packets) >= 2:
            if cur or not page.last_packet_complete:
                raise OggWriteError("The headers of this file share a page with the sound; "
                                    "it was left alone.")
            break
    if len(packets) < 2 or not packets[0].startswith(b"\x03vorbis") or not packets[1].startswith(b"\x05vorbis"):
        raise OggWriteError("The comment or setup header of this file is missing or damaged.")
    return _Layout(ident, packets[0], packets[1], used, serial)


def _split_comments(packet: bytes) -> tuple[bytes, list[bytes]]:
    """(vendor bytes, raw comment entries) of a comment packet."""
    bad = OggWriteError("The comment block of this file is damaged.")
    data = packet[7:]
    if len(data) < 4:
        raise bad
    vlen = struct.unpack_from("<I", data, 0)[0]
    if 4 + vlen + 4 > len(data):
        raise bad
    vendor = data[4:4 + vlen]
    pos = 4 + vlen
    count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    entries = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise bad
        n = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if pos + n > len(data):
            raise bad
        entries.append(data[pos:pos + n])
        pos += n
    return vendor, entries


def _is_loop_entry(entry: bytes) -> bool:
    name = entry.split(b"=", 1)[0].upper()
    return name in LOOP_NAMES


def build_comment_packet(old_packet: bytes, start: int | None, length: int | None) -> bytes:
    """The comment packet with LOOPSTART / LOOPLENGTH replaced (None removes a tag)."""
    vendor, entries = _split_comments(old_packet)
    kept = [e for e in entries if not _is_loop_entry(e)]
    if start is not None:
        kept.append(b"LOOPSTART=%d" % start)
    if length is not None:
        kept.append(b"LOOPLENGTH=%d" % length)
    out = bytearray(b"\x03vorbis")
    out += struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(kept))
    for e in kept:
        out += struct.pack("<I", len(e)) + e
    out += b"\x01"
    return bytes(out)


def _check_values(start: int | None, length: int | None) -> None:
    for name, v in (("LOOPSTART", start), ("LOOPLENGTH", length)):
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0 or v >= MAX_VALUE):
            raise OggWriteError(f"{name} must be a whole number of samples, 0 or more.")
    if (start is None) != (length is None):
        raise OggWriteError("Write both LOOPSTART and LOOPLENGTH, or neither: the game ignores a loop "
                            "that has only one of them.")
    if length is not None and length <= 0:
        raise OggWriteError("LOOPLENGTH must be more than 0; the game ignores a loop of length 0.")


def rewrite_headers(data: bytes, start: int | None, length: int | None) -> bytes:
    """The whole file with new loop tags. Raises OggWriteError when it should not be changed."""
    _check_values(start, length)
    pages = read_pages(data)
    lay = _layout(pages)
    comment = build_comment_packet(lay.comment, start, length)
    if len(comment) > MAX_COMMENT_PACKET:
        raise OggWriteError(
            "The comment block of this file is too big (it probably holds a picture or a long text). "
            "RPG Maker cannot read loop tags from a comment block that long, so nothing was changed. "
            "Remove the big comment with an audio editor first.")

    serial = lay.serial
    new: list[bytes] = [lay.ident.raw]
    seq = 1
    new.append(build_page(0, 0, serial, seq, _lacing_for(len(comment)), comment))
    seq += 1
    setup = lay.setup
    laces = _lacing_for(len(setup))
    off_l = off_b = 0
    first = True
    while off_l < len(laces):
        chunk = laces[off_l:off_l + 255]
        size = sum(chunk)
        done = off_l + len(chunk) >= len(laces)
        header_type = 0 if first else 1
        granule = 0 if done else 0xFFFFFFFFFFFFFFFF
        new.append(build_page(header_type, granule, serial, seq, chunk, setup[off_b:off_b + size]))
        seq += 1
        off_l += len(chunk)
        off_b += size
        first = False
    new_header_pages = len(new)

    rest = pages[lay.header_pages:]
    renumber = new_header_pages != lay.header_pages
    for page in rest:
        if renumber:
            new.append(build_page(page.header_type, page.granule, page.serial, seq,
                                  page.lacing, page.body))
        else:
            new.append(page.raw)
        seq += 1
    return b"".join(new)


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def _audio_signature(pages: list[Page], first_audio: int) -> str:
    h = hashlib.sha256()
    for p in pages[first_audio:]:
        h.update(struct.pack("<BQ", p.header_type, p.granule))
        h.update(p.lacing)
        h.update(p.body)
    return h.hexdigest()


def verify_written(original: bytes, written: bytes, start: int | None, length: int | None) -> None:
    """Raise OggWriteError unless `written` is `original` with exactly the loop tags changed."""
    bad = OggWriteError("The new file did not pass the safety check, so it was not used.")
    try:
        a_pages, b_pages = read_pages(original), read_pages(written)
        a, b = _layout(a_pages), _layout(b_pages)
    except OggWriteError as e:
        raise OggWriteError(f"The new file did not pass the safety check ({e}), so it was not used.") from e
    for p in b_pages:                                           # every page has a valid CRC
        zeroed = p.raw[:22] + b"\x00\x00\x00\x00" + p.raw[26:]
        if page_crc(zeroed) != struct.unpack_from("<I", p.raw, 22)[0]:
            raise bad
    if [p.seq for p in b_pages] != list(range(len(b_pages))):
        raise bad
    if a.ident.raw != b.ident.raw or a.setup != b.setup:
        raise bad
    if _audio_signature(a_pages, a.header_pages) != _audio_signature(b_pages, b.header_pages):
        raise bad
    _, old_entries = _split_comments(a.comment)
    _, new_entries = _split_comments(b.comment)
    if [e for e in old_entries if not _is_loop_entry(e)] != [e for e in new_entries if not _is_loop_entry(e)]:
        raise bad
    seen = enginetags.read_engine_tags(written)
    want_start, want_length = (0, 0) if start is None else (start, length)
    if (seen.loop_start, seen.loop_length) != (want_start, want_length):
        raise OggWriteError(
            f"RPG Maker's own reader would find LOOPSTART {seen.loop_start} and LOOPLENGTH "
            f"{seen.loop_length} in the new file, not {want_start} and {want_length}, so it was not used.")
    if seen.sample_rate != struct.unpack_from("<I", a.ident.body, 12)[0] or seen.sample_rate <= 0:
        raise bad


def write_loop_tags(src: str | Path, dst: str | Path, start: int | None, length: int | None) -> None:
    """Write a copy of `src` with new loop tags to `dst`, after checking it. `src` is not changed."""
    try:
        original = Path(src).read_bytes()
    except OSError as e:
        raise OggWriteError(f"The file could not be read: {e.strerror or e}") from e
    written = rewrite_headers(original, start, length)
    verify_written(original, written, start, length)
    try:
        Path(dst).write_bytes(written)
    except OSError as e:
        raise OggWriteError(f"The new file could not be written: {e.strerror or e}") from e

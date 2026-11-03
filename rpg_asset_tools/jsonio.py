"""
jsonio.py
---------
Read/write RPG Maker data files without reformatting them.

MZ saves compact JSON (top-level arrays one element per line, e.g.
Actors.json). Re-saving with indent=2 would turn a one-field rename into a
whole-file diff in Git. So before touching a file we find the writer that
reproduces the ORIGINAL text byte for byte; if none does, we fall back to
indent=2 (still valid, just noisier).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _c(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _flat(data) -> str:
    return _c(data)


def _list_lines(items: list) -> str:
    return "[\n" + ",\n".join(_c(x) for x in items) + "\n]"


def _top_list_lines(data) -> str:
    return _list_lines(data) if isinstance(data, list) else _c(data)


def _dict_events_lines(data) -> str:
    """Map files: compact object, but the 'events' array is one event per line."""
    if not isinstance(data, dict):
        return _c(data)
    parts = []
    for k, v in data.items():
        if k == "events" and isinstance(v, list):
            parts.append(f"{_c(k)}:{_list_lines(v)}")
        else:
            parts.append(f"{_c(k)}:{_c(v)}")
    return "{" + ",".join(parts) + "}"


def _indent2(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


_WRITERS = (_flat, _top_list_lines, _dict_events_lines)


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def detect_writer(original_text: str, data):
    """Pick the writer that reproduces `original_text` from the untouched `data`."""
    norm = original_text.replace("\r\n", "\n").rstrip("\n")
    for writer in _WRITERS:
        try:
            if writer(data) == norm:
                return writer
        except Exception:
            continue
    return _indent2


def write_json_atomic(path: Path, data, writer, newline_at_end: bool = False) -> None:
    text = writer(data) + ("\n" if newline_at_end else "")
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp_rpgat")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

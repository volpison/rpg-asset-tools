"""
batch_tools.py
--------------
Pure-Python helpers behind the Batch Rename dialog (no Qt, unit-tested):
find/replace, prefix/suffix, and reading "old -> new" lists from pasted text,
CSV/TXT or the old rename_mapping.json format.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SEPARATORS = ("\t", "->", "=>", ";", ",")


def apply_find_replace(name: str, find: str, repl: str, regex: bool = False,
                       case_sensitive: bool = True) -> str:
    """Apply to the file name only; folders in the name are left alone unless `find` hits them."""
    if not find:
        return name
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = find if regex else re.escape(find)
    try:
        return re.sub(pattern, repl, name, flags=flags)
    except re.error:
        return name


def add_prefix_suffix(name: str, prefix: str = "", suffix: str = "") -> str:
    """Prefix/suffix go on the file name, not on the folder part."""
    folder, _, base = name.rpartition("/")
    base = f"{prefix}{base}{suffix}"
    return f"{folder}/{base}" if folder else base


def set_folder(name: str, folder: str) -> str:
    """Move to `folder` (relative to the asset root; '' = root), keeping the file name."""
    base = name.rpartition("/")[2]
    folder = folder.strip().strip("/")
    return f"{folder}/{base}" if folder else base


def parse_mapping_text(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """
    Accepts one rule per line:  old<TAB>new   old,new   old;new   old->new   old=>new
    (Spreadsheet copy/paste uses TAB.) A line with NO separator is returned as
    ("", name) = positional new name, to be matched by row order.
    Returns (pairs, warnings).
    """
    pairs, warnings = [], []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for sep in _SEPARATORS:
            if sep in line:
                old, _, new = line.partition(sep)
                old, new = old.strip().strip('"'), new.strip().strip('"')
                if old and new:
                    pairs.append((old, new))
                else:
                    warnings.append(f"line {n}: empty name, skipped")
                break
        else:
            pairs.append(("", line.strip('"')))
    return pairs, warnings


def parse_mapping_file(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("mapping JSON must be an object of old -> new")
        return [(str(k), str(v)) for k, v in data.items()], []
    return parse_mapping_text(text)


def apply_pairs_to_rows(rows: list[str], pairs: list[tuple[str, str]]) -> tuple[dict[int, str], list[str]]:
    """
    rows = current names in table order. Returns ({row_index: new_name}, warnings).
    Named pairs match by old name; positional pairs fill rows in order.
    """
    result, warnings = {}, []
    by_name = {n: i for i, n in enumerate(rows)}
    positional = [new for old, new in pairs if not old]
    for old, new in pairs:
        if old:
            if old in by_name:
                result[by_name[old]] = new
            else:
                warnings.append(f"'{old}' is not in the selection, ignored")
    if positional:
        if len(positional) != len(rows):
            warnings.append(f"{len(positional)} new names pasted but {len(rows)} rows selected")
        else:
            for i, new in enumerate(positional):
                result[i] = new
    return result, warnings

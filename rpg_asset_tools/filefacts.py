"""
filefacts.py
------------
Remembers cheap-to-keep facts about asset FILES between runs: image width/height and SHA-256.

Why: on Windows every file open can cost several milliseconds (antivirus scans each one), so
opening 5,000 PNGs just to read their size took ~45 s.  A fact is reused while the file's size
and modification time are unchanged.  JSON only; deleting .asset_tools/cache/filefacts.json is
always safe (the next run is just slower).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

VERSION = 1


class FileFacts:
    def __init__(self, game_folder: Path, enabled: bool = True):
        self.game = Path(game_folder)
        self.enabled = enabled
        self.path = self.game / ".asset_tools" / "cache" / "filefacts.json"
        self._d: dict = {}
        self._dirty = False
        self._lock = threading.Lock()
        if enabled:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and raw.get("v") == VERSION and isinstance(raw.get("f"), dict):
                    self._d = raw["f"]
            except (OSError, ValueError):
                pass

    def get(self, rel: str, size: int, mtime: int, key: str):
        e = self._d.get(rel)
        if e and e.get("s") == size and e.get("m") == mtime:
            return e.get(key)
        return None

    def put(self, rel: str, size: int, mtime: int, key: str, value) -> None:
        with self._lock:
            e = self._d.get(rel)
            if not e or e.get("s") != size or e.get("m") != mtime:
                e = {"s": size, "m": mtime}
                self._d[rel] = e
            e[key] = value
            self._dirty = True

    def save(self) -> None:
        if not (self.enabled and self._dirty):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp_rpgat")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": VERSION, "f": self._d}, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass                                   # a cache that cannot be written is just not used

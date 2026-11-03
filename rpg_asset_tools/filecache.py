"""
filecache.py
------------
Remembers what each big data file contained, so reopening a project only re-reads
the files that changed.

  .asset_tools/cache/<section>__<file>.json     {"v", "fp", "sig", "data"}

  sig  = (size, modification time) of the data file
  fp   = fingerprint of everything else the result depends on: this tool's cache version,
         js/plugins.js, the plugin headers and your plugin_params.json
Any difference means "recompute".  JSON only (never pickle): a project folder you downloaded
must not be able to run code through a cache file.  The cache is disposable: deleting
.asset_tools/cache/ only makes the next open slower.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

CACHE_VERSION = 3
MIN_BYTES = 30_000          # bigger files get a cache file each; smaller ones share one (see below)


def signature(path: Path) -> list:
    st = path.stat()
    return [st.st_size, st.st_mtime_ns]


def fingerprint(game: Path) -> str:
    h = hashlib.sha1(f"v{CACHE_VERSION}".encode())
    game = Path(game)
    items = [game / "js" / "plugins.js", game / ".asset_tools" / "plugin_params.json"]
    pdir = game / "js" / "plugins"
    if pdir.is_dir():
        items += sorted(pdir.glob("*.js"))
    for p in items:
        try:
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns};".encode())
        except OSError:
            h.update(f"{p.name}:-;".encode())
    return h.hexdigest()


class FileCache:
    def __init__(self, game_folder: Path, enabled: bool = True):
        self.game = Path(game_folder)
        self.enabled = enabled
        self.fp = fingerprint(self.game) if enabled else ""
        self.dir = self.game / ".asset_tools" / "cache"
        # Small files (a project can have hundreds of tiny maps): one shared JSON per section.
        # Re-opening each of them costs milliseconds on Windows (antivirus), more than re-parsing.
        self._small: dict[str, dict] = {}
        self._small_seen: dict[str, set] = {}
        self._small_dirty: set = set()

    def _path(self, section: str, name: str) -> Path:
        return self.dir / f"{section}__{name}.json"

    def worth_caching(self, sig: list) -> bool:
        return self.enabled and sig[0] >= MIN_BYTES

    # ---- shared store for small files
    def _small_path(self, section: str) -> Path:
        return self.dir / f"small__{section}.json"

    def _small_load(self, section: str) -> dict:
        if section not in self._small:
            d: dict = {}
            try:
                with open(self._small_path(section), "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if (isinstance(raw, dict) and raw.get("v") == CACHE_VERSION and raw.get("fp") == self.fp
                        and isinstance(raw.get("e"), dict)):
                    d = raw["e"]
            except (OSError, ValueError):
                pass
            self._small[section] = d
            self._small_seen[section] = set()
        return self._small[section]

    def flush(self) -> None:
        """Write the shared small-file stores that changed. Call once at the end of a scan."""
        for section in list(self._small_dirty):
            store = self._small.get(section, {})
            seen = self._small_seen.get(section, set())
            keep = {n: e for n, e in store.items() if n in seen}      # forget deleted files
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                tmp = self._small_path(section).with_suffix(".tmp_rpgat")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"v": CACHE_VERSION, "fp": self.fp, "e": keep}, f,
                              ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, self._small_path(section))
                self._small_dirty.discard(section)
            except (OSError, TypeError, ValueError):
                pass

    def get(self, section: str, name: str, sig: list):
        if not self.enabled:
            return None
        if sig[0] < MIN_BYTES:
            store = self._small_load(section)
            self._small_seen[section].add(name)
            e = store.get(name)
            if isinstance(e, dict) and e.get("sig") == sig:
                return e.get("data")
            return None
        try:
            with open(self._path(section, name), "r", encoding="utf-8") as f:
                e = json.load(f)
        except (OSError, ValueError):
            return None
        if (isinstance(e, dict) and e.get("v") == CACHE_VERSION and e.get("fp") == self.fp
                and e.get("sig") == sig):
            return e.get("data")
        return None

    def put(self, section: str, name: str, sig: list, data) -> None:
        if not self.enabled:
            return
        if sig[0] < MIN_BYTES:
            store = self._small_load(section)
            store[name] = {"sig": sig, "data": data}
            self._small_seen[section].add(name)
            self._small_dirty.add(section)
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(section, name).with_suffix(".tmp_rpgat")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": CACHE_VERSION, "fp": self.fp, "sig": sig, "data": data}, f,
                          ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self._path(section, name))
        except (OSError, TypeError, ValueError):
            pass                                     # a cache that cannot be written is just slower

    def clear(self) -> int:
        n = 0
        if self.dir.is_dir():
            for p in self.dir.glob("*.json"):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        return n

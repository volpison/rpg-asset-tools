"""
annotations.py
--------------
Per-project user data, all kept in <game>/.asset_tools/ so the game folder
itself stays clean:

  comments.json   notes per asset
  marks.json      colour per asset
  ignore.json     "keep / not an orphan" flags
  history.jsonl   operation log (used by Undo)
  backups/        data/ backups, one per operation

Keys are "category:name". Files written by the first version of this tool
(asset_comments.json etc. in the game root) are migrated automatically.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

TOOL_DIR_NAME = ".asset_tools"

_LEGACY = {
    "asset_comments.json": "comments.json",
    "asset_marks.json": "marks.json",
    "asset_ignore.json": "ignore.json",
    "history.jsonl": "history.jsonl",
}

PRESET_COLORS = {
    "Mint Green": "#B6F2D1", "Sage": "#D3E08C", "Lavender": "#DAB1DA",
    "Lilac": "#C1BFFF", "Blush Pink": "#FFC5D3", "Peach": "#FFDBBB",
    "Apricot": "#F7D78D", "Powder Blue": "#BADDFF", "Sky Blue": "#94B5FD",
}


def tool_dir(game_folder: Path, create: bool = True) -> Path:
    d = Path(game_folder) / TOOL_DIR_NAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def migrate_legacy_files(game_folder: Path, log=None) -> None:
    game_folder = Path(game_folder)
    for old, new in _LEGACY.items():
        src = game_folder / old
        if src.is_file():
            dst = tool_dir(game_folder) / new
            if not dst.exists():
                shutil.move(str(src), str(dst))
                if log:
                    log(f"Moved {old} -> {TOOL_DIR_NAME}/{new}")


def key_of(category: str | None, name: str) -> str:
    return f"{(category or 'unknown')}:{name}"


def normalize_hex(color: str) -> str | None:
    c = (color or "").strip().lstrip("#")
    if not c:
        return ""
    if len(c) != 6:
        return None
    try:
        int(c, 16)
    except ValueError:
        return None
    return "#" + c.upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Annotations:
    """Loads once, saves on every change (files are tiny)."""

    def __init__(self, game_folder: Path):
        self.game = Path(game_folder)
        migrate_legacy_files(self.game)
        self.comments: dict = self._load("comments.json", "comments")
        self.marks: dict = self._load("marks.json", "marks")
        self.ignore: set = set(self._load_ignore())

    # ---- io --------------------------------------------------------------
    def _path(self, name: str) -> Path:
        return tool_dir(self.game) / name

    def _load(self, filename: str, field: str) -> dict:
        p = tool_dir(self.game, create=False) / filename
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
        entries = data.get(field) if isinstance(data, dict) else None
        return entries if isinstance(entries, dict) else {}

    def _save(self, filename: str, field: str, entries: dict) -> None:
        payload = {"format": 2, field: entries}
        self._path(filename).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _load_ignore(self) -> list:
        p = tool_dir(self.game, create=False) / "ignore.json"
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("ignore", [])
        return [str(x) for x in data] if isinstance(data, list) else []

    # ---- comments --------------------------------------------------------
    def get_comment(self, category, name) -> str:
        e = self.comments.get(key_of(category, name))
        if isinstance(e, dict):
            return e.get("text", "")
        return e if isinstance(e, str) else ""

    def set_comment(self, category, name, text: str) -> None:
        text = (text or "").strip()
        k = key_of(category, name)
        if text:
            if self.get_comment(category, name) == text:
                return
            self.comments[k] = {"text": text, "updated": _now()}
        elif k in self.comments:
            del self.comments[k]
        else:
            return
        self._save("comments.json", "comments", self.comments)

    # ---- colours ---------------------------------------------------------
    def get_color(self, category, name) -> str:
        e = self.marks.get(key_of(category, name))
        if isinstance(e, dict):
            return e.get("color", "")
        return e if isinstance(e, str) else ""

    def set_color(self, category, name, color: str) -> None:
        color = normalize_hex(color) or ""
        k = key_of(category, name)
        if color:
            self.marks[k] = {"color": color, "updated": _now()}
        else:
            self.marks.pop(k, None)
        self._save("marks.json", "marks", self.marks)

    # ---- ignore ----------------------------------------------------------
    def is_ignored(self, category, name) -> bool:
        return key_of(category, name) in self.ignore or name in self.ignore

    def set_ignored(self, category, name, value: bool) -> None:
        k = key_of(category, name)
        if value:
            self.ignore.add(k)
        else:
            self.ignore.discard(k)
            self.ignore.discard(name)
        self._save_ignore()

    def is_flagged(self, key: str) -> bool:
        """Exact-key flag (used for database entries, e.g. 'db:item:5')."""
        return key in self.ignore

    def set_flagged(self, key: str, value: bool) -> None:
        if value:
            self.ignore.add(key)
        else:
            self.ignore.discard(key)
        self._save_ignore()

    def _save_ignore(self) -> None:
        self._path("ignore.json").write_text(
            json.dumps({"format": 1, "ignore": sorted(self.ignore)}, indent=2,
                       ensure_ascii=False) + "\n", encoding="utf-8")

    # ---- rename support --------------------------------------------------
    def migrate(self, category, old: str, new: str) -> None:
        """Carry notes/colour/ignore over when an asset is renamed."""
        ok, nk = key_of(category, old), key_of(category, new)
        changed = {"comments": False, "marks": False, "ignore": False}
        if ok in self.comments:
            self.comments[nk] = self.comments.pop(ok)
            changed["comments"] = True
        if ok in self.marks:
            self.marks[nk] = self.marks.pop(ok)
            changed["marks"] = True
        if ok in self.ignore:
            self.ignore.discard(ok)
            self.ignore.add(nk)
            changed["ignore"] = True
        if changed["comments"]:
            self._save("comments.json", "comments", self.comments)
        if changed["marks"]:
            self._save("marks.json", "marks", self.marks)
        if changed["ignore"]:
            self.set_ignored(category, new, True)

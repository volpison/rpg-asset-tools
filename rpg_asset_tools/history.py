"""
history.py
----------
Append-only operation log (.asset_tools/history.jsonl). Every rename / orphan
move writes one record with enough detail to undo it.
"""

from __future__ import annotations

import json
from pathlib import Path

from annotations import tool_dir


def history_path(game_folder: Path) -> Path:
    return tool_dir(game_folder) / "history.jsonl"


def append_history(game_folder: Path, event: dict) -> None:
    with open(history_path(game_folder), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_history(game_folder: Path) -> list[dict]:
    p = history_path(game_folder)
    if not p.is_file():
        return []
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def last_undoable(game_folder: Path) -> dict | None:
    """Most recent rename/move operation that has not been undone yet."""
    events = read_history(game_folder)
    undone = {e.get("target") for e in events if e.get("op") == "undo"}
    for e in reversed(events):
        if e.get("op") in ("rename", "move_unused", "merge") and e.get("id") not in undone:
            return e
    return None

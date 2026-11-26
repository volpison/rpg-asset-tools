"""
gridstore.py
------------
Remembers the beat grid (BPM, offset, beats per bar) of each song in the project's
.asset_tools/loop_grids.json. It is kept there, not in the audio file, so RPG Maker never sees it.

File format (schema_version 1):
  {"file_type": "rpg_asset_tools.loop_grids", "schema_version": 1, "min_reader_version": 1,
   "grids": {"audio/bgm/Theme.ogg": {"bpm": 120.0, "offset": 22050, "beats_per_bar": 4}}}
Unknown fields (in the file and in each entry) are kept when it is written back. A file from a
newer version of the tool, or one that is damaged, is never overwritten: GridStoreError says so.
The key is the file's path in the project, so renaming the song forgets its grid.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from annotations import tool_dir
from beatgrid import Grid, GridError

FILE_TYPE = "rpg_asset_tools.loop_grids"
SCHEMA_VERSION = 1
FILE_NAME = "loop_grids.json"


class GridStoreError(Exception):
    """The grid file cannot be used. The text is meant for the user."""


def store_path(game_folder: Path) -> Path:
    return tool_dir(game_folder, create=False) / FILE_NAME


def _read(game_folder: Path) -> dict:
    p = store_path(game_folder)
    if not p.is_file():
        return {"file_type": FILE_TYPE, "schema_version": SCHEMA_VERSION,
                "min_reader_version": SCHEMA_VERSION, "grids": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GridStoreError(f"{p.name} in .asset_tools cannot be read ({e}). Beat grids are not "
                             "saved until it is fixed or removed.") from e
    if not isinstance(data, dict) or data.get("file_type") != FILE_TYPE or not isinstance(data.get("grids"), dict):
        raise GridStoreError(f"{p.name} in .asset_tools is not a beat grid file. Beat grids are not saved "
                             "until it is fixed or removed.")
    if int(data.get("min_reader_version", 1)) > SCHEMA_VERSION:
        raise GridStoreError(f"{p.name} was written by a newer version of this tool. Update the tool "
                             "to use beat grids; the file was left alone.")
    return data


def load_grid(game_folder: Path, rel: str) -> Grid | None:
    """The saved grid of a song, or None. Raises GridStoreError when the file is unusable."""
    entry = _read(game_folder)["grids"].get(rel)
    if not isinstance(entry, dict):
        return None
    try:
        return Grid(float(entry["bpm"]), int(entry["offset"]), int(entry.get("beats_per_bar", 4)))
    except (KeyError, TypeError, ValueError, GridError):
        return None                                   # a bad entry is ignored (and replaced on the next save)


def save_grid(game_folder: Path, rel: str, grid: Grid | None) -> None:
    """Remember the grid of a song (None forgets it)."""
    data = _read(game_folder)
    if grid is None:
        data["grids"].pop(rel, None)
    else:
        entry = data["grids"].get(rel)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry.update({"bpm": grid.bpm, "offset": grid.offset, "beats_per_bar": grid.beats_per_bar})
        data["grids"][rel] = entry
    p = store_path(game_folder)
    try:
        tool_dir(game_folder)
        tmp = p.with_name(p.name + ".tmp_rpgat")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        raise GridStoreError(f"The beat grid could not be saved: {e.strerror or e}") from e

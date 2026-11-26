"""
loopwrite.py
------------
Saves new LOOPSTART / LOOPLENGTH values into a project's .ogg file, safely:

1. the original is copied to .asset_tools/backups/<id>/data/<path> (the same place and format
   the rename and move operations use, so the main window's Undo can put it back);
2. the new file is written next to the original under a temporary name and checked
   (oggwrite.verify_written: same audio, same headers, the tags the game will read);
3. only then does it replace the original, and the operation is added to the history.

If anything fails the original is left as it was and nothing is added to the history.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import oggwrite
from annotations import tool_dir
from history import append_history
from jsonio import file_hash
from oggwrite import OggWriteError
from rename_engine import _new_op_id, _prune_backups


def save_loop_tags(game_folder: Path, ogg_path: Path, start: int | None, length: int | None,
                   log=print) -> str:
    """Write the tags; returns the history id. Raises OggWriteError with a message for the user."""
    game = Path(game_folder).resolve()
    src = Path(ogg_path).resolve()
    try:
        rel = src.relative_to(game).as_posix()
    except ValueError:
        raise OggWriteError("This file is not inside the open project, so it was not changed.") from None
    if not src.is_file():
        raise OggWriteError("The file is no longer there.")
    if "/" not in rel:                                  # history keys need a folder part (see rename_engine)
        raise OggWriteError("This file is not in a project folder, so it was not changed.")

    op_id = _new_op_id()
    backup_dir = tool_dir(game) / "backups" / op_id
    backup = backup_dir / "data" / rel
    tmp = src.with_name(src.name + ".loop_tmp")
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, backup)
        log(f"Backup: {backup.relative_to(game).as_posix()}")
        oggwrite.write_loop_tags(src, tmp, start, length)
        try:
            os.replace(tmp, src)
        except PermissionError as e:
            raise OggWriteError("The file is in use by another program (the game or a player?). "
                                "Close it and try again. Nothing was changed.") from e
        except OSError as e:
            raise OggWriteError(f"The file could not be replaced: {e.strerror or e}. "
                                "Nothing was changed.") from e
    except BaseException:
        shutil.rmtree(backup_dir, ignore_errors=True)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    append_history(game, {
        "id": op_id, "op": "loop_tags",
        "ts": datetime.now(timezone.utc).isoformat(),
        "file": rel,
        "loop": {"start": start, "length": length},
        "json_hashes": {rel: file_hash(src)},
        "backup": f"backups/{op_id}",
    })
    _prune_backups(game)
    log(f"Saved LOOPSTART={start} LOOPLENGTH={length} in {rel}. Undo is available.")
    return op_id

"""
watchdog.py
-----------
If the window stops answering for ~8 s, write what every thread is doing to
~/.rpg_asset_tools/hang.log (faulthandler).  The file tells us exactly where a freeze is, so
it can be fixed instead of guessed at.  Costs nothing while the window is healthy.
"""

from __future__ import annotations

import faulthandler
import os
import time
from pathlib import Path

LOG = Path.home() / ".rpg_asset_tools" / "hang.log"


class Watchdog:
    def __init__(self, seconds: int = 8, path: Path = LOG):
        self.seconds = seconds
        self.path = path
        self._f = None
        if os.environ.get("RPGAT_NO_WATCHDOG"):
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._f = open(path, "a", encoding="utf-8")
            self._f.write(f"\n--- session {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            self._f.flush()
        except OSError:
            self._f = None

    def beat(self) -> None:
        """Called by a 1 s timer on the UI thread: proves the UI is alive."""
        if self._f is None:
            return
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(self.seconds, file=self._f)

    def stop(self) -> None:
        faulthandler.cancel_dump_traceback_later()

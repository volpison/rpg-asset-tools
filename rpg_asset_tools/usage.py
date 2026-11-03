"""
usage.py
--------
"Where is this used?"  Turns the raw reference locations the scanners collect
(file / event / page / command) into places a person can actually find in the
RPG Maker MZ editor:

    Town (Map001) > event 3 'Guard' at (12, 8) > page 2 > line 5: Show Picture

RPG Maker has no "open at this spot" link, so the answer is the exact path
(map, event position, page, line) that you can copy.  Pure Python, no Qt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_MAP_RE = re.compile(r"Map(\d+)\.json$", re.I)

_SINGULAR = {"Items.json": "Item", "Weapons.json": "Weapon", "Armors.json": "Armor",
             "Skills.json": "Skill", "States.json": "State", "Actors.json": "Actor",
             "Classes.json": "Class", "Enemies.json": "Enemy", "Animations.json": "Animation",
             "Tilesets.json": "Tileset"}


@dataclass
class UsageItem:
    file: str                    # project-relative, e.g. "data/Map001.json" or "js/plugins.js"
    place: str                   # "Town (Map001)", "Troop 3 'Slimes'", "Item 5 'Key'", ...
    event: str = ""              # "event 3 'Guard' at (12, 8)"
    page: str = ""               # "page 2"
    line: str = ""               # "line 5"
    what: str = ""               # "Show Picture", "sets it", "equipment" ...
    optional: bool = False       # only a disabled plugin
    sort_key: tuple = ()

    def text(self) -> str:
        parts = [self.place, self.event, self.page, self.line]
        s = " > ".join(p for p in parts if p)
        if self.what:
            s += f": {self.what}"
        return s + ("  (disabled plugin)" if self.optional else "")


class Locator:
    """Resolves ids to names / coordinates; every file is read at most once."""

    def __init__(self, game_folder: Path):
        self.game = Path(game_folder)
        self._json: dict = {}
        self._map_names: dict | None = None

    def _load(self, name: str):
        if name not in self._json:
            try:
                self._json[name] = json.loads(
                    (self.game / "data" / name).read_text(encoding="utf-8-sig"))
            except Exception:
                self._json[name] = None
        return self._json[name]

    def map_name(self, map_id: int) -> str:
        if self._map_names is None:
            self._map_names = {}
            infos = self._load("MapInfos.json")
            if isinstance(infos, list):
                for it in infos:
                    if isinstance(it, dict) and isinstance(it.get("id"), int):
                        self._map_names[it["id"]] = str(it.get("name", ""))
        return self._map_names.get(map_id, "")

    def entry(self, file: str, ident) -> dict:
        data = self._load(file)
        if isinstance(data, list) and isinstance(ident, int) and 0 < ident < len(data):
            e = data[ident]
            return e if isinstance(e, dict) else {}
        return {}

    def map_event(self, file: str, ident) -> dict:
        data = self._load(file)
        evs = data.get("events") if isinstance(data, dict) else None
        if isinstance(evs, list) and isinstance(ident, int) and 0 < ident < len(evs):
            e = evs[ident]
            return e if isinstance(e, dict) else {}
        return {}

    # ------------------------------------------------------------------
    def describe(self, file: str, event=None, page=None, command=None,
                 what: str = "", optional: bool = False) -> UsageItem:
        """page/command are 0-based (as the scanners store them)."""
        base = (file or "?").replace("\\", "/")
        page_s = f"page {page + 1}" if isinstance(page, int) else ""
        line_s = f"line {command + 1}" if isinstance(command, int) else ""
        m = _MAP_RE.search(base)
        if m:
            mid = int(m.group(1))
            name = self.map_name(mid)
            place = f"{name} (Map{mid:03d})" if name else f"Map{mid:03d}"
            ev_s = ""
            if event is not None:
                ev = self.map_event(Path(base).name, event)
                nm = f" '{ev.get('name')}'" if ev.get("name") else ""
                at = f" at ({ev.get('x')}, {ev.get('y')})" if "x" in ev and "y" in ev else ""
                ev_s = f"event {event}{nm}{at}"
            return UsageItem(f"data/{Path(base).name}", place, ev_s, page_s, line_s, what, optional,
                             (0, "", mid, event or 0, page or 0, command or 0))
        name_only = Path(base).name
        if name_only in ("Troops.json", "CommonEvents.json"):
            label = "Troop" if name_only == "Troops.json" else "Common event"
            nm = self.entry(name_only, event).get("name")
            place = f"{label} {event}" + (f" '{nm}'" if nm else "")
            return UsageItem(f"data/{name_only}", place, "", page_s, line_s, what, optional,
                             (1, name_only, event or 0, 0, page or 0, command or 0))
        if name_only in _SINGULAR and event is not None:
            nm = self.entry(name_only, event).get("name")
            place = f"{_SINGULAR[name_only]} {event}" + (f" '{nm}'" if nm else "")
            return UsageItem(f"data/{name_only}", place, "", page_s, line_s, what, optional,
                             (2, name_only, event, 0, 0, 0))
        if name_only in ("plugins.js",) or base.startswith("js/"):
            rel = base if base.startswith("js/") else "js/plugins.js"
            return UsageItem(rel, rel, "", "", "", what, optional, (3, rel, 0, 0, 0, 0))
        rel = f"data/{name_only}" if name_only.endswith(".json") else base
        return UsageItem(rel, name_only, "", page_s, line_s, what, optional, (4, name_only, 0, 0, 0, 0))


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def from_asset_refs(loc: Locator, references: list[dict], mentions: list[dict] | None = None
                    ) -> list[UsageItem]:
    """`references` = the "references" list of an asset row (Ref.location() dicts);
    `mentions` = its "mentions" (free text that names it: only *possible* uses)."""
    items = []
    for m in mentions or []:
        file = m.get("file") or "?"
        kind = m.get("kind") or "text"
        if kind == "note" and m.get("event") is None and file.lower().startswith("map"):
            kind = "map note"
        it = loc.describe(file, m.get("event"), m.get("page"), m.get("command"),
                          f"mentioned in {kind} (possible use)")
        items.append(it)
    for r in references:
        what = r.get("context") or ""
        file = r.get("file") or "?"
        if file == "plugins.js":
            it = loc.describe("js/plugins.js", what=what, optional="(plugin disabled)" in what)
        else:
            it = loc.describe(file, r.get("event"), r.get("page"), r.get("command"), what)
        items.append(it)
    return _sorted(items)


_KIND_TEXT = {"set": "sets it", "read": "checks it", "ref": "uses it",
              "start": "starting map setting", "transfer": "transfers the player here",
              "script": "script transfers here", "plugin": "plugin parameter (possible)"}


def from_db_refs(loc: Locator, refs: list) -> list[UsageItem]:
    """`refs` = list[dbscan.DbRef]."""
    items = []
    for r in refs:
        l = r.loc or {}
        what = _KIND_TEXT.get(r.kind, r.kind)
        detail = r.where.split(": ", 1)[1] if ": " in r.where else ""
        if detail:
            what += f" ({detail})"
        items.append(loc.describe(l.get("file") or r.where.split(" ")[0], l.get("event"),
                                  l.get("page"), l.get("command"), what, r.optional))
    return _sorted(items)


def _sorted(items: list[UsageItem]) -> list[UsageItem]:
    return sorted(items, key=lambda i: i.sort_key or (9, "", 0, 0, 0, 0))


def usage_text(title: str, items: list[UsageItem], total: int | None = None) -> str:
    lines = [title, ""]
    lines += [f"- {i.text()}" for i in items]
    if total and total > len(items):
        lines.append(f"... and {total - len(items)} more")
    return "\n".join(lines)

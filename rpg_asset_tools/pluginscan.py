"""
pluginscan.py
-------------
An overview of the plugin FILES, and the one thing that is safe to clean up.

What can be said for certain
  not loaded    js/plugins/X.js exists but js/plugins.js has no entry for it: dead weight
  disabled      entry with "status": false (kept: you may enable it again)
  missing file  entry without a file: the game cannot load it
What can only be hinted (never a verdict)
  an ENABLED plugin that no event command calls and that holds no asset reference is NORMAL
  for plugins that change the engine (menu layouts, message windows ...), so it is never
  reported as unused.

Dependencies come from the plugin headers (@base, @orderAfter, @orderBefore): a file that
another loaded plugin needs is never offered for moving.

Moving is limited to "not loaded" files, goes to js/plugins/_unused/ and can be undone.
Removing a disabled / enabled plugin is the Plugin Manager's job.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import plugin_refs
from filecache import FileCache, signature

UNUSED_PLUGIN_DIR = "_unused"

OK, DISABLED, NOT_LOADED, MISSING, KEPT = "enabled", "disabled", "not_loaded", "missing_file", "kept"
STATUS_TEXT = {OK: "Enabled", DISABLED: "Disabled", NOT_LOADED: "Not loaded (file only)",
               MISSING: "Entry without file", KEPT: "Kept (flagged)"}
NEEDS_ATTENTION = (DISABLED, NOT_LOADED, MISSING)

_BASE = re.compile(r"^\s*\*\s*@base\s+(\S+)", re.M)
_AFTER = re.compile(r"^\s*\*\s*@orderAfter\s+(\S+)", re.M)
_BEFORE = re.compile(r"^\s*\*\s*@orderBefore\s+(\S+)", re.M)
_DESC = re.compile(r"^\s*\*\s*@plugindesc\s+(.+)$", re.M)
_CODE357 = re.compile(r'"code"\s*:\s*357\b')


@dataclass
class PluginRow:
    name: str
    status: str
    note: str = ""
    file: str = ""                       # project-relative, "" when there is none
    size: int = 0
    description: str = ""
    commands_declared: int = 0
    commands_called: int = 0
    asset_refs: int = 0
    bases: list = field(default_factory=list)         # plugins this one needs
    needed_by: list = field(default_factory=list)     # loaded plugins that need this one
    movable: bool = False
    hint: str = ""

    @property
    def key(self) -> str:
        return f"plugin:{self.name}"


@dataclass
class PluginScan:
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _count_calls(data_dir: Path) -> dict:
    """{plugin: {command: n}} from every Plugin Command (code 357) in data/*.json."""
    calls: dict = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("code") == 357 and isinstance(o.get("parameters"), list) and len(o["parameters"]) > 1:
                c = calls.setdefault(str(o["parameters"][0]), {})
                c[str(o["parameters"][1])] = c.get(str(o["parameters"][1]), 0) + 1
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                if isinstance(v, (dict, list)):
                    walk(v)

    cache = FileCache(data_dir.parent)
    for jf in sorted(data_dir.glob("*.json")):
        try:
            sig = signature(jf)
        except OSError:
            continue
        hit = cache.get("pcalls", jf.name, sig)      # big files: reuse what was found last time
        if hit is None:
            before = {p: dict(c) for p, c in calls.items()}
            text = _read(jf)
            if _CODE357.search(text):
                try:
                    walk(json.loads(text.lstrip("﻿")))
                except ValueError:
                    pass
            hit = {}
            for p, c in calls.items():
                d = {k: n - before.get(p, {}).get(k, 0) for k, n in c.items()}
                d = {k: n for k, n in d.items() if n}
                if d:
                    hit[p] = d
            cache.put("pcalls", jf.name, sig, hit)
        else:
            for p, c in hit.items():
                dst = calls.setdefault(p, {})
                for k, n in c.items():
                    dst[k] = dst.get(k, 0) + n
    cache.flush()
    return calls


def scan_plugins(game_folder: Path, scan=None, ann=None) -> PluginScan:
    game = Path(game_folder)
    res = PluginScan()
    folder = game / "js" / "plugins"
    files = {p.stem: p for p in sorted(folder.glob("*.js"))} if folder.is_dir() else {}

    entries: dict = {}
    try:
        pf = plugin_refs.read_plugins_js(game)
    except (ValueError, OSError) as e:
        res.errors.append(f"js/plugins.js: {e}")
        pf = None
    if pf is not None:
        for p in pf.data:
            if isinstance(p, dict) and p.get("name"):
                entries[str(p["name"])] = p
    elif not res.errors and not (game / "js" / "plugins.js").is_file():
        res.errors.append("js/plugins.js not found")
    if res.errors and pf is None and (game / "js" / "plugins.js").is_file():
        return res                         # cannot tell what is loaded: report nothing risky

    schema = plugin_refs.load_schema(game)
    calls = _count_calls(game / "data") if (game / "data").is_dir() else {}
    refs_per: dict = {}
    for r in (getattr(scan, "refs", None) or []):
        ctx = r.context or ""
        m = re.match(r"Plugin command ([^.]+)\.", ctx) or re.match(r"Plugin ([^:]+):", ctx)
        if m:
            refs_per[m.group(1)] = refs_per.get(m.group(1), 0) + 1

    headers: dict = {}
    texts = plugin_refs.plugin_texts(game)               # read once, in parallel (see plugin_refs)
    for name, path in files.items():
        head = texts.get(name, "")[:60000]
        headers[name] = {
            "base": sorted(set(_BASE.findall(head))),
            "desc": (_DESC.search(head).group(1).strip() if _DESC.search(head) else ""),
        }

    loaded = {n for n, e in entries.items() if files.get(n) is not None}
    enabled = {n for n, e in entries.items() if e.get("status", True)}

    names = sorted(set(files) | set(entries), key=str.lower)
    rows = {}
    for name in names:
        e = entries.get(name)
        path = files.get(name)
        row = PluginRow(name=name, status=OK, file=path.relative_to(game).as_posix() if path else "",
                        size=path.stat().st_size if path else 0,
                        description=(headers.get(name, {}).get("desc") or (e or {}).get("description", "")),
                        commands_declared=len(schema.commands.get(name, {})),
                        commands_called=sum(calls.get(name, {}).values()),
                        asset_refs=refs_per.get(name, 0),
                        bases=headers.get(name, {}).get("base", []))
        if e is None:
            row.status = NOT_LOADED
        elif path is None:
            row.status = MISSING
        elif not e.get("status", True):
            row.status = DISABLED
        rows[name] = row

    # who needs whom (only enabled, existing plugins count as needing)
    for name, row in rows.items():
        if name in enabled and name in files:
            for b in row.bases:
                if b in rows:
                    rows[b].needed_by.append(name)

    for row in rows.values():
        if row.status == NOT_LOADED:
            row.movable = not row.needed_by
            row.note = ("not in js/plugins.js: the game never loads it"
                        if not row.needed_by else
                        f"not loaded, but needed (@base) by {', '.join(sorted(row.needed_by))}")
            if row.commands_called:
                row.note += f"; {row.commands_called} event command(s) still call it"
            row.hint = "Safe to move out of js/plugins/." if row.movable else "Keep: another plugin needs it."
        elif row.status == DISABLED:
            row.note = "disabled in the Plugin Manager"
            row.hint = ("Keep if you may enable it again. To remove it, delete its entry in the "
                        "Plugin Manager (then the file is not loaded).")
            if row.needed_by:
                row.hint = f"Needed (@base) by {', '.join(sorted(row.needed_by))}."
        elif row.status == MISSING:
            row.note = "listed in js/plugins.js but the file is missing"
            row.hint = "The game cannot load it: remove the entry in the Plugin Manager or restore the file."
        else:
            bits = []
            if row.commands_called:
                bits.append(f"{row.commands_called} command call(s)")
            if row.asset_refs:
                bits.append(f"{row.asset_refs} asset reference(s)")
            if row.needed_by:
                bits.append(f"needed by {', '.join(sorted(row.needed_by))}")
            row.note = ", ".join(bits)
            if not bits:
                row.hint = ("Nothing visible uses it: normal for plugins that change the engine "
                            "(menus, windows, battle). Not reported as unused.")
        if ann is not None and ann.is_flagged(row.key) and row.status in NEEDS_ATTENTION:
            row.status, row.movable, row.note = KEPT, False, "flagged to keep"
        res.rows.append(row)
    return res


def export_plugins_csv(rows: list, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["Plugin", "Status", "File", "Bytes", "Commands declared", "Commands called",
                     "Asset references", "Needs (@base)", "Needed by", "Note", "Hint"])
        for r in rows:
            wr.writerow([r.name, STATUS_TEXT.get(r.status, r.status), r.file, r.size, r.commands_declared,
                         r.commands_called, r.asset_refs, " ".join(r.bases), " ".join(r.needed_by),
                         r.note, r.hint])

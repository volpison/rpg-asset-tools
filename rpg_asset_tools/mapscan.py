"""
mapscan.py
----------
Which maps does the game actually use?

Maps are listed in data/MapInfos.json (what the editor shows) and stored as
data/MapNNN.json.  A map is *reachable* when the player can get there:

  roots   : the starting map and the vehicles' start maps (System.json),
            and any Transfer Player / Set Vehicle Location / reserveTransfer(...)
            found in common events and troops (they can run from anywhere)
  edges   : the same commands inside a map's events: map A -> map B
  possible: a plugin parameter that looks like a map id, or a transfer whose
            destination comes from a variable (cannot be followed)

Results per map: reachable / unused / possible / orphan file (not in MapInfos) /
missing file (in MapInfos, no file) / broken target (a transfer to a map that does not exist).

Also reported: assets used ONLY by one map, and assets that only unused maps use
(what you would save by deleting those maps in the editor).
Read-only: deleting maps is left to the editor, which keeps MapInfos consistent.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import plugin_refs
from dbscan import DbRef
from filecache import FileCache, signature
from parallel import run_parallel
from progress import report

REACHABLE, UNUSED, POSSIBLE = "reachable", "unused", "possible"
ORPHAN, MISSING, BROKEN, KEPT = "orphan_file", "missing_file", "broken", "kept"
STRAY = "stray_file"
NEEDS_ATTENTION = (UNUSED, POSSIBLE, ORPHAN, MISSING, BROKEN, STRAY)
STATUS_TEXT = {
    REACHABLE: "Reachable", UNUSED: "Unused", POSSIBLE: "Possibly used",
    ORPHAN: "Not in MapInfos (orphan file)", MISSING: "In MapInfos, file missing",
    BROKEN: "Broken transfer target", KEPT: "Kept (flagged)",
    STRAY: "Stray file (not a game map)",
}

_MAP_FILE = re.compile(r"^Map(\d+)\.json$", re.I)             # exactly MapNNN.json
_STRAY_HINT = re.compile(r"map[\s_-]*\d+", re.I)              # "Map004 - Copy.json", "Map004.json.bak" ...
_RE_RESERVE = re.compile(r"reserveTransfer\s*\(\s*([^,)]*)")
_NUM = re.compile(r"^\d+$")
MAX_REFS = 200


@dataclass
class MapRow:
    id: int
    name: str
    status: str
    note: str = ""
    label: str = ""                 # "World 1 / Town" (parent chain)
    file_size: int = 0
    filename: str = ""              # set only for stray files (copies / backups of maps)
    events: int = 0
    inbound: list = field(default_factory=list)       # [DbRef] who sends the player here
    outbound: list = field(default_factory=list)      # [map ids] where this map leads
    asset_count: int = 0
    exclusive: list = field(default_factory=list)     # [(category, name, bytes)] used by this map only
    exclusive_bytes: int = 0

    @property
    def key(self) -> str:
        return f"mapfile:{self.filename}" if self.filename else f"map:{self.id}"

    @property
    def file(self) -> str:
        return self.filename or f"Map{self.id:03d}.json"


@dataclass
class MapScan:
    rows: list = field(default_factory=list)
    dynamic: list = field(default_factory=list)       # [(where, text)] destination from a variable
    caveats: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    freed_assets: list = field(default_factory=list)  # [(category, name, bytes)] only unused maps use
    freed_bytes: int = 0


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _walk_commands(cmds, src, loc, edges, dynamic):
    """edges: [(src_map_id | None, target, DbRef)]. dynamic: [(where, text)]."""
    buf, buf_idx = [], 0

    def flush():
        if not buf:
            return
        text = "\n".join(buf)
        for m in _RE_RESERVE.finditer(text):
            arg = m.group(1).strip()
            where = dict(loc, command=buf_idx)
            if _NUM.match(arg):
                edges.append((src, int(arg), DbRef("script", _where(where), False, where)))
            else:
                dynamic.append((_where(where), m.group(0).strip(), src))
        buf.clear()

    for idx, cmd in enumerate(cmds or []):
        if not isinstance(cmd, dict):
            continue
        code, P = cmd.get("code"), cmd.get("parameters")
        if not isinstance(code, int) or not isinstance(P, list):
            continue
        if code in (355, 655):
            if code == 355:
                flush()
                buf_idx = idx
            buf.append(str(P[0]) if P else "")
            continue
        flush()
        here = dict(loc, command=idx)
        try:
            if code == 201:
                if P[0] == 0:
                    edges.append((src, int(P[1]), DbRef("transfer", _where(here), False, here)))
                else:
                    dynamic.append((_where(here), "Transfer Player (variable destination)", src))
            elif code == 202:
                if P[1] == 0:
                    edges.append((src, int(P[2]), DbRef("transfer", _where(here), False, here)))
                else:
                    dynamic.append((_where(here), "Set Vehicle Location (variable destination)", src))
        except (IndexError, TypeError, ValueError):
            continue
    flush()


def _where(loc: dict) -> str:
    bits = [loc.get("file", "?")]
    if loc.get("event") is not None:
        bits.append(f"event {loc['event']}")
    if loc.get("page") is not None:
        bits.append(f"page {loc['page'] + 1}")
    if loc.get("command") is not None:
        bits.append(f"line {loc['command'] + 1}")
    return " > ".join(bits)


_G: dict = {}


def _map_init(game: str) -> None:
    _G["game"] = Path(game)


def _map_task(name: str) -> dict:
    """Transfers found in one MapNNN.json (cacheable)."""
    m = _MAP_FILE.search(name)
    mid = int(m.group(1))
    try:
        mp = _load(_G["game"] / "data" / name)
    except Exception as e:
        return {"error": f"{name}: {e}"}
    edges, dyn, n = [], [], 0
    for ev in (mp.get("events") if isinstance(mp, dict) else None) or []:
        if not isinstance(ev, dict):
            continue
        n += 1
        for pi, page in enumerate(ev.get("pages") or []):
            if isinstance(page, dict):
                loc = {"file": name, "event": ev.get("id"), "page": pi}
                _walk_commands(page.get("list"), mid, loc, edges, dyn)
    return {"events": n,
            "edges": [[src, tgt, r.kind, r.where, r.loc] for src, tgt, r in edges],
            "dyn": [[w, t, s] for (w, t, s) in dyn]}


def scan_maps(game_folder: Path, assets: list | None = None, ann=None, log=None) -> MapScan:
    game = Path(game_folder)
    data = game / "data"
    res = MapScan()
    if not data.is_dir():
        res.errors.append("data/ folder not found")
        return res

    infos: dict = {}
    ip = data / "MapInfos.json"
    if ip.is_file():
        try:
            raw = _load(ip)
            for it in raw if isinstance(raw, list) else []:
                if isinstance(it, dict) and isinstance(it.get("id"), int):
                    infos[it["id"]] = it
        except Exception as e:
            res.errors.append(f"MapInfos.json: {e}")
    else:
        res.errors.append("MapInfos.json not found")

    files: dict = {}
    for p in sorted(data.glob("Map*.json")):
        m = _MAP_FILE.search(p.name)
        if m:
            files[int(m.group(1))] = p

    stray_paths, _plugin_data = _find_stray(game, data)
    stray_low = {p.name.lower() for p in stray_paths}

    edges: list = []
    dyn: list = []
    events_count: dict = {}
    cache = FileCache(game)
    sigs, frags, todo = {}, {}, []
    for mid, p in files.items():
        sigs[mid] = signature(p)
        hit = cache.get("map", p.name, sigs[mid])
        if hit is not None:
            frags[mid] = hit
        else:
            todo.append(mid)
    done0, total = len(frags), len(files)
    report(log, done0, total, "maps")

    def tick(k):
        report(log, done0 + k, total, f"maps ({done0 + k}/{total})")

    fresh = run_parallel(_map_task, [files[m].name for m in todo], _map_init, (str(game),), tick,
                         weights=[sigs[m][0] for m in todo])
    for mid, fr in zip(todo, fresh):
        frags[mid] = fr
        if "error" not in fr:
            cache.put("map", files[mid].name, sigs[mid], fr)
    cache.flush()
    for mid in files:
        fr = frags[mid]
        if "error" in fr:
            res.errors.append(fr["error"])
            continue
        events_count[mid] = fr["events"]
        for src, tgt, kind, where, loc in fr["edges"]:
            edges.append((src, tgt, DbRef(kind, where, False, loc)))
        for where, text, src in fr["dyn"]:
            dyn.append((where, text, src))

    for fname, key in (("CommonEvents.json", None), ("Troops.json", None)):
        fp = data / fname
        if not fp.is_file():
            continue
        try:
            arr = _load(fp)
        except Exception as e:
            res.errors.append(f"{fname}: {e}")
            continue
        for it in arr if isinstance(arr, list) else []:
            if not isinstance(it, dict):
                continue
            if fname == "CommonEvents.json":
                _walk_commands(it.get("list"), None, {"file": fname, "event": it.get("id")}, edges, dyn)
            else:
                for pi, page in enumerate(it.get("pages") or []):
                    if isinstance(page, dict):
                        _walk_commands(page.get("list"), None,
                                       {"file": fname, "event": it.get("id"), "page": pi}, edges, dyn)

    # roots
    roots: dict = {}                      # map id -> [DbRef]
    sp = data / "System.json"
    if sp.is_file():
        try:
            sysd = _load(sp)
            for label, obj in (("starting map", sysd), ("boat start map", sysd.get("boat")),
                               ("ship start map", sysd.get("ship")),
                               ("airship start map", sysd.get("airship"))):
                if isinstance(obj, dict) and isinstance(obj.get("startMapId"), int) and obj["startMapId"] > 0:
                    roots.setdefault(obj["startMapId"], []).append(
                        DbRef("start", f"System.json: {label}", False, {"file": "System.json"}))
        except Exception as e:
            res.errors.append(f"System.json: {e}")

    plugin_refs_: dict = {}               # map id -> [DbRef]   (possible only)
    try:
        pf = plugin_refs.read_plugins_js(game)
    except (ValueError, OSError) as e:
        pf = None
        res.errors.append(f"js/plugins.js: {e}")
    if pf is not None:
        schema = plugin_refs.load_schema(game)
        for leaf in plugin_refs.plugins_js_leaves(pf.data, schema):
            if leaf.enabled and not leaf.category and re.search(r"map", leaf.path.rsplit(".", 1)[-1], re.I) \
                    and re.fullmatch(r"\s*\d{1,5}\s*", leaf.value) and int(leaf.value) > 0:
                plugin_refs_.setdefault(int(leaf.value), []).append(
                    DbRef("plugin", f"Plugin {leaf.plugin}: {leaf.path.split('.', 1)[-1]}", False,
                          {"file": "js/plugins.js"}))

    # graph
    out: dict = {}
    inbound: dict = {}
    for src, tgt, ref in edges:
        if tgt <= 0:
            continue
        inbound.setdefault(tgt, []).append(ref)
        if src is not None:
            out.setdefault(src, set()).add(tgt)
    for tgt, refs in roots.items():
        inbound.setdefault(tgt, [])[0:0] = refs

    def bfs(starts):
        seen, stack = set(), list(starts)
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            stack.extend(out.get(m, ()))
        return seen

    global_targets = {t for s, t, _r in edges if s is None and t > 0}
    reach = bfs(set(roots) | global_targets)
    dyn_live = any(src is None or src in reach for (_w, _t, src) in dyn)
    res.dynamic = [(w, t) for (w, t, _s) in dyn]
    plugin_reach = bfs(set(plugin_refs_)) - reach

    # assets per map
    asset_files: dict = {}
    if assets:
        for a in assets:
            if not a.get("exists"):
                continue
            refs = a.get("references", [])
            users = refs.files() if hasattr(refs, "files") else {loc.get("file") for loc in refs}
            size = 0
            for rel in a.get("files", []):
                try:
                    size += (game / rel).stat().st_size
                except OSError:
                    pass
            asset_files[(a["category"], a["name"])] = (users, size)

    all_ids = set(infos) | set(files) | {t for t in inbound if t > 0}
    for mid in sorted(all_ids):
        info = infos.get(mid)
        name = str(info.get("name", "")) if info else ""
        row = MapRow(mid, name or "(unnamed)", REACHABLE, label=_label(infos, mid),
                     events=events_count.get(mid, 0), inbound=inbound.get(mid, [])[:MAX_REFS],
                     outbound=sorted(out.get(mid, ())))
        if mid in files:
            row.file_size = files[mid].stat().st_size
        if mid not in files and info is None:
            row.status, row.name = BROKEN, "(does not exist)"
            row.note = "a transfer / start setting points to a map that does not exist"
        elif mid not in files:
            row.status, row.note = MISSING, f"{row.file} is missing: the editor reports an error for this entry"
        else:
            if mid in reach:
                row.status = REACHABLE
                row.note = "starting map" if mid in roots else ""
            elif mid in plugin_reach or mid in plugin_refs_:
                row.status, row.note = POSSIBLE, "only a plugin parameter that looks like a map id points here"
            elif dyn_live:
                row.status, row.note = POSSIBLE, "a transfer picks its destination from a variable"
            else:
                row.status = UNUSED
                srcs = sorted({int(r.loc.get("file", "Map0")[3:6]) for r in row.inbound
                               if r.loc.get("file", "").lower().startswith("map")
                               and r.loc.get("file", "")[3:6].isdigit()})
                row.note = ("nothing transfers here" if not row.inbound else
                            "only reached from unused maps" + (f" ({', '.join(map(str, srcs[:5]))})" if srcs else ""))
            if info is None:
                row.note = (row.note + "; " if row.note else "") + \
                    "not listed in MapInfos.json (invisible in the editor, but it still ships)"
                if row.status in (UNUSED, POSSIBLE):
                    row.status = ORPHAN
                elif row.status == REACHABLE:
                    pass
        if ann is not None and ann.is_flagged(row.key) and row.status in NEEDS_ATTENTION:
            row.status, row.note = KEPT, "flagged to keep"
        res.rows.append(row)

    # copies / backups of maps lying in data/: the game never loads them
    for p in stray_paths:                  # (plugin_data files are deliberately not listed)
        m = _STRAY_HINT.search(p.name)
        num = int(re.search(r"\d+", m.group(0)).group(0)) if m else 0
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if m:
            note = ("nothing in the game loads this file; it looks like a copy or backup"
                    + (f" of Map{num:03d}.json" if num in files else ""))
        else:
            note = ("not one of the engine's data files, not a map, and no plugin mentions its "
                    "name; probably a leftover")
        row = MapRow(num, "(not a game map)", STRAY, note=note + ". It still ships with the game.",
                     label=p.name, file_size=size, filename=p.name)
        if ann is not None and ann.is_flagged(row.key):
            row.status, row.note = KEPT, "flagged to keep"
        res.rows.append(row)

    # assets only some maps use
    by_file = {r.file.lower(): r for r in res.rows}
    unused_files = {r.file.lower() for r in res.rows if r.status in (UNUSED, ORPHAN, STRAY)}
    for (cat, name), (users, size) in asset_files.items():
        low = {u.lower() for u in users if u}
        maps_only = low and all(_MAP_FILE.search(u) or u in stray_low for u in low)
        if not maps_only:
            continue
        row = by_file.get(next(iter(low))) if len(low) == 1 else None
        if row is not None:
            row.exclusive.append((cat, name, size))
            row.exclusive_bytes += size
        if low <= unused_files:
            res.freed_assets.append((cat, name, size))
            res.freed_bytes += size
    for r in res.rows:
        r.exclusive.sort(key=lambda x: -x[2])
    n_assets: dict = {}
    for (cat, name), (users, _s) in asset_files.items():
        for u in {u.lower() for u in users if u}:
            n_assets[u] = n_assets.get(u, 0) + 1
    for r in res.rows:
        r.asset_count = n_assets.get(r.file.lower(), 0)
    res.freed_assets.sort(key=lambda x: -x[2])

    if plugin_refs_:
        res.caveats.append("Plugin parameters that hold a map number were treated as possible uses.")
    res.caveats.append("Transfers made by plugin commands or computed in plugin code cannot be seen; "
                       "check 'Unused' maps once before deleting them in the editor.")
    return res


STANDARD_DATA = {n.lower() for n in (
    "Actors", "Animations", "Armors", "Classes", "CommonEvents", "Enemies", "Items", "MapInfos",
    "Skills", "States", "System", "Tilesets", "Troops", "Weapons")}


def _find_stray(game: Path, data: Path):
    """(stray, plugin_data): files in data/ that are neither engine data nor MapNNN.json.
    A file whose name appears in a plugin (or plugins.js) is probably plugin data."""
    cands = []
    for p in sorted(data.iterdir()):
        if not p.is_file() or _MAP_FILE.search(p.name):
            continue
        if p.suffix.lower() == ".json" and p.stem.lower() in STANDARD_DATA:
            continue
        if p.suffix.lower() == ".json" or _STRAY_HINT.search(p.name):
            cands.append(p)
    if not cands:
        return [], []
    texts = []
    pl = game / "js" / "plugins"
    for f in ([game / "js" / "plugins.js"] + (sorted(pl.glob("*.js")) if pl.is_dir() else [])):
        try:
            texts.append(f.read_text(encoding="utf-8", errors="ignore").lower())
        except OSError:
            pass
    blob = "\n".join(texts)
    stray, plug = [], []
    for p in cands:
        mentioned = p.name.lower() in blob or (p.suffix.lower() == ".json" and p.stem.lower() in blob
                                               and len(p.stem) >= 4)
        (plug if mentioned and not _STRAY_HINT.search(p.name) else stray).append(p)
    return stray, plug


def _label(infos: dict, mid: int) -> str:
    parts, seen, cur = [], set(), mid
    while cur in infos and cur not in seen:
        seen.add(cur)
        parts.append(str(infos[cur].get("name", "")) or f"Map{cur:03d}")
        cur = infos[cur].get("parentId") or 0
    return " / ".join(reversed(parts)) if parts else f"Map{mid:03d}"


def export_maps_csv(rows: list, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["ID", "File", "Name", "Path", "Status", "Note", "Events", "File bytes",
                     "Assets used", "Assets only here", "Bytes only here", "Leads to"])
        for r in rows:
            wr.writerow([r.id, r.file, r.name, r.label, STATUS_TEXT.get(r.status, r.status), r.note,
                         r.events, r.file_size, r.asset_count, len(r.exclusive), r.exclusive_bytes,
                         " ".join(map(str, r.outbound))])

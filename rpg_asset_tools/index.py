"""
index.py
--------
Builds the list the UI shows. One row per (category, name):

  status
    ok        referenced and the file exists
    missing   referenced but no file on disk            (red)
    unused    file on disk, nothing references it      (amber)  <- safe to move
    possible  file on disk, no hard reference, BUT its name appears in
              plugins / notes / scripts / effects      (blue)   <- do not move
    engine    default img/system file the engine loads by name
    ignored   you flagged it "keep"

Audio twins (Hit.ogg + Hit.m4a) are ONE row with several files.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from annotations import Annotations
from asset_paths import list_disk_assets
from refs import CODE_LABELS
from scanner import ScanResult, scan_project
from system_defaults import DEFAULT_SYSTEM_IMAGES

STATUS_ORDER = {"missing": 0, "unused": 1, "possible": 2, "ok": 3, "engine": 4, "ignored": 5}


def build_asset_index(game_folder: Path, log=None, annotations: Annotations | None = None):
    """Returns (assets, scan)."""
    game = Path(game_folder)
    ann = annotations or Annotations(game)
    scan = scan_project(game, log)
    disk = list_disk_assets(game)

    # group refs by (primary category, name)
    groups: dict[tuple, dict] = {}
    for r in scan.refs:
        g = groups.setdefault((r.categories[0], r.name),
                              {"cats": set(), "refs": []})
        g["cats"].update(r.categories)
        g["refs"].append(r)

    assets = []
    used_keys: set = set()

    for (cat, name), g in groups.items():
        files = []
        for c in sorted(g["cats"], key=lambda c: (c != cat, c)):
            for p in disk.get((c, name), ()):
                files.append(p)
                used_keys.add((c, name))
        rels = [p.relative_to(game).as_posix() for p in files]
        hard = [r for r in g["refs"] if not r.optional]
        kinds: set = set()
        if hard:
            status = "ok" if files else "missing"
        elif files:
            # only a DISABLED plugin points here: it may still be used later
            status, kinds = "possible", {"disabled plugin"}
        else:
            continue                      # disabled plugin pointing at nothing: not an error
        ignored = ann.is_ignored(cat, name)
        assets.append(_row(cat, name, rels, g["refs"],
                           status, ignored, kinds))

    engine_hits = 0
    for (cat, name), files in disk.items():
        if (cat, name) in used_keys:
            continue
        rels = [p.relative_to(game).as_posix() for p in files]
        if cat == "system_img" and name in DEFAULT_SYSTEM_IMAGES:
            assets.append(_row(cat, name, rels, [], "engine", False, set()))
            engine_hits += 1
            continue
        kinds = scan.mentions.find(name)
        status = "possible" if kinds else "unused"
        assets.append(_row(cat, name, rels, [], status,
                           ann.is_ignored(cat, name), kinds))

    for a in assets:
        a["mentions"] = [
            {"kind": k, "file": f, "event": e, "page": pg, "command": c}
            for (k, f, e, pg, c) in scan.mentions.find_places(a["name"])]
        if a["ignored"] and a["status"] in ("unused", "possible"):
            a["status"] = "ignored"

    assets.sort(key=lambda a: (STATUS_ORDER[a["status"]], a["category"], a["name"].lower()))

    if log:
        c = defaultdict(int)
        for a in assets:
            c[a["status"]] += 1
        log("Index: " + ", ".join(f"{c[s]} {s}" for s in STATUS_ORDER if c[s]))
    return assets, scan


class RefLocations(Sequence):
    """The reference list of an asset row, turned into dicts only when somebody looks.
    (A busy project has hundreds of thousands of references; the window shows a few.)"""

    def __init__(self, refs: list):
        self._refs = refs

    def __len__(self) -> int:
        return len(self._refs)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [r.location() for r in self._refs[i]]
        return self._refs[i].location()

    def __iter__(self):
        for r in self._refs:
            yield r.location()

    def files(self) -> set:
        """Names of the files that hold these references (no dicts are built)."""
        return {r.file for r in self._refs}


def _row(cat, name, rels, locations, status, ignored, kinds) -> dict:
    return {
        "name": name,
        "category": cat,
        "path": rels[0] if rels else None,
        "files": rels,
        "exists": bool(rels),
        "references": locations if isinstance(locations, RefLocations) else RefLocations(locations),
        "ref_count": len(locations),
        "status": status,
        "base_status": status,      # status before the "keep" flag is applied
        "ignored": ignored,
        "mention_kinds": sorted(kinds),
        "mentions": [],             # filled by build_asset_index: where free text names it
        "asset_key": f"{cat}:{name}",
    }


def format_reference(loc: dict) -> str:
    """Human-readable reference, e.g. Map003.json -> event 5 -> page 0 -> Show Picture."""
    file_name = loc.get("file") or "?"
    parts = [file_name]
    event, page, code, ctx = loc.get("event"), loc.get("page"), loc.get("code"), loc.get("context")
    if event is not None:
        if file_name == "Troops.json":
            parts.append(f"troop {event}")
        elif file_name == "CommonEvents.json":
            parts.append(f"common event {event}")
        else:
            parts.append(f"event {event}")
    if page is not None:
        parts.append(f"page {page + 1}")
    if code in CODE_LABELS:
        parts.append(CODE_LABELS[code])
    if ctx and (code not in CODE_LABELS):
        parts.append(str(ctx).replace("_", " "))
    elif ctx and str(ctx).startswith("Route"):
        parts.append(str(ctx))
    return " > ".join(parts)


def movable_orphans(assets) -> list[dict]:
    """Rows that are safe to move to _unused/."""
    return [a for a in assets if a["status"] == "unused"]


def export_report(assets, path: Path, scan: ScanResult | None = None) -> Path:
    """Write the index as .json or .csv (by extension)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["status", "category", "name", "references", "files"])
            for a in assets:
                w.writerow([a["status"], a["category"], a["name"],
                            a["ref_count"], " | ".join(a["files"])])
    else:
        counts = defaultdict(int)
        for a in assets:
            counts[a["status"]] += 1
        payload = {
            "summary": dict(counts),
            "parse_errors": scan.errors if scan else [],
            "assets": [{k: a[k] for k in
                        ("status", "category", "name", "files", "ref_count", "mention_kinds")}
                       | {"references": [format_reference(l) for l in a["references"][:20]]}
                       for a in assets],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return path

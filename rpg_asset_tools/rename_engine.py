"""
rename_engine.py
----------------
Safe rename / move for assets. One code path for single renames, batch
renames, "move orphans to _unused/" and Undo.

Guarantees
  * Exact-field JSON rewrite through refs.walk_refs (the same walker the
    scanner uses) - never a blind text replace.
  * Linked categories (enemy + sv_enemy) move and rewrite together.
  * Everything is checked BEFORE anything is touched (plan_rename).
  * Audio twins (.ogg + .m4a) move together; chains/swaps (A->B, B->A) work.
  * ONE backup per operation (only the data files that change).
  * Files move first, JSON second; any failure rolls both back.
  * Every operation is logged and can be undone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from annotations import Annotations, tool_dir
from asset_paths import (
    ASSET_ROOTS, UNUSED_DIR_NAME, affected_categories, dest_for,
    files_for, normalize_name,
)
from history import append_history, last_undoable
from jsonio import detect_writer, file_hash, read_text, write_json_atomic
from refs import Ref, walk_refs
import plugin_refs
from scanner import MANUAL_KINDS, ScanResult, plugin_leaf_ref, scan_project
from system_defaults import DEFAULT_SYSTEM_IMAGES

KEEP_BACKUPS = 20
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


@dataclass
class RenameItem:
    category: str
    old: str
    new: str


@dataclass
class Plan:
    items: list = field(default_factory=list)            # list[RenameItem] (normalised)
    item_errors: dict = field(default_factory=dict)      # index -> [messages]
    errors: list = field(default_factory=list)           # global
    warnings: list = field(default_factory=list)
    moves: list = field(default_factory=list)            # [(src Path, dst Path)]
    json_changes: dict = field(default_factory=dict)     # file -> field count
    ref_counts: dict = field(default_factory=dict)       # item index -> refs rewritten

    @property
    def ok(self) -> bool:
        return not self.errors and not any(self.item_errors.values())

    def problems(self) -> list[str]:
        out = list(self.errors)
        for i, msgs in sorted(self.item_errors.items()):
            for m in msgs:
                it = self.items[i]
                out.append(f"{it.old} -> {it.new}: {m}")
        return out


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _rel(game: Path, p: Path) -> str:
    return Path(p).relative_to(game).as_posix()


def _validate_name(name: str) -> str | None:
    if not name:
        return "name is empty"
    if _BAD_CHARS.search(name):
        return 'name contains an illegal character (<>:"|?* or control chars)'
    for part in name.split("/"):
        if part in ("", ".", ".."):
            return "name has an empty or relative path segment"
        if part != part.strip() or part.endswith("."):
            return "folder/file names cannot start/end with spaces or end with a dot"
    return None


def _mapping(items) -> dict:
    """(category, old) -> index, expanded over linked categories."""
    m = {}
    for i, it in enumerate(items):
        for cat in affected_categories(it.category):
            m[(cat, it.old)] = i
    return m


PLUGINS_KEY = "js/plugins.js"     # keys WITH a "/" are paths relative to the project;
                                 # keys without one are legacy names inside data/


def _target_path(game: Path, key: str) -> Path:
    return game / key if "/" in key else game / "data" / key


def _display(key: str) -> str:
    return key if "/" in key else f"data/{key}"


def _backup_files(game: Path, changed: dict, op_id: str) -> Path:
    backup_dir = tool_dir(game) / "backups" / op_id / "data"
    for key, (path, *_rest) in changed.items():
        dst = backup_dir / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    return backup_dir


def _rewrite_pass(game: Path, items, mapping: dict):
    """
    Parse every data/*.json and js/plugins.js, rename matching refs IN MEMORY.
    Returns (changed, counts, errors)
      changed: {key: (path, data, writer, ends_with_newline, n_fields)}
      counts : {item_index: number of refs rewritten}
    """
    changed, counts, errors = {}, {}, []
    data_dir = game / "data"
    if not data_dir.is_dir():
        return changed, counts, ["data/ folder not found"]
    schema = plugin_refs.load_schema(game)
    for jf in sorted(data_dir.glob("*.json")):
        try:
            text = read_text(jf)
            data = json.loads(text)
        except Exception as e:
            errors.append(f"cannot parse data/{jf.name}: {e}")
            continue
        writer = detect_writer(text, data)
        touched = 0
        for obj in walk_refs(data, jf.name, schema):
            if not isinstance(obj, Ref):
                continue
            for cat in obj.categories:
                idx = mapping.get((cat, obj.name))
                if idx is not None:
                    obj.set(items[idx].new)
                    counts[idx] = counts.get(idx, 0) + 1
                    touched += 1
                    break
        if touched:
            changed[jf.name] = (jf, data, writer, text.endswith("\n"), touched)

    # plugin parameters declared as assets (js/plugins.js)
    try:
        pf = plugin_refs.read_plugins_js(game)
    except Exception as e:
        errors.append(f"cannot parse js/plugins.js: {e}")
        pf = None
    if pf is not None:
        touched = 0
        for leaf in plugin_refs.plugins_js_leaves(pf.data, schema):
            ref = plugin_leaf_ref(leaf)
            if ref is None:
                continue
            idx = mapping.get((ref.categories[0], ref.name))
            if idx is not None:
                ref.set(items[idx].new)
                counts[idx] = counts.get(idx, 0) + 1
                touched += 1
        if touched:
            changed[PLUGINS_KEY] = (pf.path, pf.data, pf.render, False, touched)
    return changed, counts, errors


def _move_all(pairs, log=None):
    """
    Move many files safely. If a destination is another source (swap / chain /
    case-only rename) go through temporary names. Rolls itself back on error.
    """
    srcs = {Path(s) for s, _ in pairs}
    need_two_phase = any(
        Path(d) in srcs or (Path(d).exists() and Path(s).exists() and os.path.samefile(s, d))
        for s, d in pairs
    )
    done: list[tuple[Path, Path]] = []

    def step(a: Path, b: Path):
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(a), str(b))
        done.append((a, b))

    try:
        if need_two_phase:
            temps = []
            for i, (s, d) in enumerate(pairs):
                s = Path(s)
                tmp = s.with_name(f".{s.name}.__rpgat{i}")
                step(s, tmp)
                temps.append((tmp, Path(d)))
            for tmp, d in temps:
                step(tmp, d)
        else:
            for s, d in pairs:
                if Path(d).exists():
                    raise RuntimeError(f"destination appeared: {d}")
                step(Path(s), Path(d))
    except Exception:
        for a, b in reversed(done):
            try:
                if b.exists() and not a.exists():
                    shutil.move(str(b), str(a))
            except Exception:
                pass
        raise


def _new_op_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def _prune_backups(game: Path) -> None:
    root = tool_dir(game, create=False) / "backups"
    if not root.is_dir():
        return
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    for old in dirs[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

def _count_refs(plan: Plan, scan: ScanResult, mapping: dict) -> None:
    """Count what a rename touches from the scan (no re-parsing, so previews are instant)."""
    for r in scan.refs:
        for cat in r.categories:
            idx = mapping.get((cat, r.name))
            if idx is None:
                continue
            new = plan.items[idx].new
            msg = r.check_name(new)
            if msg and msg not in plan.item_errors[idx]:
                plan.item_errors[idx].append(msg)
            plan.ref_counts[idx] = plan.ref_counts.get(idx, 0) + 1
            plan.json_changes[r.file] = plan.json_changes.get(r.file, 0) + 1
            break


def plan_rename(game_folder: Path, items, scan: ScanResult | None = None) -> Plan:
    game = Path(game_folder)
    plan = Plan()
    scan = scan or scan_project(game)

    # normalise
    for it in items:
        plan.items.append(RenameItem(
            it.category, normalize_name(it.old), normalize_name(it.new)))
    for i, it in enumerate(plan.items):
        errs = plan.item_errors.setdefault(i, [])
        if it.category not in ASSET_ROOTS:
            errs.append(f"unknown category '{it.category}'")
            continue
        if it.old == it.new:
            errs.append("new name is the same as the old name")
        bad = _validate_name(it.new)
        if bad:
            errs.append(bad)
        if it.category == "system_img" and it.old in DEFAULT_SYSTEM_IMAGES:
            errs.append("engine default file in img/system: the engine loads it by "
                        "fixed name, renaming would break the game")

    if scan.errors:
        plan.errors.append(
            "Some data files could not be parsed, so references there would be "
            "left behind: " + "; ".join(scan.errors[:3]))

    # duplicates inside the batch
    seen = {}
    for i, it in enumerate(plan.items):
        for cat in affected_categories(it.category):
            key = (cat, it.old)
            if key in seen and seen[key] != i:
                plan.item_errors[i].append("asset appears twice in this batch")
            seen[key] = i

    # file moves
    all_srcs = set()
    per_item_moves: dict[int, list] = {}
    for i, it in enumerate(plan.items):
        if plan.item_errors[i] or it.category not in ASSET_ROOTS:
            continue
        mv = []
        for cat in affected_categories(it.category):
            for src in files_for(game, cat, it.old):
                mv.append((src, dest_for(game, cat, src, it.new)))
        per_item_moves[i] = mv
        all_srcs.update(s for s, _ in mv)
        if not mv:
            plan.warnings.append(
                f"{it.old}: no file on disk, only the references will be updated "
                f"(use this to re-point a missing asset to an existing name).")

    dest_seen: dict[str, int] = {}
    for i, mv in per_item_moves.items():
        for src, dst in mv:
            low = str(dst).lower()
            if low in dest_seen and dest_seen[low] != i:
                plan.item_errors[i].append(
                    f"another item in this batch also targets {_rel(game, dst)}")
            dest_seen[low] = i
            if dst in all_srcs:
                continue                      # chain / swap: handled by two-phase
            if dst.exists():
                try:
                    if os.path.samefile(src, dst):
                        continue              # case-only rename
                except OSError:
                    pass
                plan.item_errors[i].append(f"destination exists: {_rel(game, dst)}")
        plan.moves.extend(mv)

    # animation textures are referenced from inside binary .efkefc files
    for i, it in enumerate(plan.items):
        if it.category == "animation" and "effect" in scan.mentions.find(it.old):
            plan.item_errors[i].append(
                "this texture is used inside an Effekseer effect (.efkefc); "
                "renaming it would break that effect")

    # JSON references: counted from the scan (no re-parsing, so the live preview
    # in the dialog stays instant). apply_plan() does the real rewrite pass.
    if not plan.errors:
        mapping = {k: v for k, v in _mapping(plan.items).items()
                   if not plan.item_errors[v]}
        _count_refs(plan, scan, mapping)

    # things we cannot update automatically
    for i, it in enumerate(plan.items):
        kinds = [k for k in scan.mentions.find(it.old) if k in MANUAL_KINDS]
        if kinds:
            plan.warnings.append(
                f"{it.old}: also mentioned in {', '.join(sorted(kinds))} - "
                f"those places are NOT updated automatically, check them.")
    return plan


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

def apply_plan(game_folder: Path, plan: Plan, log=print, annotations=None):
    """Returns (ok, op_id)."""
    game = Path(game_folder)
    if not plan.ok:
        for p in plan.problems():
            log(f"BLOCKED: {p}")
        return False, None

    mapping = _mapping(plan.items)
    changed, counts, errs = _rewrite_pass(game, plan.items, mapping)
    if errs:
        for e in errs:
            log(f"BLOCKED: {e}")
        return False, None
    for src, _ in plan.moves:
        if not src.exists():
            log(f"BLOCKED: file vanished since preview: {_rel(game, src)}")
            return False, None

    op_id = _new_op_id()
    backup_dir = None
    if changed:
        backup_dir = _backup_files(game, changed, op_id)
        log(f"Backup of {len(changed)} data file(s): "
            f"{_rel(game, backup_dir.parent)}")

    written: list[str] = []
    moved = False
    try:
        if plan.moves:
            log(f"Moving {len(plan.moves)} file(s)...")
            _move_all(plan.moves)
            moved = True
            for s, d in plan.moves:
                log(f"  {_rel(game, s)} -> {_rel(game, d)}")

        hashes = {}
        for name, (path, data, writer, nl, n) in changed.items():
            write_json_atomic(path, data, writer, nl)
            written.append(name)
            hashes[name] = file_hash(path)
            log(f"  updated {n} field(s) in {_display(name)}")

        ann = annotations or Annotations(game)
        for it in plan.items:
            for cat in affected_categories(it.category):
                ann.migrate(cat, it.old, it.new)

        append_history(game, {
            "id": op_id, "op": "rename",
            "ts": datetime.now(timezone.utc).isoformat(),
            "items": [vars(i) for i in plan.items],
            "moves": [[_rel(game, s), _rel(game, d)] for s, d in plan.moves],
            "json_hashes": hashes,
            "backup": f"backups/{op_id}" if backup_dir else None,
        })
        _prune_backups(game)
        log(f"Done: {len(plan.items)} rename(s). Test the game; "
            f"Undo is available.")
        return True, op_id

    except Exception as e:
        log(f"ERROR: {e}  -> rolling back")
        if backup_dir:
            for name in written:
                try:
                    shutil.copy2(backup_dir / name, _target_path(game, name))
                except Exception as e2:
                    log(f"  could not restore {_display(name)}: {e2}")
        if moved:
            try:
                _move_all([(d, s) for s, d in plan.moves])
            except Exception as e2:
                log(f"  could not restore files: {e2}")
        log("Rename aborted; project restored.")
        return False, None


def rename_many(game_folder: Path, items, dry_run: bool, log=print,
                scan: ScanResult | None = None, annotations=None):
    """Plan, report, and (unless dry_run) apply. Returns (ok, plan)."""
    game = Path(game_folder)
    plan = plan_rename(game, items, scan)
    log(f"Rename [{'PREVIEW' if dry_run else 'APPLY'}] - {len(plan.items)} item(s)")
    for i, it in enumerate(plan.items):
        n = plan.ref_counts.get(i, 0)
        log(f"  [{it.category}] {it.old} -> {it.new}   ({n} reference(s))")
    for f, n in sorted(plan.json_changes.items()):
        log(f"  {_display(f) if f.endswith('.json') else 'js/' + f}: {n} field(s)")
    for s, d in plan.moves:
        log(f"  move {_rel(game, s)} -> {_rel(game, d)}")
    for w in plan.warnings:
        log(f"  WARNING {w}")
    if not plan.ok:
        for p in plan.problems():
            log(f"  ERROR {p}")
        return False, plan
    if dry_run:
        log("Preview only - nothing changed.")
        return True, plan
    ok, _ = apply_plan(game, plan, log, annotations)
    return ok, plan


# --------------------------------------------------------------------------
# orphans -> _unused/
# --------------------------------------------------------------------------

def _unused_pairs(game: Path, rel_paths, log=None, dry_run=False, dirname: str = UNUSED_DIR_NAME):
    """(src, dest-in-_unused/) pairs; never overwrites, adds __1, __2 on collision."""
    pairs, taken = [], set()
    for rel in rel_paths:
        src = game / rel
        if not src.is_file():
            if log:
                log(f"  skip (missing): {rel}")
            continue
        dest_dir = src.parent / dirname
        dest = dest_dir / src.name
        n = 1
        while dest.exists() or dest in taken:
            dest = dest_dir / f"{src.stem}__{n}{src.suffix}"
            n += 1
        taken.add(dest)
        pairs.append((src, dest))
        if log:
            log(f"  {'[preview] ' if dry_run else ''}{rel} -> {_rel(game, dest)}")
    return pairs


def move_to_unused(game_folder: Path, rel_paths, dry_run: bool, log=print,
                   dirname: str = UNUSED_DIR_NAME):
    """Move files into an '_unused/' folder next to them. Returns (count, op_id)."""
    game = Path(game_folder)
    pairs = _unused_pairs(game, rel_paths, log, dry_run, dirname)
    if dry_run or not pairs:
        return len(pairs), None
    _move_all(pairs)
    op_id = _new_op_id()
    append_history(game, {
        "id": op_id, "op": "move_unused",
        "ts": datetime.now(timezone.utc).isoformat(),
        "moves": [[_rel(game, s), _rel(game, d)] for s, d in pairs],
        "backup": None, "json_hashes": {},
    })
    log(f"Moved {len(pairs)} file(s) to {dirname}/. Undo is available.")
    return len(pairs), op_id


# --------------------------------------------------------------------------
# merge duplicates: re-point references to the keeper, move the copy to _unused/
# --------------------------------------------------------------------------

def plan_merge(game_folder: Path, merges, scan: ScanResult | None = None) -> Plan:
    """merges = [(category, duplicate_name, keeper_name), ...]"""
    from duplicates import MERGEABLE, same_content   # local: avoids import cycle
    game = Path(game_folder)
    plan = Plan()
    scan = scan or scan_project(game)
    plan.items = [RenameItem(c, normalize_name(d), normalize_name(k)) for c, d, k in merges]
    if scan.errors:
        plan.errors.append("Some data files could not be parsed: " + "; ".join(scan.errors[:3]))

    removed = {(it.category, it.old) for it in plan.items}
    all_rels = []
    for i, it in enumerate(plan.items):
        errs = plan.item_errors.setdefault(i, [])
        if it.category not in MERGEABLE:
            errs.append(f"category '{it.category}' cannot be merged automatically")
            continue
        if it.old == it.new:
            errs.append("duplicate and keeper are the same asset")
            continue
        if (it.category, it.new) in removed:
            errs.append("the keeper is itself being merged away in this batch")
            continue
        dup_files = files_for(game, it.category, it.old)
        keep_files = files_for(game, it.category, it.new)
        if not dup_files:
            errs.append("duplicate has no file on disk")
            continue
        if not keep_files:
            errs.append("keeper has no file on disk")
            continue
        if not same_content(dup_files, keep_files):
            errs.append("files are NOT identical any more (changed since the scan)")
            continue
        all_rels.extend(_rel(game, f) for f in dup_files)

    if plan.ok:
        plan.moves = _unused_pairs(game, all_rels)
        _count_refs(plan, scan, _mapping(plan.items))
    return plan


def merge_assets(game_folder: Path, merges, dry_run: bool, log=print,
                 scan: ScanResult | None = None):
    """Returns (ok, plan). One backup, one history record, fully undoable."""
    game = Path(game_folder)
    plan = plan_merge(game, merges, scan)
    log(f"Merge duplicates [{'PREVIEW' if dry_run else 'APPLY'}] - {len(plan.items)} item(s)")
    for i, it in enumerate(plan.items):
        log(f"  [{it.category}] {it.old}  ->  keep {it.new}   "
            f"({plan.ref_counts.get(i, 0)} reference(s) re-pointed)")
    if not plan.ok:
        for p in plan.problems():
            log(f"  ERROR {p}")
        return False, plan
    if dry_run:
        log("Preview only - nothing changed.")
        return True, plan

    mapping = _mapping(plan.items)
    changed, _counts, errs = _rewrite_pass(game, plan.items, mapping)
    if errs:
        for e in errs:
            log(f"BLOCKED: {e}")
        return False, plan

    op_id = _new_op_id()
    backup_dir = None
    if changed:
        backup_dir = _backup_files(game, changed, op_id)
    written, moved = [], False
    try:
        _move_all(plan.moves)
        moved = True
        hashes = {}
        for name, (path, data, writer, nl, n) in changed.items():
            write_json_atomic(path, data, writer, nl)
            written.append(name)
            hashes[name] = file_hash(path)
            log(f"  updated {n} field(s) in {_display(name)}")
        append_history(game, {
            "id": op_id, "op": "merge",
            "ts": datetime.now(timezone.utc).isoformat(),
            "items": [vars(i) for i in plan.items],
            "moves": [[_rel(game, s), _rel(game, d)] for s, d in plan.moves],
            "json_hashes": hashes,
            "backup": f"backups/{op_id}" if backup_dir else None,
        })
        _prune_backups(game)
        log(f"Merged {len(plan.items)} duplicate(s); copies are in _unused/. Undo is available.")
        return True, plan
    except Exception as e:
        log(f"ERROR: {e}  -> rolling back")
        if backup_dir:
            for name in written:
                shutil.copy2(backup_dir / name, _target_path(game, name))
        if moved:
            _move_all([(d, s) for s, d in plan.moves])
        return False, plan


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------

def describe_last_undoable(game_folder: Path) -> str | None:
    e = last_undoable(game_folder)
    if not e:
        return None
    return describe_last_undoable_text(e)


def undo_last(game_folder: Path, log=print, annotations=None) -> bool:
    game = Path(game_folder)
    e = last_undoable(game)
    if not e:
        log("Nothing to undo.")
        return False

    # Refuse if the data files changed since (e.g. edited in RPG Maker)
    for name, h in (e.get("json_hashes") or {}).items():
        p = _target_path(game, name)
        if not p.is_file() or file_hash(p) != h:
            log(f"CANNOT UNDO: {_display(name)} was modified after the operation "
                f"(probably edited in RPG Maker). Undoing would discard that work.")
            return False
    backup = tool_dir(game, create=False) / e["backup"] / "data" if e.get("backup") else None
    if e.get("json_hashes") and (not backup or not backup.is_dir()):
        log("CANNOT UNDO: the backup for this operation was pruned.")
        return False

    reverse = []
    for src_rel, dst_rel in e.get("moves", []):
        src, dst = game / src_rel, game / dst_rel
        if not dst.exists():
            log(f"CANNOT UNDO: {dst_rel} is no longer there.")
            return False
        reverse.append((dst, src))
    try:
        if reverse:
            _move_all(reverse)
        for name in (e.get("json_hashes") or {}):
            shutil.copy2(backup / name, _target_path(game, name))
            log(f"  restored {_display(name)}")
        if e["op"] == "rename":
            ann = annotations or Annotations(game)
            for it in e.get("items", []):
                for cat in affected_categories(it["category"]):
                    ann.migrate(cat, it["new"], it["old"])
    except Exception as ex:
        log(f"ERROR while undoing: {ex}")
        return False
    append_history(game, {"op": "undo", "target": e["id"],
                          "ts": datetime.now(timezone.utc).isoformat()})
    log(f"Undone: {describe_last_undoable_text(e)}")
    return True


def describe_last_undoable_text(e: dict) -> str:
    if e["op"] == "rename":
        return f"rename of {len(e.get('items', []))} asset(s)"
    if e["op"] == "merge":
        return f"merge of {len(e.get('items', []))} duplicate(s)"
    return f"move of {len(e.get('moves', []))} file(s) to _unused/"

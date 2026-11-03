"""
scanner.py
----------
Reads the project ONCE and returns every asset reference (certain) plus
every free-text mention (possible).

  data/*.json   -> refs.walk_refs   (incl. Plugin Commands whose args are declared)
  js/plugins.js -> plugin parameters declared as files (header @type file, or your
                   plugin_params.json) become references; everything else is a mention
  effects/*.efkefc -> mentions only (they reference img/animations textures)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import plugin_refs
from filecache import FileCache, signature
from parallel import run_parallel
from progress import report
from refs import MentionIndex, Ref, walk_refs

# Free-text kinds that a rename cannot update automatically
MANUAL_KINDS = ("note", "script", "plugin", "comment", "effect")


@dataclass
class ScanResult:
    refs: list = field(default_factory=list)          # list[Ref]
    mentions: MentionIndex = field(default_factory=MentionIndex)
    errors: list = field(default_factory=list)        # unparsable files
    files_scanned: int = 0
    plugin_declared: int = 0                          # plugin params/args recognised as assets
    plugin_user_mapped: int = 0                       # ... of which from plugin_params.json
    plugin_candidates: dict = field(default_factory=dict)   # path -> {values} (undeclared)
    plugins_js_error: str = ""


def load_json_file(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


# ---- one data file -> a plain, picklable / JSON-able payload ---------------------
_G: dict = {}          # per-process state set by _init_worker


def _init_worker(game_folder: str, schema) -> None:
    _G["game"], _G["schema"] = Path(game_folder), schema


def _ref_to_row(r: Ref) -> list:
    return [r.name, list(r.categories), r.file, r.context, r.event, r.page, r.command, r.code,
            r.prefix, r.optional, r.forbid]


def _row_to_ref(row: list) -> Ref:
    n, cats, f, ctx, ev, pg, cmd, code, prefix, opt, forbid = row
    return Ref(name=n, categories=tuple(cats), file=f, context=ctx, event=ev, page=pg,
               command=cmd, code=code, prefix=prefix, optional=opt, forbid=forbid)


def _scan_file(name: str) -> dict:
    """Parse + walk one data/*.json.  Never raises: an unreadable file returns {"error": ...}."""
    path = _G["game"] / "data" / name
    try:
        data = load_json_file(path)
    except Exception as e:                       # never swallow silently: orphan results depend on this
        return {"error": f"{name}: {e}"}
    refs, mentions = [], []
    for item in walk_refs(data, name, _G["schema"]):
        if isinstance(item, Ref):
            refs.append(_ref_to_row(item))
        else:
            mentions.append([item.text, item.kind, item.file, item.event, item.page,
                             item.command, item.path])
    return {"refs": refs, "mentions": mentions}


def scan_project(game_folder: Path, log=None, use_cache: bool = True) -> ScanResult:
    game_folder = Path(game_folder)
    result = ScanResult()
    data_dir = game_folder / "data"
    schema = plugin_refs.load_schema(game_folder)

    if data_dir.is_dir():
        names = sorted(p.name for p in data_dir.glob("*.json"))
        cache = FileCache(game_folder, use_cache)
        payloads: dict = {}
        todo: list = []
        sigs: dict = {}
        for n in names:
            try:
                sigs[n] = signature(data_dir / n)
            except OSError:
                sigs[n] = [0, 0]
            hit = cache.get("assets", n, sigs[n])
            if hit is not None:
                payloads[n] = hit
            else:
                todo.append(n)
        if log:
            log(f"Reading {len(names)} data file(s): {len(names) - len(todo)} from cache, "
                f"{len(todo)} to scan...")
        total = len(names)
        report(log, len(payloads), total, "")
        done_before = len(payloads)

        def tick(k):
            report(log, done_before + k, total, f"scanning data files ({done_before + k}/{total})")

        fresh = run_parallel(_scan_file, todo, _init_worker, (str(game_folder), schema), tick,
                             weights=[sigs[n][0] for n in todo])
        for n, pl in zip(todo, fresh):
            payloads[n] = pl
            if "error" not in pl:
                cache.put("assets", n, sigs[n], pl)
        cache.flush()

        for n in names:
            pl = payloads[n]
            if "error" in pl:
                result.errors.append(pl["error"])
                continue
            result.files_scanned += 1
            for row in pl["refs"]:
                ref = _row_to_ref(row)
                result.refs.append(ref)
                if ref.code == 357:
                    result.plugin_declared += 1
            for text, kind, file, ev, pg, cmd, path in pl["mentions"]:
                result.mentions.add(text, kind, (file, ev, pg, cmd))
                if path:
                    result.plugin_candidates.setdefault(path, set()).add(text)
    else:
        result.errors.append("data/ folder not found")

    _scan_plugins_js(game_folder, result, schema)
    _scan_effects(game_folder, result)

    if log:
        log(f"Scanned {result.files_scanned} data files, "
            f"{len(result.refs)} references.")
        for err in result.errors:
            log(f"WARNING could not read {err}")
    return result


def plugin_leaf_ref(leaf) -> Ref | None:
    """A declared plugins.js leaf as a Ref (None if it is not an asset name)."""
    name = leaf.prefix + leaf.value.strip().replace("\\", "/")
    if not leaf.category or not name:
        return None
    ctx = f"Plugin {leaf.plugin}: {leaf.path.split('.', 1)[-1]}"
    return Ref(name=name, categories=(leaf.category,), file="plugins.js",
               context=ctx + ("" if leaf.enabled else " (plugin disabled)"),
               setter=leaf.commit, prefix=leaf.prefix, optional=not leaf.enabled)


def _scan_plugins_js(game_folder: Path, result: ScanResult, schema) -> None:
    try:
        pf = plugin_refs.read_plugins_js(game_folder)
    except (ValueError, OSError) as e:
        # broken plugins.js: fall back to "the whole file is one big mention"
        result.plugins_js_error = str(e)
        result.errors.append(f"js/plugins.js: {e}")      # plugin refs invisible -> block risky actions
        for path in (game_folder / "js" / "plugins.js", game_folder / "plugins.js"):
            if path.is_file():
                result.mentions.add(path.read_text(encoding="utf-8", errors="ignore"), "plugin",
                                    ("js/plugins.js", None, None, None))
                break
        return
    if pf is None:
        return
    for leaf in plugin_refs.plugins_js_leaves(pf.data, schema):
        ref = plugin_leaf_ref(leaf)
        if ref:
            result.refs.append(ref)
            result.plugin_declared += 1
            if leaf.path in schema.user:
                result.plugin_user_mapped += 1
        elif not leaf.db_type:          # database ids are handled by dbscan, not asset mentions
            result.mentions.add(leaf.value, "plugin", ("js/plugins.js", None, None, None))
            result.plugin_candidates.setdefault(leaf.path, set()).add(leaf.value)


def _scan_effects(game_folder: Path, result: ScanResult) -> None:
    """.efkefc files embed texture paths as UTF-8 / UTF-16 text."""
    folder = game_folder / "effects"
    if not folder.is_dir():
        return
    for path in folder.rglob("*.efkefc"):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        # UTF-16LE strings: decode with both alignments so odd offsets work too
        text = raw.decode("latin-1") + "\n" + raw.decode("utf-16le", "ignore") \
            + "\n" + raw[1:].decode("utf-16le", "ignore")
        result.mentions.add(text, "effect", (path.relative_to(game_folder).as_posix(), None, None, None))

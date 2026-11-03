"""
duplicates.py
-------------
Find assets whose files are byte-identical (same category folder only) and
group them. Audio twins are compared as a set (.ogg and .m4a must both match).

Only categories where "same file" really means "same asset" can be merged.
Linked categories (enemy/sv_enemy share one JSON field), engine files,
animation textures (referenced from inside .efkefc) and effects are excluded.
"""

from __future__ import annotations

import hashlib

from filefacts import FileFacts
from parallel import threaded_map
from dataclasses import dataclass, field
from pathlib import Path

from asset_paths import ASSET_ROOTS

MERGEABLE = tuple(c for c in ASSET_ROOTS
                  if c not in ("system_img", "animation", "effect", "enemy", "sv_enemy"))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same_content(dup_files, keeper_files) -> bool:
    """Every file of the duplicate must exist, byte-identical, with the same extension, in the keeper."""
    by_ext = {Path(f).suffix.lower(): f for f in keeper_files}
    for f in dup_files:
        k = by_ext.get(Path(f).suffix.lower())
        if k is None or Path(k).stat().st_size != Path(f).stat().st_size:
            return False
        if sha256_of(Path(f)) != sha256_of(Path(k)):
            return False
    return True


@dataclass
class DupGroup:
    category: str
    members: list = field(default_factory=list)     # asset rows, keeper first
    bytes_each: int = 0

    @property
    def keeper(self) -> dict:
        return self.members[0]

    @property
    def copies(self) -> list:
        return self.members[1:]

    @property
    def freed_bytes(self) -> int:
        return self.bytes_each * len(self.copies)

    def set_keeper(self, name: str) -> None:
        for i, m in enumerate(self.members):
            if m["name"] == name:
                self.members.insert(0, self.members.pop(i))
                return

    def merges(self) -> list[tuple[str, str, str]]:
        return [(self.category, m["name"], self.keeper["name"]) for m in self.copies]


def _keeper_rank(a: dict):
    # most referenced first, referenced beats unreferenced, then shorter / alphabetical name
    return (-a["ref_count"], a["status"] != "ok", len(a["name"]), a["name"].lower())


def _hash_files(game: Path, rels: list[str], progress=None) -> dict[str, str]:
    """{rel: sha256}. Unchanged files (same size + mtime) come from the facts cache; the rest
    are read by a few threads at once (each open can cost milliseconds on Windows)."""
    facts = FileFacts(game)
    out: dict[str, str] = {}
    todo, sig = [], {}
    for rel in rels:
        try:
            st = (game / rel).stat()
        except OSError:
            out[rel] = ""
            continue
        sig[rel] = (st.st_size, st.st_mtime_ns)
        hit = facts.get(rel, sig[rel][0], sig[rel][1], "sha")
        if hit:
            out[rel] = hit
        else:
            todo.append(rel)
    total = len(rels) or 1
    base = len(out)
    if progress:
        progress(base, total)
    res = threaded_map(lambda r: sha256_of(game / r), todo, 8,
                       (lambda n: progress(base + n, total)) if progress else None)
    for rel, h in res.items():
        out[rel] = h or ""
        if h:
            facts.put(rel, sig[rel][0], sig[rel][1], "sha", h)
    facts.save()
    return out


def find_duplicates(game_folder: Path, assets: list[dict], progress=None) -> list[DupGroup]:
    game = Path(game_folder)
    rows = [a for a in assets
            if a["category"] in MERGEABLE and a["files"] and a["status"] not in ("ignored", "engine")]

    # 1) cheap pre-filter: same category + same (extension, size) signature
    buckets: dict[tuple, list] = {}
    for a in rows:
        try:
            sig = tuple(sorted((Path(f).suffix.lower(), (game / f).stat().st_size)
                               for f in a["files"]))
        except OSError:
            continue
        buckets.setdefault((a["category"], sig), []).append(a)

    # 2) hash only the candidates - remembered between runs, several files read at once
    groups: list[DupGroup] = []
    candidates = [(k, v) for k, v in buckets.items() if len(v) > 1]
    total = sum(len(v) for _, v in candidates) or 1
    shas = _hash_files(game, sorted({f for _k, v in candidates for a in v for f in a["files"]}),
                       (lambda d, t: progress(d, t)) if progress else None)
    done = 0
    for (cat, sig), members in candidates:
        by_hash: dict[tuple, list] = {}
        for a in members:
            h = tuple(sorted((Path(f).suffix.lower(), shas[f]) for f in a["files"]))
            by_hash.setdefault(h, []).append(a)
            done += 1
        for same in by_hash.values():
            if len(same) > 1:
                same.sort(key=_keeper_rank)
                size = sum(s for _, s in sig)
                groups.append(DupGroup(cat, same, size))
    groups.sort(key=lambda g: -g.freed_bytes)
    return groups

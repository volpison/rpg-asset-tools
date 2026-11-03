"""
deploy.py
---------
"What will my build weigh, and what should NOT be in it?"

  * size per category, and how much the unused assets would save
  * largest files, oversized images (> 4096 px: fails on some mobile GPUs),
    heavy PNGs and audio
  * leftovers that should not ship if you copy the project folder by hand:
    .asset_tools, data_backup_*, .git, test saves, and every _unused/ folder
    (they are inside img/ and audio/, so a plain copy or an "include all"
    deployment would ship them)
"""

from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path

from annotations import TOOL_DIR_NAME
from asset_paths import ALL_UNUSED_DIR_NAMES, ASSET_ROOTS
from filefacts import FileFacts
from parallel import threaded_map

MAX_TEXTURE_SIDE = 4096
HEAVY_IMAGE_BYTES = 3 * 1024 * 1024
HEAVY_AUDIO_BYTES = 6 * 1024 * 1024
LARGEST_N = 15


def fmt_size(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{n} B"


def png_size(path: Path) -> tuple[int, int] | None:
    """Width/height from the PNG header (no image library needed)."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
            return None
        return struct.unpack(">II", head[16:24])
    except OSError:
        return None


def dir_size(path: Path) -> tuple[int, int]:
    total = files = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


@dataclass
class DeployReport:
    categories: list = field(default_factory=list)    # dicts: category, files, bytes, unused_files, unused_bytes
    total_bytes: int = 0
    unused_bytes: int = 0
    largest: list = field(default_factory=list)       # (rel, bytes)
    oversized: list = field(default_factory=list)     # (rel, w, h, bytes)
    heavy: list = field(default_factory=list)         # (rel, bytes, why)
    leftovers: list = field(default_factory=list)     # (rel, bytes, why)
    unused_dirs: list = field(default_factory=list)   # (rel, bytes, files)
    warnings: list = field(default_factory=list)

    @property
    def unused_dirs_bytes(self) -> int:
        return sum(b for _, b, _ in self.unused_dirs)


def build_deploy_report(game_folder: Path, assets: list[dict]) -> DeployReport:
    game = Path(game_folder)
    rep = DeployReport()
    per_cat: dict[str, dict] = {}
    sizes: list[tuple[str, int]] = []

    # image sizes: remembered between runs, and read with several threads at once (each file
    # open can take milliseconds on Windows; thousands of them added up to ~45 s)
    facts = FileFacts(game)
    stats: dict[str, tuple[int, int]] = {}
    for a in assets:
        for rel in a["files"]:
            if rel not in stats:
                try:
                    st = (game / rel).stat()
                except OSError:
                    continue
                stats[rel] = (st.st_size, st.st_mtime_ns)
    dims: dict[str, tuple | None] = {}
    todo = []
    for rel, (sz, mt) in stats.items():
        if rel.lower().endswith(".png"):
            hit = facts.get(rel, sz, mt, "wh")
            if hit is not None:
                dims[rel] = tuple(hit) if hit else None
            else:
                todo.append(rel)
    for rel, wh in threaded_map(lambda r: png_size(game / r), todo, 16, None).items():
        dims[rel] = wh
        facts.put(rel, stats[rel][0], stats[rel][1], "wh", list(wh) if wh else [])
    facts.save()

    for a in assets:
        cat = a["category"]
        row = per_cat.setdefault(cat, {"category": cat, "files": 0, "bytes": 0,
                                       "unused_files": 0, "unused_bytes": 0})
        for rel in a["files"]:
            if rel not in stats:
                continue
            size = stats[rel][0]
            row["files"] += 1
            row["bytes"] += size
            rep.total_bytes += size
            sizes.append((rel, size))
            if a["status"] == "unused":
                row["unused_files"] += 1
                row["unused_bytes"] += size
                rep.unused_bytes += size
            suffix = Path(rel).suffix.lower()
            if suffix == ".png":
                wh = dims.get(rel)
                if wh and max(wh) > MAX_TEXTURE_SIDE:
                    rep.oversized.append((rel, wh[0], wh[1], size))
                if size > HEAVY_IMAGE_BYTES:
                    rep.heavy.append((rel, size, "large PNG"))
            elif cat in ("bgm", "bgs", "me", "se") and size > HEAVY_AUDIO_BYTES:
                rep.heavy.append((rel, size, "large audio file"))

    rep.categories = sorted(per_cat.values(), key=lambda r: -r["bytes"])
    rep.largest = sorted(sizes, key=lambda x: -x[1])[:LARGEST_N]
    rep.oversized.sort(key=lambda x: -max(x[1], x[2]))
    rep.heavy.sort(key=lambda x: -x[1])

    # _unused/ folders created by "Move unused" - they ship with a plain copy
    for cat, (folder_rel, _exts) in ASSET_ROOTS.items():
        folder = game / folder_rel
        if folder.is_dir():
            for dirname in ALL_UNUSED_DIR_NAMES:
                for d in folder.rglob(dirname):
                    if d.is_dir():
                        b, n = dir_size(d)
                        if n:
                            rep.unused_dirs.append((d.relative_to(game).as_posix(), b, n))

    for rel in ("js/plugins/_unused", "data/_unused"):  # not-loaded plugins / stray data files
        pd = game / rel
        if pd.is_dir():
            b, n = dir_size(pd)
            if n:
                rep.unused_dirs.append((rel, b, n))

    # other leftovers at the project root
    for p in sorted(game.iterdir()):
        why = None
        if p.name == TOOL_DIR_NAME:
            why = "this tool's data (comments, history, backups)"
        elif p.name.startswith("data_backup_"):
            why = "backup made by the first version of this tool"
        elif p.name == ".git":
            why = "Git history"
        elif p.name == "save" and p.is_dir():
            why = "playtest save files"
        elif p.name.endswith(".tmp_rpgat"):
            why = "temporary file left by this tool"
        if why:
            b = dir_size(p)[0] if p.is_dir() else p.stat().st_size
            rep.leftovers.append((p.name, b, why))

    if rep.unused_dirs:
        rep.warnings.append(
            f"{len(rep.unused_dirs)} _unused/ folder(s) inside img/ and audio/ "
            f"({fmt_size(rep.unused_dirs_bytes)}) would ship with a plain copy of the project. "
            f"Move them out of the project before you build.")
    if rep.unused_bytes:
        rep.warnings.append(
            f"{fmt_size(rep.unused_bytes)} of assets are Unused (nothing references them).")
    if rep.oversized:
        rep.warnings.append(
            f"{len(rep.oversized)} image(s) are larger than {MAX_TEXTURE_SIDE}px on a side; "
            f"some mobile / WebGL devices cannot load them.")
    return rep


def move_unused_dirs_out(game_folder: Path, dest: Path, log=print) -> int:
    """
    Move every _unused/ folder's files OUT of the project into `dest`, keeping the
    relative layout (dest/img/pictures/_unused/Foo.png) so copying the folder
    back restores them. Refuses a destination inside the project.
    """
    game, dest = Path(game_folder).resolve(), Path(dest).resolve()
    if dest == game or game in dest.parents:
        raise ValueError("Choose a folder OUTSIDE the project.")
    moved = 0
    scopes = [(game / folder_rel, n) for _c, (folder_rel, _e) in ASSET_ROOTS.items()
              for n in ALL_UNUSED_DIR_NAMES]
    scopes.append((game / "js" / "plugins", "_unused"))
    scopes.append((game / "data", "_unused"))
    for folder, dirname in scopes:
        if not folder.is_dir():
            continue
        for d in sorted(folder.rglob(dirname), reverse=True):
            if not d.is_dir():
                continue
            for f in sorted(p for p in d.rglob("*") if p.is_file()):
                target = dest / f.relative_to(game)
                n = 1
                while target.exists():
                    target = target.with_name(f"{f.stem}__{n}{f.suffix}")
                    n += 1
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(target))
                moved += 1
                log(f"  {f.relative_to(game).as_posix()} -> {target}")
            try:
                d.rmdir()               # only succeeds when empty
            except OSError:
                pass
    log(f"Moved {moved} file(s) out of the project into {dest}")
    return moved

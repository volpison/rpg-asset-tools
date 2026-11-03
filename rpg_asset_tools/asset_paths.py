"""
asset_paths.py
--------------
The ONE table that describes every asset category: where it lives on disk,
which extensions count, and which categories share a JSON field.

Everything else (scanner, index, rename engine) reads from here, so adding
or fixing a category is a one-line change.
"""

from __future__ import annotations

import os

from pathlib import Path

AUDIO_EXTS = (".ogg", ".m4a", ".wav", ".mp3")
PICTURE_EXTS = (".png",)
MOVIE_EXTS = (".webm", ".mp4", ".m4v")
EFFECT_EXTS = (".efkefc",)

# category -> (folder relative to the project, allowed extensions)
ASSET_ROOTS: dict[str, tuple[Path, tuple[str, ...]]] = {
    "picture":     (Path("img/pictures"), PICTURE_EXTS),
    "bgm":         (Path("audio/bgm"), AUDIO_EXTS),
    "bgs":         (Path("audio/bgs"), AUDIO_EXTS),
    "me":          (Path("audio/me"), AUDIO_EXTS),
    "se":          (Path("audio/se"), AUDIO_EXTS),
    "character":   (Path("img/characters"), PICTURE_EXTS),
    "face":        (Path("img/faces"), PICTURE_EXTS),
    "enemy":       (Path("img/enemies"), PICTURE_EXTS),
    "sv_actor":    (Path("img/sv_actors"), PICTURE_EXTS),
    "sv_enemy":    (Path("img/sv_enemies"), PICTURE_EXTS),
    "tileset":     (Path("img/tilesets"), PICTURE_EXTS),
    # MZ: img/animations holds *textures* used inside Effekseer effects
    "animation":   (Path("img/animations"), PICTURE_EXTS),
    # MZ: Animations.json -> effectName -> effects/<name>.efkefc
    "effect":      (Path("effects"), EFFECT_EXTS),
    "parallax":    (Path("img/parallaxes"), PICTURE_EXTS),
    "battleback1": (Path("img/battlebacks1"), PICTURE_EXTS),
    "battleback2": (Path("img/battlebacks2"), PICTURE_EXTS),
    "title1":      (Path("img/titles1"), PICTURE_EXTS),
    "title2":      (Path("img/titles2"), PICTURE_EXTS),
    "system_img":  (Path("img/system"), PICTURE_EXTS),
    # Galv's Layer Graphics: whole-map images (named in map notes / plugin commands)
    "layer":       (Path("img/layers"), PICTURE_EXTS),
    "movie":       (Path("movies"), MOVIE_EXTS),
}

AUDIO_CATEGORIES = ("bgm", "bgs", "me", "se")
UNUSED_DIR_NAME = "_unused"            # the leading underscore keeps it at the top and noticeable
LEGACY_UNUSED_DIR_NAMES = ("unused",)   # created by older versions: still recognised everywhere
ALL_UNUSED_DIR_NAMES = (UNUSED_DIR_NAME,) + LEGACY_UNUSED_DIR_NAMES

# Categories whose JSON field is shared: the same name points at a file in
# BOTH folders (front-view enemy + side-view enemy). Renaming one renames both.
LINKED_CATEGORIES: dict[str, tuple[str, ...]] = {
    "enemy": ("enemy", "sv_enemy"),
    "sv_enemy": ("enemy", "sv_enemy"),
}

# Human-friendly names for the UI
CATEGORY_LABELS = {
    "picture": "Pictures", "bgm": "BGM", "bgs": "BGS", "me": "ME", "se": "SE",
    "character": "Characters", "face": "Faces", "enemy": "Enemies",
    "sv_actor": "SV Actors", "sv_enemy": "SV Enemies", "tileset": "Tilesets",
    "animation": "Animation textures", "effect": "Effects",
    "parallax": "Parallaxes", "battleback1": "Battlebacks 1",
    "battleback2": "Battlebacks 2", "title1": "Titles 1", "title2": "Titles 2",
    "system_img": "System", "movie": "Movies", "layer": "Layers (Galv)",
}


def affected_categories(category: str) -> tuple[str, ...]:
    """Categories that must move/rewrite together with `category`."""
    return LINKED_CATEGORIES.get(category, (category,))


def normalize_name(asset_name: str) -> str:
    return asset_name.strip().replace("\\", "/").strip("/")


def list_disk_assets(game_folder: Path) -> dict[tuple[str, str], list[Path]]:
    """
    One pass over every asset folder.
    Returns {(category, name): [files...]} - files with the same name but a
    different extension (audio .ogg/.m4a twins) end up in the same list.
    Anything inside an '_unused' (or legacy 'unused') folder is skipped.
    """
    # os.walk + plain strings: building and sorting thousands of Path objects was a large part
    # of a warm rescan (pathlib is slow, above all on Windows).
    game_folder = Path(game_folder)
    result: dict[tuple[str, str], list[Path]] = {}
    for cat, (folder_rel, exts) in ASSET_ROOTS.items():
        folder = game_folder / folder_rel
        if not folder.is_dir():
            continue
        base = str(folder)
        found = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ALL_UNUSED_DIR_NAMES]   # prune
            sub = dirpath[len(base):].replace(os.sep, "/").strip("/")
            for fn in filenames:
                dot = fn.rfind(".")
                if dot <= 0 and dot != 0:
                    continue
                if fn[dot:].lower() not in exts:
                    continue
                stem = fn[:dot]
                name = f"{sub}/{stem}" if sub else stem
                found.append((name, fn, dirpath))
        found.sort()
        for name, fn, dirpath in found:
            result.setdefault((cat, name), []).append(Path(dirpath) / fn)
    return result


def files_for(game_folder: Path, category: str, name: str) -> list[Path]:
    """Exact lookup of every file (all extensions) for one asset."""
    folder_rel, exts = ASSET_ROOTS[category]
    folder = Path(game_folder) / folder_rel
    name = normalize_name(name)
    found = []
    for ext in exts:
        cand = folder / (name + ext)
        if cand.is_file():
            found.append(cand)
    return found


def dest_for(game_folder: Path, category: str, src: Path, new_name: str) -> Path:
    folder_rel, _ = ASSET_ROOTS[category]
    return Path(game_folder) / folder_rel / (normalize_name(new_name) + src.suffix)

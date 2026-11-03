# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-04

First public release.

### Asset list
- Scans an RPG Maker MZ project and lists every asset with a status: OK, Missing, Unused, Possible,
  Engine, Kept.
- Rename (single or bulk) with a live preview of every file and data field that will change, conflict
  blocking, find/replace with regex, prefix/suffix, move to folder, paste from a spreadsheet, CSV import.
- *Move unused* sends unused assets to an `_unused/` folder next to the original.
- Undo for rename, merge and move; one backup per operation in `.asset_tools/backups/`.
- Notes, highlight colours (with colour swatches in the menu) and *Keep* flags; JSON / CSV export.
- *Where is this used?* shows every reference as a path you can follow in the editor.

### Health check (seven tabs, each run on demand)
- **Duplicates**: byte-identical files, merge with every reference re-pointed.
- **Naming**: case mismatches, spaces, non-ASCII names, your own naming convention.
- **Plugin params**: plugin parameters that hold asset names, mapped by you when the header does not say.
- **Plugin files**: not loaded / disabled / missing plugin files, dependencies, command counts.
- **Database**: unused items, skills, states, enemies, switches, variables and more, with broken references.
- **Maps**: reachable and unused maps, orphan and missing map files, and stray `.json` files in `data/`
  (copies, backups) that can be moved to `data/_unused/`.
- **Deploy size**: size per category, oversized files, and things that should not ship.

### Plugins and Galv's Layer Graphics
- Plugin parameters and plugin commands declared `@type file` (including `struct` and arrays) count as
  references and are updated by renames.
- `img/layers/` support for Galv's Layer Graphics: plugin commands, map-note `LAYER` lines and script calls.

### Performance and safety
- Handles projects with thousands of assets (tested with about 5,600 assets and 1.5 GB of data).
- Heavy scans run in separate processes; results are cached per file in `.asset_tools/cache/`.
- A watchdog writes `~/.rpg_asset_tools/hang.log` if the window stops responding.
- Asks for confirmation that you work on a copy before the first change to a project.
- A self-test that drives the real window (`smoke_test_qt.py`) is included.

### Known limitations
- Asset names built at run time in scripts or plugins cannot be seen; those assets show as *Possible* or *Unused*.
- Plugin commands written in the old MV text style (event command 356) are not parsed.
- Transfers made by plugin commands or computed in plugin code are invisible to the Maps check.
- The asset list does not yet mark assets that are only used by unused maps (the Maps tab reports the total).
- Tested on Windows with Python 3.12 and PySide6 6.12. Other systems and Python 3.9 are expected to work but
  have not been tried with the real window.

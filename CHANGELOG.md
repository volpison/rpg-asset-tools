# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- The "Play audio..." window is gone (its controls never worked for Ogg on Windows). The details panel has an **Open in external player...** button for every sound (BGM, BGS, ME, SE), next to **Loop editor...**, also in the right-click menu and on a double-click. It opens the file directly, with no confirmation question.
- Loop editor layout: the controls are in boxes (*Play*, *View*, *Loop points*, *Tools*), the tools (Suggest loops, beat grid, bars) are apart from the player buttons, and *Revert* and *Save tags...* are at the lower right.

### Fixed
- Loop editor, very long songs: the waveform is now built in chunks (a long song no longer needs about 0.85 times its own size again as temporary memory), and *Suggest loops* compares a song longer than about 6 minutes in a coarser way, so its similarity matrix stays at 64 MB at most instead of growing with the square of the length (about 670 MB for 20 minutes). Measured on a 20-minute stereo song at 44.1 kHz, the whole search needs about 280 MB on top of the song itself (mostly its mono mix-down). The window says so when this happens; the suggested points are still refined to the exact sample.

### Added
- Loop editor, BPM finder: **BPM finder...** (in the *Tools* box) can **detect** the tempo from the audio (the best few answers with a first-beat guess for each, the half and double tempo too, optionally only the loop part) and has a **Tap** button for tapping the tempo in time with the music. Detection resolves about 0.01 BPM (checked on generated click tracks, not yet on real songs); the first beat is a guess of where beat 1 of a bar falls and can be moved with *First beat = cursor*. Nothing is written to the audio file.
- Loop editor, pan and pitch: a **Pitch** slider (50-150) and a **Pan** slider (-100 to 100) in the Play box, with **Reset pitch/pan**, work like the game's PlayBGM. Pitch changes speed and pitch together (as in MZ); pan needs a stereo file. They are for listening only: nothing is saved, the loop points are not changed, and the loop jump, beat grid and metronome keep following the music. Volume moved to the second row of the Play box.
- Loop editor, seam view: a "Seam view" option (and a "Zoom to seam" button) that, after the loop end, shows
  the beginning of the loop instead of the file's real outro, so the join can be judged by eye. The grid, the
  ruler and clicks follow it. Switching it on or off does not move the view, and while it is on the view stays on the jump during playback (following the cursor made zoomed-in playback stutter at every loop jump).
- Loop editor, step 4 (suggest loops): "Suggest loops..." compares the song with itself and lists the best loop
  points (start, end, length, match %). Use one, or use it and hear the seam; nothing is saved until "Save tags...".
  It can keep the current start or end and look for the other point only.
- Loop editor, step 3 (beat grid and metronome): type the BPM and the first beat (or place it at the cursor) to
  draw beat and bar lines over the wave, snap markers to beats or bars, set the loop length in bars, work out
  the BPM from a loop of known length, and hear a metronome click that stays in time with the music and the
  loop jump. The grid is remembered per song in `.asset_tools/loop_grids.json`, not in the audio file.
- Loop editor, step 2 (edit and save): drag the loop markers or type LOOPSTART / LOOPLENGTH (samples, seconds
  or minutes:seconds), optionally snapping to zero crossings, hear the result, then *Save tags...*. The file is
  backed up first, only its comment header is rewritten (the sound is untouched), the result is checked with the
  game's own tag reader, and Undo restores the old file.
- Loop editor, step 1 (view and listen): for BGM and BGS in Ogg Vorbis. Shows the waveform and the loop
  stored in the `LOOPSTART` / `LOOPLENGTH` tags, with zoom down to single samples, and plays the music looping
  like the game, with a sample-exact jump and a "Hear the seam" button. It never changes the file.
  Open it from the details panel or the right-click menu. The loop shown is the one RPG Maker MZ itself reads
  (upper-case names only, LOOPSTART alone ignored, ...); remarks say when the file and the game disagree.
- `requirements-audio.txt` (numpy, soundfile): needed only by the loop editor. `check_env.py` reports them.

### Fixed
- Loop editor: moving the cursor or toggling "Loop like the game" while playing changes the sound within about 0.15 s, with no silence and no overlap (the sound output keeps running and only a short buffer is replaced). The hint line no longer says the window does not change the file.
- Loop editor: moving the cursor or changing "Loop like the game" while playing no longer makes the sound briefly louder (the old audio buffer was playing under the new sound).

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

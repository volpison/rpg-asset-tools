# Roadmap

What is planned after the loop editor (steps 1 to 4 and the seam view are done). It is a plan, not a promise:
the order can change, and each item becomes its own pull request. The *why* of finished work is in
[DEVELOPMENT.md](DEVELOPMENT.md) under "Decisions".

## 1. Finish the loop editor

1. **Main window:** replace the "Play audio" window (its volume, pitch and pan are always off; only
   "Open in external player..." works) with a direct **Open in external player...** button next to
   **Loop editor...**. ME and SE have no loop editor and still need a quick listen, so the external player
   button stays for them. Add **pan** and **pitch** inside the loop editor.
2. **Loop editor layout:** *Revert* and *Save tags...* at the lower right; the utilities (*Suggest loops...*, the BPM
   finder) together in their own group box, apart from the player buttons.
3. **Memory for very long songs.** The song is decoded whole to 32-bit float: about 64 MB for 3 minutes, 423 MB for
   20 minutes of stereo at 44.1 kHz. The finished waveform data is small (about 2 MB), but building it needs a
   temporary extra of about 0.85 times the song, and *Suggest loops* grows with the square of the length (about
   44 MB at 5 minutes, about 670 MB estimated at 20 minutes). Planned: build the waveform in chunks, cap the
   similarity matrix for long songs (coarser frames), and say so plainly when a file is very long. Storing the
   song as 16-bit would halve its memory but is not needed yet. Closing the window already frees everything.
4. **BPM finder:** tap tempo, and tempo detection from the audio. Later: a grid per section for songs that change
   tempo.
5. **Cheaper repaint:** a repaint of the zoomed wave takes about 15 ms. Draw the wave once into an image and only
   redraw the cursor while playing.

## 2. Reform the project to the house guidelines

After the loop editor is complete: documentation layout (a leaflet README, user guide, technical docs, an index),
a decisions log, data-format versions and migrations, a pull-request template and CI. Done before the next
feature so that it lands in the new structure.

## 3. Switch / variable ID reassign

Change a switch or variable ID everywhere it is used (event commands, page conditions, scripts, text codes).
Decision (2026-11-27): build it **inside this app, as a separate tool**, on top of `dbscan.py`, which already
knows every place an ID can hide and is tested. A separate project would have to copy that walker or build a
second, different one. If more game-data editing tools follow, the shared core can be extracted then.

Rules for it:

* Phases: read-only scan and dry-run, then a preview window, then the write path. Dry-run is the default.
* One walker for scanning and rewriting (`dbscan.py`), with each hit carrying the exact parameter slot to change.
* Never rewrite computed references (`$gameSwitches.value(x)`); report them as warnings. Refuse a Control Switches /
  Variables range that contains the ID until command splitting is designed. Refuse a new ID that is already used.
* Backup, signature check, rollback and Undo as for renames, then **rescan to prove** the old ID has no hits and the
  new ID has exactly the old hits plus its previous ones.
* Not covered, and said so: save files made before the change, plugin code, plugin parameters not declared as a
  switch or variable.
* An earlier draft scanner (not merged) missed variable operands, variable-to-variable comparisons, Input Number,
  Change Gold, Transfer Player, scripts inside branches and Control Variables, and mistook `value(10+1)` for ID 10;
  its roadmap principles (phases, dry-run first, never touch dynamic references, backup and Undo) are kept.

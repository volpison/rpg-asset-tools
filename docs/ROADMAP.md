# Roadmap

What is planned after the loop editor (steps 1 to 4 and the seam view are done). It is a plan, not a promise:
the order can change, and each item becomes its own pull request. The *why* of finished work is in
[DEVELOPMENT.md](DEVELOPMENT.md) under "Decisions".

## 1. Finish the loop editor

1. **Main window:** replace the "Play audio" window (its volume, pitch and pan are always off; only
   "Open in external player..." works) with a direct **Open in external player...** button next to
   **Loop editor...**. ME and SE have no loop editor and still need a quick listen, so the external player
   button stays for them. Add **pan** and **pitch** inside the loop editor (done: sliders in the Play box).
2. **Loop editor layout:** *Revert* and *Save tags...* at the lower right; the utilities (*Suggest loops...*, the BPM
   finder) together in their own group box, apart from the player buttons.
3. **Memory for very long songs.** The song is decoded whole to 32-bit float: about 64 MB for 3 minutes, 423 MB for
   20 minutes of stereo at 44.1 kHz. The finished waveform data is small (about 2 MB), but building it needs a
   temporary extra of about 0.85 times the song, and *Suggest loops* grows with the square of the length (about
   44 MB at 5 minutes, about 670 MB estimated at 20 minutes). Done: the waveform is built in chunks, the
   similarity matrix is capped (coarser frames for songs over about 6 minutes) and the Suggest window says so. Storing the
   song as 16-bit would halve its memory but is not needed yet. Closing the window already frees everything.
4. **BPM finder:** tap tempo, and tempo detection from the audio. Later: a grid per section for songs that change
   tempo.
5. **Cheaper repaint:** a repaint of the zoomed wave takes about 15 ms. Draw the wave once into an image and only
   redraw the cursor while playing.

## 2. Reform the project to the house guidelines

After the loop editor is complete: documentation layout (a leaflet README, user guide, technical docs, an index),
a decisions log, data-format versions and migrations, a pull-request template and CI. Done before the next
feature so that it lands in the new structure.

## 3. Switch / variable ID reassign: moved to RPG Story Adjuster

Changing a switch or variable ID everywhere it is used is game-logic editing, not asset management, so it will
**not** be built in this app. It lives in the sibling project
[rpg-story-adjuster](https://github.com/volpison/rpg-story-adjuster), together with a readable text form of
event commands and a safe round trip back to the game's JSON.

Why it moved (decision of 2026-11-27): first it looked like one more tool on top of `dbscan.py`, which already
knows every place an ID can hide. Then the wider goals (readable events, editing them, converting back) showed
a different product with different risks and almost nothing in common with renames or audio. It needs its own
table of event commands, which also serves the printer and the parser.

What stays here: the Health check's *Database* tab keeps finding unused, write-only and read-only switches and
variables (`dbscan.py`). The two projects each keep their own knowledge of the commands for now; a shared library
is worth extracting only if a third project needs it.

Rules the new project follows (they came out of this analysis): dry-run by default; one walker for scanning and
rewriting; never rewrite computed references (`$gameSwitches.value(x)`); refuse a range that contains the ID and a
new ID that is already used; backup, signature check, rollback and Undo; then **rescan to prove** the old ID has no
hits left. Not covered: save files made before the change, plugin code, plugin parameters not declared as a switch
or variable.

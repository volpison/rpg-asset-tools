# Developer guide

## Layout

```
rpg_asset_tools/   the app (flat modules; run with: python main.py)
tests/             unit tests (no Qt needed)
docs/              manual and this guide
```

The logic lives in pure-Python modules that never import Qt. The GUI modules (`gui.py`, `health_gui.py`,
`progress_gui.py`, `usage_gui.py`, `fit_table.py`, `image_preview.py`, `audio_preview.py`) are thin and
call into them. Keep it that way: it is what makes most of the app testable without a display.

## Code map

| Module | Role |
|---|---|
| `asset_paths.py` | category table, `_unused` folder names, listing assets on disk |
| `refs.py` | the one reference walker (data, events, notes) |
| `scanner.py` | project scan |
| `index.py` | asset statuses, `RefLocations` |
| `dbscan.py` | unused database entries |
| `usage.py`, `usage_gui.py` | "where is this used" paths |
| `mapscan.py` | reachable / unused maps, stray files in `data/` |
| `pluginscan.py` | plugin files against `plugins.js` |
| `plugin_refs.py` | plugin headers, parameters, commands; plugin source text (read once, memoised) |
| `rename_engine.py` | plan / apply / undo, backups |
| `history.py` | append-only operation log (`.asset_tools/history.jsonl`) that Undo reads |
| `system_defaults.py` | default `img/system` files the engine loads by fixed name (never renamed or reported) |
| `progress.py`, `progress_gui.py` | progress reporting through the `log` callable; the progress window |
| `jsonio.py` | format-preserving JSON writes |
| `annotations.py` | notes, colours, keep flags |
| `batch_tools.py` | bulk-rename helpers |
| `duplicates.py`, `lint.py`, `deploy.py` | Duplicates, Naming and Deploy size checks |
| `health.py` | `SECTIONS`, `run_section`, `HealthReport`: runs one Health tab at a time |
| `health_gui.py`, `gui.py` | windows |
| `oggmeta.py` | reads an Ogg Vorbis header: sample rate, channels, comment tags, `loop_tags()` (pure Python, read-only) |
| `enginetags.py` | what RPG Maker MZ's own reader finds in an .ogg (port of `_readLoopComments` / `_readMetaData` / `_onDecode`), read-only |
| `oggwrite.py` | rewrites only the comment header of an .ogg (CRC, page layout), then `verify_written` (same audio/headers, engine reader finds the tags) |
| `loopwrite.py` | saves tags safely: backup, temp file, check, replace, history op `loop_tags` (Undo goes through `rename_engine.undo_last`) |
| `loopedit.py` | typed position parsing (samples / seconds / m:ss), snap to rising zero crossing |
| `beatgrid.py` | `Grid` (BPM, offset, beats per bar): beat/bar frames, nearest beat, bars <-> frames, BPM from a loop (pure) |
| `gridstore.py` | per-song grids in `.asset_tools/loop_grids.json` (versioned, keeps unknown fields, refuses newer/damaged files) |
| `metronome.py` | click sound added into the playback buffer by file position (numpy) |
| `loopmath.py` | which loop is shown/played for the tags found, time formatting, ruler steps |
| `waveform.py` | min/max pyramid for drawing any zoom level quickly (numpy) |
| `loopplayback.py` | which samples come next (also at a changed rate), the exact loop jump, cursor position (numpy) |
| `audiofx.py` | MZ-style pitch (rate from 50-150) and the stereo pan formula (numpy) |
| `audiodecode.py` | the one place that decodes audio (optional numpy + soundfile; clear message when missing) |
| `loopfind.py` | loop-point suggestions by self-comparison of the song (numpy, no Qt) |
| `loop_suggest_gui.py` | the "Suggest loops" window: background search, candidate table |
| `loop_editor_gui.py` | the loop editor window: `WaveformView`, `LoopEditorDialog`, `open_loop_editor()` |
| `fit_table.py` | `FitTable`: fitted, resizable, reorderable columns with an empty-state overlay |
| `filecache.py`, `filefacts.py`, `parallel.py` | per-file caches, cached file hashes / PNG sizes, process pool and `threaded_map` |
| `watchdog.py` | hang log |
| `check_env.py`, `check_dependencies.bat` | environment check |
| `smoke_test_qt.py`, `smoke_fixture.py`, `run_smoke_test.bat` | real-window self-test |

## Running the tests

```bash
python -m unittest discover -s tests
```

Or a single file: `python tests/test_core.py`. Set `RPGAT_NO_WATCHDOG=1` if you run anything that creates
the main window, so the hang watchdog does not arm.

`tests/test_gui_stub.py` drives the window code against a **fake Qt** (`tests/qt_stub.py`). That checks the
app's own logic (what is called, in which order), but it cannot tell whether a real Qt call is valid. That is
what the smoke test is for.

## Real-window smoke test

```bash
python rpg_asset_tools/smoke_test_qt.py                      # builds a small fake project in a temp folder
python rpg_asset_tools/smoke_test_qt.py --assets 6000        # more pictures: stress test for the table
python rpg_asset_tools/smoke_test_qt.py --project "D:\Games\MyGame"   # READ-ONLY timings on a real project
python rpg_asset_tools/smoke_test_qt.py --show               # show the windows instead of hiding them
```

It runs the real window with `QT_QPA_PLATFORM=offscreen`, answers message boxes automatically, and goes
through: open, rescan, table filter and sort, every Health tab, column fitting, moving stray files and
undo, and the process pool. It prints PASS/FAIL with timings. With `--project` it never moves or renames
anything; it only writes the usual `.asset_tools/cache`. Run it twice: the second run shows the warm
timings, because the first one pays for the operating system reading every file.

## Performance lessons (things that cost real time before)

* **Never use `QHeaderView.ResizeToContents` on the asset table.** Qt re-measures the whole column after
  every `setItem`; refilling a populated 5,000-row table froze the window for minutes. Use Interactive
  modes, `setRowCount(0)` then the new count, and one `resizeColumnToContents` after the fill.
* **Opening a file is slow on Windows** (about 5 to 10 ms with antivirus). Thousands of small reads dominate
  everything else. Remedies in the code: `threaded_map` for I/O, `FileFacts` (hashes and PNG sizes keyed by
  size and mtime), one shared cache file for small data files, plugin sources read once and memoised,
  `os.walk` instead of `pathlib` for listing assets.
* **Cold versus warm.** The first run after hours is two to four times slower than the next one, because
  of the OS file cache. Compare only warm runs.
* **Measure before optimising.** Several guesses about the slow part were wrong; the hang log and the
  smoke-test timings are what found the real causes.

## Caches

`FileCache` keeps one JSON file per analysed data file in `<project>/.asset_tools/cache/`; files under
30 KB share `small__<section>.json` (call `flush()` after a scan). A fingerprint of `plugins.js`, the plugin
headers and `plugin_params.json`, plus each file's size and mtime, decides whether an entry is still valid.
Bump `CACHE_VERSION` in `filecache.py` whenever the shape of a cached result changes. Deleting the folder is
always safe.

## Hang watchdog

`watchdog.py` uses `faulthandler.dump_traceback_later`, re-armed every second by a `QTimer` in the main
window. If the UI thread stops for about 8 seconds, every thread's stack goes to
`~/.rpg_asset_tools/hang.log`. Set `RPGAT_NO_WATCHDOG=1` to disable it (the tests do).

## Decisions

**Loop editor: pan and pitch are playback-only.** Pitch is MZ's: a playback-rate change (speed and pitch together,
50-150), done by linear-interpolation resampling in `loopplayback.render_rate` (positions are floats; the loop wrap
and the play-once end are exact; at rate 1.0 the old whole-sample path is used unchanged). Pan is the Web Audio
stereo-panner formula (`audiofx.apply_pan`), applied to the chunk before the metronome is mixed in. The cursor is
found with `advance(..., rate)`. A pitch change restarts the run (rate is per run, debounced 100 ms); pan is live.
Nothing is saved and loop tags stay in file samples. Left out: pitch without speed (time-stretching), because the game does not do it.

**Main window: no in-app preview for sounds.** The small "Play audio" window (QMediaPlayer; volume, pitch and pan
never worked for Ogg on Windows) was removed. A sound is played by **Open in external player...**, which hands the
file to the system's default player with no confirmation (`audio_preview.open_in_external_player(ask=False)`; the
module keeps only that and the file lookup). The loop editor plays music itself, through its own sound path.

**Loop editor: optional numpy + soundfile for decoding.** QtMultimedia (the old "Play audio" window used it)
often cannot decode `.ogg` on Windows, and the editor needs exact sample counts. soundfile (libsndfile) and
numpy are permissively licensed, and the app starts and works without them (`audiodecode.missing_dependencies()`,
`requirements-audio.txt`). Left out: decoding through `QAudioDecoder` (codec availability varies by machine).

**Loop editor: our own Ogg reader instead of mutagen.** mutagen is GPL; this project is MIT. `oggmeta.py` is a
small pure-Python reader. The writer for step 2 will follow the same rule and must be tested by comparing the
decoded audio before and after.

**Loop editor: playback through `QAudioSink` fed by our own PCM**, not `QMediaPlayer`. The jump from loop end
to loop start is then exact to the sample, and the later metronome can be mixed into the same buffer so it
stays in time. Left out: `QMediaPlayer` (cannot loop on a sample).

**Loop editor: engine rules come from `js/rmmz_core.js` (MZ 1.0.0).** `enginetags.py` is a faithful port of
the engine's byte scan, quirks included, because the editor must show what the game will play. Rules found:
upper-case names only; each Ogg page scanned alone and a comment block that spans pages is skipped (its size
becomes NaN); tag text runs to the next zero byte, so a following comment of 48 to 57 bytes puts a digit
into the number; LOOPSTART without LOOPLENGTH is ignored (whole file loops); LOOPLENGTH without LOOPSTART
starts at 0. **Constraints for the step-2 writer:** write both tags in upper case, keep them in the first
Ogg page of the comment block, avoid the digit quirk, and verify by running `enginetags` on the written file.
Only MZ 1.0.0 was checked; newer builds of `rmmz_core.js` should be compared.

**Loop editor: playback is push mode.** A `QIODevice` subclass pulled by the sink failed on Windows (playback
stopped at once), so `_Feeder` writes into the device from a 10 ms timer. `playback_report()` prints state,
error and byte counts for bug reports.

**Loop editor: writing tags.** `oggwrite.py` repaginates only the header pages: the comment packet gets a
page of its own, the setup header follows, the audio pages are copied byte for byte (renumbered with new CRCs
only when the header page count changes). Names are upper case, LOOPSTART and LOOPLENGTH are the last comments
(so the engine's zero-byte-terminated text never swallows a digit), and a comment packet over one page
(65,024 bytes) is refused because the engine cannot read tags from it. The new file is checked before it is used
(`verify_written`) and the original is backed up first; Undo reuses the generic history/backups mechanism with
the file path as the key. Snap is applied only to mouse drags and the cursor buttons, never to typed values.

**Loop editor: beat grid and metronome.** The grid is a fixed tempo (floats, never rounded to whole frames
per beat, so it does not drift over a long song) plus an offset. It is stored beside the project's other
tool data (`.asset_tools/loop_grids.json`, schema_version 1), not in the .ogg: RPG Maker ignores unknown
tags but other tools might not, and the grid is an editing aid, not part of the song. The metronome is mixed
into the same PCM chunk as the music using `loopplayback.positions()` (the file position of each output
sample), so it follows the loop jump exactly and needs no second timer. Left out: tempo changes, tap tempo,
automatic BPM detection.

**Loop editor: suggestions by self-comparison.** `loopfind.py` reduces the song to about 11 kHz, describes
each 2048-sample frame by 32 log-spaced band energies (song mean removed, unit length, silent frames zeroed),
builds the cosine self-similarity matrix and, for every loop length, averages the diagonal over about +-2 s.
Peaks are thinned by non-maximum suppression, one point is refined to the exact sample by waveform
cross-correlation and a seam score (waveform match just after and just before the jump) is added; candidates
with a seam score below 0.3 are dropped, and the windows at the file edges count at least half a window so
a short match at the very edge cannot win. No BPM is needed. It runs in a worker thread with a cancel flag.
Left out: tempo or key analysis, learning-based methods, crossfading the loop.
Memory: the matrix is frames x frames of float32, so it is capped at `MAX_FRAMES` (4000, 64 MB): a longer song
has `coarse_factor` neighbouring frames averaged into one (`_coarsen`, hop times k), so the matrix of a 20-minute song is
64 MB instead of about 670 MB. Whole search, measured with `tracemalloc` on a 20-minute stereo 44.1 kHz song: about
281 MB on top of the song (the mono mix-down alone is about 212 MB; a mono input peaks near 68 MB). `waveform.build_pyramid` folds the channels and builds the first level in
chunks of `CHUNK` frames (the result is identical to the all-at-once build, tested), so only the finished
~2 MB pyramid is kept. The decoded song itself (float32, about 423 MB for 20 minutes) is still whole in memory.

**Loop editor: seam view is a timeline remap, not a second widget.** In seam view the waveform's timeline is
the file up to the loop end followed by the loop's beginning (`loopmath.seam_source`, `seam_extent`,
`seam_tail`): `WaveformView.total()` returns the longer or shorter timeline, `frame_at` maps a click back to
the file sample that is heard there, and the part after the jump is painted with a sample shift (wave, grid
and ruler share one `shift` argument). The real outro is not shown at all, which is the point; the tail is
capped at 15 s so a loop ending at the end of a long song stays readable. Dragging and typed values still
work on real sample numbers. Left out: seam view during playback follow, a crossfade preview.

## Ideas for later

The plan for what comes next is in [ROADMAP.md](ROADMAP.md).

## Conventions

* New project-wide checks go through `health.SECTIONS` / `run_section`, so they run on demand and show up
  as a lazy tab.
* Anything that changes the project goes through `rename_engine` (backup, files first then JSON, rollback,
  Undo). Do not write project files from GUI code.
* Pure logic gets a unit test in `tests/`; anything that touches real Qt behaviour gets a step in
  `smoke_test_qt.py`.
* Test audio lives in `tests/data/` (see its README for how the files were made).

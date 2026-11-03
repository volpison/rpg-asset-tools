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

## Conventions

* New project-wide checks go through `health.SECTIONS` / `run_section`, so they run on demand and show up
  as a lazy tab.
* Anything that changes the project goes through `rename_engine` (backup, files first then JSON, rollback,
  Undo). Do not write project files from GUI code.
* Pure logic gets a unit test in `tests/`; anything that touches real Qt behaviour gets a step in
  `smoke_test_qt.py`.

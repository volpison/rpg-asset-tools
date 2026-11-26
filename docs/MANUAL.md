# User manual

1. [Getting started](#getting-started)
2. [The asset list](#the-asset-list)
3. [Health check](#health-check-ctrlh)
4. [Plugins are real references](#plugins-are-real-references)
5. [Galv's Layer Graphics](#galvs-layer-graphics-imglayers)
6. [Where is this used?](#where-is-this-used-ctrlu)
7. [Loop editor](#loop-editor-bgm-and-bgs)
8. [Safety, backups and Undo](#safety-backups-and-undo)
9. [Where things are stored](#where-things-are-stored)
10. [What it cannot see](#what-it-cannot-see)
11. [Speed on big projects](#speed-on-big-projects)
12. [Troubleshooting](#troubleshooting)

## Getting started

Start the app (`python main.py`, or `check_dependencies.bat` on Windows) and open your project folder: the
one containing `data/`, `img/` and `audio/`.

Work on a **copy** the first time. The first time the tool is about to change your project (rename, merge,
move) it asks you to confirm that you have a copy; there is a "do not show again for this project" tick
box. Close RPG Maker MZ first so it cannot overwrite the changes.

## The asset list

| Status | Meaning |
|---|---|
| **OK** | referenced, file exists |
| **Missing** | referenced, but no file. *Rename...* re-points the references to an existing file |
| **Unused** | no reference anywhere (data, notes, scripts, plugins, effects) - safe to move |
| **Possible** | no hard reference, but the name appears in plugins / notes / scripts / effects - never moved automatically |
| **Engine** | default `img/system` file the engine loads by name |
| **Kept** | you flagged it "keep" |

* **Rename... (F2)**: select one or many rows. The dialog shows, live, every file and data
  field that will change and blocks conflicts. Tools: find/replace (regex), prefix/suffix,
  move to folder, paste a list from a spreadsheet, load a CSV / old `rename_mapping.json`.
* **Move unused...**: moves only *Unused* assets into `_unused/` folders next to them.
* **Undo**: reverts the last rename / move (refuses if you edited the data files in RPG Maker since).
* Notes and highlight colours save automatically (the colour menu shows a swatch for each colour);
  **Export...** writes JSON / CSV.

### Unused files go to `_unused/`

Everything the app moves aside ("Move unused", merged duplicates, not-loaded plugin files, stray map
copies) goes into a folder called `_unused` next to the original (`img/pictures/_unused/`,
`js/plugins/_unused/`, `data/_unused/`). The underscore keeps it at the top of the folder list. Folders
named `unused` from older versions are still recognised.

## Health check (Ctrl+H)

Seven project checks in one window. It never changes anything itself; each button hands
the job to the normal preview / confirm / undo path.

**Run only what you need.** The Health check window opens instantly and every tab starts empty. Press
**Run this check** on a tab, **Refresh <tab>** to redo the tab you are looking at, or **Check all tabs**
for the ones not yet run. Results are kept while the project is not re-scanned, so closing and reopening
the window is free. Slow checks (Duplicates, Database, Maps) show a progress window; the others take moments.
Empty results are explained in the tab ("No identical files found.", "Nothing to report ...").

Tabs, in order: Duplicates, Naming | Plugin params, Plugin files | Database, Maps | Deploy size.

In the Database and Maps tabs the list is on top and the details below; drag the divider to give the
list more room. Columns can be dragged, resized and reordered in every tab.

### Duplicates

Byte-identical files in the same folder (audio twins compared as a set).
Pick the asset to keep, then *Merge*: every reference is re-pointed to it and the copies go to
`_unused/`. Undoable. (Enemies/SV enemies, animation textures, effects and engine files are never auto-merged.)

### Naming

Catches *case mismatches* (`"hero"` in the data, `Hero.png` on disk: fine on Windows,
broken on Android / web / Linux), spaces, non-ASCII characters, names that differ only by case, and
your own convention. One suggested name per asset fixes everything; *Fix...* opens the rename
dialog pre-filled so you still see the preview. Your convention lives in `.asset_tools/rules.json`,
for example `{"categories": {"se": {"prefix": "se_", "style": "snake"}}}`
(styles: `lower`, `snake`, `pascal`).

### Plugin params

*Plugin params* = plugin parameters that hold asset names. Lists undeclared parameters (the header says
`@type string`) whose value is exactly an existing asset name. *Treat as references* stores the choice in
`.asset_tools/plugin_params.json`; see [Plugins are real references](#plugins-are-real-references).

### Plugin files

An overview of `js/plugins/` against `js/plugins.js`: status, size, commands called / declared, asset
references, and dependencies (`@base` in the headers). These are the `.js` files themselves, not their parameters.

* **Not loaded**: the file exists but `plugins.js` has no entry. The game never uses it, so it can be
  moved to `js/plugins/_unused/` (button; Undo available). A file another loaded plugin needs (`@base`)
  is never offered. The Deploy size tab lists `_unused/` so you leave it out of the build, and
  *Move _unused/ folders out* takes it along.
* **Disabled**: kept (you may enable it later). To remove it, delete its entry in the Plugin Manager.
* **Entry without file**: the game cannot load it; remove the entry in the Plugin Manager or restore the file.
* **Enabled** plugins are never called "unused": many change the engine and nothing refers to them
  (menu layouts, windows ...). The tab only shows how many command calls and asset references they have.
* *Keep* flags a file you want to stay where it is. Removing plugins stays the Plugin Manager's job.

### Database

Finds entries nothing refers to: items, weapons, armors, skills, states, actors, classes, enemies,
troops, common events, animations - and **switches and variables**.

* Looks at traits, effects, drops, enemy actions, class learnings, troop members/pages, every event
  command (maps, common events, troops) incl. page conditions and encounter lists, text codes
  `\V[n]` / `\N[n]`, script calls (`$gameSwitches.value(3)`, `$dataItems[5]`, `reserveCommonEvent(2)`),
  plugin parameters / commands declared `@type item|switch|variable|...`, and literal ids in `js/plugins/*.js`.
* Switches/variables get two extra answers: **set, never checked** and **checked, never set**.
* **Broken reference**: something points at an entry that is empty or does not exist.
* Engine-fixed entries (Attack, Guard, Death) are never reported. *Keep / un-keep* flags entries you want
  on purpose (stored in `.asset_tools/ignore.json`). *Export CSV* saves the current list.
* Honest limits: ids computed at run time (`$gameSwitches.value(x)`) and plugin numbers that are not
  declared as ids make entries show as *Possibly used*, never *Unused*; the caveats are listed under the
  table. Map an undeclared plugin parameter in `.asset_tools/plugin_params.json` with a database type,
  e.g. `"MyPlugin.QuestSwitch": "switch"`.
* The tab only reports. Delete entries in RPG Maker's own editor (it keeps ids stable), then re-check.

### Maps

Which maps can the player reach? Roots are the starting map and the vehicles' start maps
(`System.json`) plus any transfer found in common events and troops; edges are Transfer Player,
Set Vehicle Location and `reserveTransfer(map, ...)` inside each map's events. Results:
*Reachable*, *Unused* (nothing transfers there, or only other unused maps do), *Possibly used*
(a plugin parameter named like a map id, or a transfer whose destination is a variable),
*Orphan file* (`MapNNN.json` not in `MapInfos.json`: invisible in the editor but still shipped),
*File missing* (listed in `MapInfos.json` without a file) and *Broken target* (a transfer to a map that
does not exist). The tab also shows the assets only one map uses and the total used only by unused
maps (what deleting them in the editor would free). *Who sends the player here?* lists each transfer
with map / event / page / line. Read-only: delete maps in the editor so `MapInfos.json` stays consistent.
Limits: transfers made by plugin commands or computed in plugin code cannot be seen.

**Stray files in `data/`.** The tab also lists every `.json` in `data/` that is not one of the engine's
data files (Actors, Items, System, MapInfos ...) and not a `MapNNN.json`: copies and backups like
`Map004 - Copy.json`, `Copy of Map004.json`, `Map004.json.bak`, and any other leftover. The game never
loads them, but they still ship. A file whose name appears in a plugin (or `plugins.js`) is assumed to be
plugin data and is NOT listed. Select the rows and use *Move stray files to data/_unused/...* (undoable).

### Deploy size

Size per category, how much is unused / duplicated, largest files, images over
4096 px, very large PNG/audio, and things that should not ship: `_unused/` folders (a plain copy of
the project would include them), `.asset_tools`, `.git`, `data_backup_*`, test saves.
*Move _unused/ folders out of the project...* relocates them to a folder you choose, keeping the layout
(this one step is not covered by Undo). It is last because it is a size summary to read just before you build.

## Plugins are real references

Plugin parameters and plugin commands count as used, and renaming an asset updates them.

* **Automatic:** the tool reads each plugin's header in `js/plugins/*.js`. Parameters and command
  arguments declared `@type file` + `@dir img/pictures/` (also `file[]`, `struct<...>`, `struct<...>[]`,
  nested several levels) become references - in `js/plugins.js` and in Plugin Commands inside events.
* **Plugins that just say `@type string`:** Health check -> *Plugin params* tab lists undeclared parameters whose value
  is exactly an existing asset name. *Treat as references* stores the choice in
  `.asset_tools/plugin_params.json`, e.g. `{"params": {"MyPlugin.Background": "picture",
  "MyPlugin.Buttons.icon": "picture", "cmd:MyPlugin.show.pic": "picture"}}`
  (arrays add no path segment). Names that exist in several folders are ambiguous: set them in that file.
* `@dir img/pictures/ui/` works: the stored value `ico` is the asset `ui/ico`, and a rename must stay inside `ui/`.
* **Disabled plugins:** their files show as *Possible* (never missing, never moved) and are still
  updated by a rename, so re-enabling the plugin later does not break.
* `plugins.js` is rewritten in place keeping its layout; structs/arrays inside parameters are
  re-encoded in Plugin Manager's compact format, only for the parameters that changed.
  Rename, merge and Undo all cover it. If `plugins.js` cannot be parsed, moving/renaming is blocked.

## Galv's Layer Graphics (`img/layers/`)

`img/layers/` is a normal category ("Layers (Galv)"): listed, previewed, checked for size (whole-map
images easily pass 4096 px), renamed, moved to `_unused/` and merged like any other asset.
The rules come from reading `GALV_LayerGraphicsMZ.js` itself:

* **Plugin commands** *Create Layer* / *Create Static Layer*: the `graphic` argument (`@dir img/layers/`).
* **Map notes**: every line the plugin would turn into a layer - `LAYER id graphic ...` and
  `LAYER_S id graphic ...` - parsed exactly like the plugin does (first `LAYER`, split on single
  spaces, third field). A rename rewrites just that field and keeps the rest of the note and its line endings.
  Only active when the plugin is installed; if it is disabled the layers show as *Possible*, never *Missing*.
* **Script calls**: `Galv.LG.bLayer(id,'graphic',...)` (battle layers) and `ImageManager.loadLayerGraphic("name")`
  when the name is a literal.
* Layers named by a variable cannot be seen (the plugin's `v2` trick only applies to numbers, so the
  graphic name is normally literal). *Where is this used?* lists the map note line or command.
* Names with a space cannot be written into a map note, so a rename to such a name is refused.

## Where is this used? (Ctrl+U)

Also on right-click, and as a button in the Database tab. Shows every reference to the selected asset /
database entry as a path you can follow in the editor:
`Town (Map001) > event 3 'Guard' at (12, 8) > page 2 > line 5: Show Picture`. Troops, common
events, database files, plugin parameters and plugin files are named too. *Copy selected / Copy all*
puts the paths on the clipboard; *Show file in folder* opens the file's folder. RPG Maker cannot be
told to jump to a spot, so the map name, event position, page and line are the closest thing to a link.
Free-text mentions of a name (notes, scripts) are listed as *possible* uses with the file / event they are in.

## Loop editor (BGM and BGS)

Music in RPG Maker MZ is Ogg Vorbis (`.ogg`). A file can carry two tags, **LOOPSTART** and
**LOOPLENGTH**. When both are in the file, the game repeats that part of the song instead of the whole
file. Both numbers are counted in **samples** of the file itself (at 44100 Hz, 3 seconds is sample 132300;
other files may use 48000 Hz or 22050 Hz, and the editor reads the real rate). The loop ends at
LOOPSTART + LOOPLENGTH and then jumps back to LOOPSTART.

The loop editor shows these numbers on the waveform, lets you listen to them, change them and write them
back into the file. **Nothing is written until you press *Save tags...*.**

**Open it:** select one BGM or BGS row, then press *Loop editor...* in the details panel, or right-click
the row. (ME and SE play once, so they have no loop editor.) Only `.ogg` files open; an `.m4a` file or an
Ogg Opus file gets an explanation instead.

**One-time setup:** the loop editor needs two small extra packages, which the rest of the app does not:

```
python -m pip install numpy soundfile
```

(the same two packages are listed in `rpg_asset_tools/requirements-audio.txt`). Without them the button
explains what to install.

**What you see**

* The waveform, with a time ruler. **Mouse wheel** zooms, **Shift + wheel** or the scroll bar moves, a **click**
  moves the cursor. Zoom in far enough and you see single samples.
* An orange bar above the wave from the loop start to the loop end, labelled with LOOPSTART and LOOPLENGTH.
  A grey bar means the file has no loop tags, so the whole file repeats.
* Below the wave: LOOPSTART and LOOPLENGTH exactly as stored in the file, where the loop plays from and to,
  the file length, the cursor and the sample under the mouse, each in samples and as minutes:seconds.
* Remarks in plain words when something looks unusual (tag names in lower case, a value that is not a whole
  number, a loop that ends after the end of the file, LOOPSTART without LOOPLENGTH, and so on).

**Listening**

* *Play / Pause* plays from the cursor. *Stop* returns the cursor to where playing started.
* *Loop like the game* (on by default) jumps from the loop end back to the loop start, exactly on the sample.
  Turn it off to play the file once.
* *From loop start* starts at the loop start. *Hear the seam* starts 3 seconds before the loop end so you
  hear the jump itself: a click or a rhythm slip at the jump means the loop points are not right.

**Changing the loop**

* **Drag** the orange markers at the two ends of the loop bar. With *Snap to zero crossing* on (the default),
  a marker moves to the nearest place within 20 ms where the sound crosses zero going up; jumping between two
  such places does not click.
* Or **type** the numbers in *Loop start*, *Length* or *Loop end*: samples (`132300`), seconds (`3 s` or
  `3.5`) or minutes:seconds (`1:23.5`). Typed values are used exactly (no snapping). Changing *Loop start*
  keeps the loop end where it is; changing *Length* moves the end.
* *Start = cursor* / *End = cursor* put a marker where the cursor is (click the wave to place it). For a
  file without a loop, the first marker you set creates one.
* While you edit, *Play*, *From loop start* and *Hear the seam* use the new loop, so you can hear it before
  saving. *Revert* goes back to what the file holds.
* **Save tags...** shows the old and the new tags and asks. Then it copies the file to
  `.asset_tools/backups/`, writes the new tags, checks the result (same sound, same headers, and the values
  RPG Maker's own reader finds) and only then replaces the file. **Undo** in the main window puts the old
  file back (it refuses if the file was changed after saving). The sound itself is never changed, only the
  comment header. Close RPG Maker MZ and the game first so the file is not in use.
* Both tags are written in upper case, and LOOPSTART and LOOPLENGTH are placed so the game reads them
  correctly. If a file has a comment block too big for the game (a picture or a long text inside), it is
  refused with an explanation instead of being changed: nothing would be readable by the game.
* Closing the window with unsaved changes asks whether to save them.

**Beat grid, metronome and loops in bars**

Many songs are made at a fixed tempo, and a good loop is a whole number of bars. The second row of the editor
helps with that:

* Type the **BPM** and the **First beat** (where beat 1 of a bar falls: samples, seconds or minutes:seconds,
  or press *First beat = cursor* after clicking the wave on a downbeat). Set *beats per bar* (4 by default).
  The **Beat grid** is drawn over the wave: thin lines for beats, strong numbered lines for bars. Zoom in on
  the start of the song to place the first beat exactly. The grid runs backwards from the first beat too.
* The grid is remembered **for each song** in `.asset_tools/loop_grids.json` in your project (never in the
  audio file, so RPG Maker does not see it). It is tied to the file's path, so renaming the song forgets it.
  Clear the BPM box to remove a grid.
* **Snap to beat / Snap to bar** moves a dragged marker (or *Start/End = cursor*) to the nearest beat or bar
  line. While it is on it replaces the zero-crossing snap.
* **Loop length in bars** + *Set loop length* keeps the loop start and sets the end that many bars later
  (use a start on a bar line). *BPM from loop* does the opposite: if you know the loop is, say, 8 bars long, it
  works out the BPM from the loop length and puts the first beat at the loop start.
* **Metronome** adds a click on every beat (higher and louder on the first beat of a bar). It is mixed into
  the sound itself, so it stays exactly in time with the music and with the loop jump. It only plays in the
  editor; it is not written to the file. A song with a changing tempo needs a different approach (one fixed
  tempo per song for now).

**Suggest loops**

*Suggest loops...* (beside *End = cursor*) compares the song with itself to find places where the music
repeats, so that the jump from the loop end back to the loop start is hard to hear. It does not need a BPM,
but it works best on music with a clear beat or a repeating pattern.

* Choose *Find start and end*, or keep the current start (or end) and look for the other point only (within a
  quarter of a second of where it is now), and set the *Shortest loop*. Press *Search*; it runs in the
  background and takes a few seconds for a long song.
* The table lists up to 8 suggestions, best first, with the start, the end, the length (also in bars when the
  song has a beat grid) and a *Match* percentage: how alike the music, and the waveform right at the jump, are.
* **Use** puts the chosen points in the editor (nothing is saved until *Save tags...*; *Revert* undoes it).
  **Use and hear the seam** (or a double-click) also plays the jump. Always listen: it is a suggestion, not
  a promise. Songs without a repeating part give no suggestions.

**Good to know:** the editor shows the loop the way the game reads it. RPG Maker MZ (checked against
`js/rmmz_core.js` of version 1.0.0) does not read tags like a standard Vorbis tool does:

* Tag names must be in **upper case**; `loopstart` is not found.
* **LOOPSTART alone is ignored** and the whole file repeats. LOOPLENGTH alone starts the loop at sample 0.
* A loop needs a LOOPLENGTH above 0.
* A comment block long enough to run onto a second Ogg page (for example a big cover picture or long
  description stored before the loop tags) hides the tags from the game.
* If a tag is followed by a comment 48 to 57 bytes long, the game can add a digit to the number.

When the game would read something different from what the file says, a remark under the waveform says so.

## Safety, backups and Undo

* **Keep a copy.** See [Getting started](#getting-started).
* **Backups and Undo.** Before each change, the data files it edits are saved in `.asset_tools/backups/`
  (one backup per operation, only the files that change; the newest 20 are kept). Undo restores them, but refuses (and says why)
  if a file was changed by something else in the meantime, so work done in RPG Maker after the operation
  is never discarded.
* Exact-field JSON rewrite through the same walker the scanner uses.
* `enemy` + `sv_enemy` share one JSON field and move together; faces/characters stay separate.
* Audio twins (`.ogg` + `.m4a`) are one asset and move together.
* Files first, JSON second; any failure rolls both back.
* If any `data/*.json` cannot be parsed, moving and renaming are disabled (references would be invisible).
* JSON is written in the same compact format RPG Maker uses, so Git diffs stay small.

## Where things are stored

Everything the tool creates lives in `<project>/.asset_tools/` (comments, colours, keep flags,
history, backups, caches). **Delete or exclude that folder before you deploy / distribute the game.**
Add `.asset_tools/` to `.gitignore` if you use Git.
Files from the first version (`asset_comments.json`, `history.jsonl`, ...) are moved there automatically.

## What it cannot see

Names built at runtime in scripts/plugins (`"Pic" + n`), parameters of plugins whose header doesn't say
`@type file` (until you map them), and notetag conventions of third-party plugins. Those show as *Possible*
or *Unused*. Check *Unused* once before moving, and use *Keep* for anything it cannot know about. A rename
warns when the old name is also mentioned in plugins / notes / scripts, because those places are not edited
automatically.

Other limits: plugin commands written in the old MV text style (event command 356) are not parsed; the
Maps check cannot see transfers made by plugin commands; the asset list does not mark assets that are used
only by unused maps (the Maps tab reports the total).

## Speed on big projects

* The first time a project is opened, every big `data/*.json` is read and analysed in **separate
  processes** (all cores; the window stays responsive), and a **progress window with a log** appears
  when it takes longer than a moment.
* The result per file is remembered in `.asset_tools/cache/` (JSON). Next time only the files that
  changed are read again. The cache is thrown away automatically when `plugins.js`, a plugin header or
  `plugin_params.json` changes. **It is safe to delete that folder**; it only makes the next open slower.
* The first run after a long time (or after a reboot) is several times slower than the next ones, because
  the operating system and antivirus have to read every file again. That part cannot be cached.
* Duplicates and Deploy size need to open many asset files (hash them / read PNG headers). Results are
  remembered in `.asset_tools/cache/filefacts.json` while a file's size and modification time are unchanged.
* Many small files (such as hundreds of tiny map files) share one cache file per check.
* If starting helper processes is impossible on your machine, the tool quietly does the work in one process.

## Troubleshooting

**The app will not start.** Run `check_dependencies.bat` (Windows) or `python check_env.py`. It tells you
whether Python is too old, PySide6 is missing, or app files are incomplete. A very new Python version may
not have a PySide6 build yet; use a 64-bit Python 3.9 to 3.13 from python.org.

**The window freezes.** The app keeps a small watchdog: if the window does not answer for about 8 seconds
it writes what every thread was doing to `~/.rpg_asset_tools/hang.log`
(Windows: `C:\Users\<you>\.rpg_asset_tools\hang.log`). Send that file with the bug report; it shows exactly
where the program was stuck.

**Messages in the console.** Qt may print lines such as `Cannot find font directory` or
`This plugin does not support propagateSizeHints()`. They appeared in test runs where everything passed.

**Check the real window on your machine.** `python smoke_test_qt.py` (or double-click `run_smoke_test.bat`)
opens the real window hidden, builds a small fake project in a temp folder and goes through the main
screens, printing PASS/FAIL with timings. `--project "D:\Games\MyGame"` runs read-only timings on your own project.
See the [developer guide](DEVELOPMENT.md#real-window-smoke-test).

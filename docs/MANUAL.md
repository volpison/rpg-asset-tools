# User manual

1. [Getting started](#getting-started)
2. [The asset list](#the-asset-list)
3. [Health check](#health-check-ctrlh)
4. [Plugins are real references](#plugins-are-real-references)
5. [Galv's Layer Graphics](#galvs-layer-graphics-imglayers)
6. [Where is this used?](#where-is-this-used-ctrlu)
7. [Safety, backups and Undo](#safety-backups-and-undo)
8. [Where things are stored](#where-things-are-stored)
9. [What it cannot see](#what-it-cannot-see)
10. [Speed on big projects](#speed-on-big-projects)
11. [Troubleshooting](#troubleshooting)

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

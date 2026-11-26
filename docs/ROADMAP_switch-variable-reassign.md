# Roadmap: Switch / Variable ID Reassign

**Status:** Phase 0 – design + read-only scanner (this branch)
**Branch:** `grok/switch-variable-reassign`
**Risk level:** High (touches every event command that references a switch or variable ID)

## Goal

Let the user change a switch or variable **numeric ID** everywhere it is used, for example:

- every `Control Switches` that sets switch 10
- every conditional branch that tests switch 10
- page conditions, troop conditions, common-event auto-run conditions
- script calls with literal IDs (`$gameSwitches.value(10)`, `$gameSwitches.setValue(10, …)`)
- the same for variables

…into a different ID, with a full preview and strong safety nets.

This is **not** renaming the label in `System.json`. Labels stay cosmetic. This feature rewrites the IDs that commands actually store.

## Why this is dangerous

- One wrong rewrite can break progress flags, conditions, and save compatibility.
- Switches/variables appear in many places (maps, common events, troops, page conditions, scripts, text codes).
- Some references are dynamic (`$gameSwitches.value(x)` where `x` is a variable). Those must never be rewritten automatically.

Because of that, the feature is built in strict phases. **No write happens until Phase 2 is proven.**

## Phases

### Phase 0 – This PR (safe foundation)

- [x] Roadmap document (this file)
- [x] Read-only scanner: given `(kind, old_id)` find every *literal* usage
- [x] Dry-run report: list of locations that *would* change (file, event, page, command, code, role)
- [x] Clear separation from asset rename / System.json label rename
- [ ] Unit tests on small synthetic maps / common events

**Deliverable:** a function the UI (or CLI) can call that returns a list of hits and never touches disk.

### Phase 1 – Preview UI (next)

- Simple dialog: kind (switch/variable), old ID, new ID
- Show the dry-run list (grouped by file)
- Conflict checks:
  - new ID already used?
  - new ID out of range / reserved?
  - dynamic references that mention the old ID in a non-literal way (warn only)
- Explicit “This will not change anything yet” messaging

### Phase 2 – Write path (only after Phase 1 feels solid)

- Backup of every affected `data/*.json` before any write (reuse existing `.asset_tools/backups/`)
- Apply the ID change only to the locations the scanner found
- Atomic-enough write (write temp → replace), per-file rollback on failure
- Undo entry that restores the backups
- Refuse to run if any target file changed on disk since the scan (signature check)

### Phase 3 – Optional polish

- Batch map: several old→new pairs in one pass
- “Compact unused IDs” helper (only after unused detection from Health Check is trusted)
- Optional update of the *label* in `System.json` when the ID moves (separate checkbox, off by default)

## What we rewrite (and what we never touch)

| Location | Rewrite? | Notes |
|----------|----------|--------|
| Control Switches (code 121) | Yes | parameters hold the ID |
| Control Variables (code 122) when the operand is a variable | Yes | careful with operand type |
| Conditional Branch – switch / variable tests (code 111) | Yes | |
| Map / troop / common-event page conditions | Yes | |
| Enemy action conditions that use a switch | Yes | |
| Script: `$gameSwitches.value(10)`, `.setValue(10, …)` and variable equivalents with a **literal** number | Yes | |
| Text codes `\V[10]` | Yes (variables only) | |
| Script with computed ID (`$gameSwitches.value(x)`) | **Never** | report as dynamic / warning only |
| Plugin parameters that happen to hold a number | **Never** unless declared `@type switch` / `@type variable` | |
| System.json `switches` / `variables` name arrays | **Not in Phase 2** | label-only; optional later |

## Implementation notes (for contributors)

- Reuse the event-walking style from `dbscan.py` and `refs.py`. Do **not** invent a second, divergent walker for the write path.
- Prefer a dedicated module (`id_reassign.py`) that:
  1. Scans (read-only)
  2. Builds a plan (`list[IdHit]`)
  3. Later applies the plan under backup + signature checks
- Every hit must carry enough location data for the UI and for a precise rewrite (`file`, `event`, `page`, `command`, `code`, `role`).
- Dry-run must be the default. A live rewrite requires an explicit confirmation step.

## Success criteria for merging the write path

1. Unit tests cover: map page condition, control switch, conditional branch, common event, troop, script literal, text code.\V.
2. Dry-run on a real project (e.g. AdventureGameRPG copy) produces a reviewable list with zero crashes.
3. Apply + undo on a disposable copy leaves the project loadable in RPG Maker MZ and in-game progress flags behaving as expected for the remapped ID.
4. Dynamic / non-literal references are never modified; they only appear as warnings.

## Non-goals (for now)

- Full visual event editor
- Automatic “smart” compaction of the entire switch/variable table
- Rewriting plugin JS source
- Changing save-file contents (old saves that stored the previous ID will still see the old number – document this limitation)

---

*Requested by Highlady Salia (bulk re-ID of switches used in events). Built in the same safety-first style as the rest of RPG Asset Tools.*

"""
dbscan.py
---------
"Is this database entry used anywhere?"  For items, weapons, armors, skills,
states, actors, classes, enemies, troops, common events, animations, and for
switches and variables.

Where we look
  * the other database files (traits, effects, drops, actions, learnings ...)
  * every event command in maps, common events and troops (also page conditions)
  * script calls ($gameSwitches.value(12), $dataItems[3], reserveCommonEvent(2) ...)
  * text codes   \\V[n]  \\N[n]  in any text of data/*.json
  * plugin parameters / plugin commands declared as a database type
    (@type item / switch / variable ...), and literal ids in js/plugins/*.js

What we CANNOT see is reported honestly: ids computed at run time
($gameSwitches.value(x) with a variable x) and plugin parameters that hold a
number but do not say what it is.  Those make the result "possible", never
"unused", and are listed as caveats.

Switches and variables get a more useful answer than "used / unused":
  write-only  (set by events, never checked)      read-only  (checked, never set)
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import plugin_refs
from filecache import FileCache, signature
from parallel import run_parallel
from progress import report

TYPES = ("item", "weapon", "armor", "skill", "state", "actor", "class", "enemy", "troop",
         "common_event", "animation", "switch", "variable")

TYPE_LABELS = {
    "item": "Items", "weapon": "Weapons", "armor": "Armors", "skill": "Skills",
    "state": "States", "actor": "Actors", "class": "Classes", "enemy": "Enemies",
    "troop": "Troops", "common_event": "Common events", "animation": "Animations",
    "switch": "Switches", "variable": "Variables",
}

DB_FILES = {
    "item": "Items.json", "weapon": "Weapons.json", "armor": "Armors.json",
    "skill": "Skills.json", "state": "States.json", "actor": "Actors.json",
    "class": "Classes.json", "enemy": "Enemies.json", "troop": "Troops.json",
    "common_event": "CommonEvents.json", "animation": "Animations.json",
}

# The engine itself uses these: never report them
ENGINE_FIXED = {("skill", 1): "Attack (used by the engine)",
                ("skill", 2): "Guard (used by the engine)",
                ("state", 1): "Death (used by the engine)"}

MAX_REFS_PER_ENTRY = 60

# Statuses
USED, UNUSED, POSSIBLE, ENGINE, BROKEN = "used", "unused", "possible", "engine", "broken"
WRITE_ONLY, READ_ONLY, KEPT = "write_only", "read_only", "kept"
NEEDS_ATTENTION = (UNUSED, WRITE_ONLY, READ_ONLY, POSSIBLE, BROKEN)

STATUS_TEXT = {
    USED: "Used", UNUSED: "Unused", POSSIBLE: "Possibly used", ENGINE: "Engine",
    BROKEN: "Broken reference", WRITE_ONLY: "Set, never checked",
    READ_ONLY: "Checked, never set", KEPT: "Kept (flagged)",
}


@dataclass
class DbRef:
    kind: str                    # "set" | "read" | "ref"
    where: str
    optional: bool = False       # only a disabled plugin / unused plugin file does this
    loc: dict = field(default_factory=dict)   # file / event / page(0-based) / command(0-based)


@dataclass
class DbScan:
    entries: dict = field(default_factory=dict)        # type -> {id: name}
    refs: dict = field(default_factory=dict)           # (type, id) -> [DbRef] (capped)
    ref_total: dict = field(default_factory=dict)      # (type, id) -> real count
    errors: list = field(default_factory=list)
    dynamic_uses: dict = field(default_factory=dict)   # type -> [(kind, where, text)]
    plugin_caveats: list = field(default_factory=list)  # human-readable lines
    files_scanned: int = 0
    cur: dict = field(default_factory=dict)            # where the walker is right now
    kinds: dict = field(default_factory=dict)          # (type, id) -> {(kind, optional)}  EXACT
    sample_cap: int = MAX_REFS_PER_ENTRY               # stored example places per (entry, kind)
    _sample_n: dict = field(default_factory=dict, repr=False)

    def add(self, typ: str, ident, kind: str, where: str, optional: bool = False) -> None:
        try:
            ident = int(ident)
        except (TypeError, ValueError):
            return
        if ident <= 0:
            return
        key = (typ, ident)
        self.ref_total[key] = self.ref_total.get(key, 0) + 1
        self.kinds.setdefault(key, set()).add((kind, optional))
        n = self._sample_n.get((key, kind, optional), 0)
        if n < self.sample_cap:                       # examples only; kinds above are exact
            self._sample_n[(key, kind, optional)] = n + 1
            self.refs.setdefault(key, []).append(DbRef(kind, where, optional, dict(self.cur)))

    def dynamic(self, typ: str, kind: str, where: str, text: str) -> None:
        self.dynamic_uses.setdefault(typ, []).append((kind, where, text))


@dataclass
class DbRow:
    type: str
    id: int
    name: str
    status: str
    note: str = ""               # why: short explanation
    used_by: list = field(default_factory=list)   # [DbRef]
    total_refs: int = 0

    @property
    def key(self) -> str:
        return f"db:{self.type}:{self.id}"


# --------------------------------------------------------------------------
# script scanning
# --------------------------------------------------------------------------

_GLOBAL_DATA = {
    "$dataItems": "item", "$dataWeapons": "weapon", "$dataArmors": "armor",
    "$dataSkills": "skill", "$dataStates": "state", "$dataActors": "actor",
    "$dataClasses": "class", "$dataEnemies": "enemy", "$dataTroops": "troop",
    "$dataCommonEvents": "common_event", "$dataAnimations": "animation",
}
_RE_SWVAR = re.compile(r"\$game(Switches|Variables)\s*\.\s*(value|setValue)\s*\(\s*([^,)]*)")
_RE_DATA = re.compile(r"(\$data(?:Items|Weapons|Armors|Skills|States|Actors|Classes|Enemies|"
                      r"Troops|CommonEvents|Animations))\s*\[\s*([^\]]*?)\s*\]")
_RE_ACTOR = re.compile(r"\$gameActors\s*\.\s*actor\s*\(\s*([^)]*?)\s*\)")
_RE_COMMON = re.compile(r"reserveCommonEvent\s*\(\s*([^)]*?)\s*\)")
_RE_TEXT_V = re.compile(r"\\[Vv]\[(\d+)\]")
_RE_TEXT_N = re.compile(r"\\[Nn]\[(\d+)\]")
_RE_LITERAL = re.compile(r"^\d+$")


def scan_script(text: str, scan: DbScan, where: str, optional: bool = False) -> None:
    """Literal ids become references; computed ids are recorded as dynamic."""
    for m in _RE_SWVAR.finditer(text):
        typ = "switch" if m.group(1) == "Switches" else "variable"
        kind = "set" if m.group(2) == "setValue" else "read"
        arg = m.group(3).strip()
        if _RE_LITERAL.match(arg):
            scan.add(typ, arg, kind, where, optional)
        elif not optional:
            scan.dynamic(typ, kind, where, m.group(0).strip())
    for m in _RE_DATA.finditer(text):
        typ = _GLOBAL_DATA[m.group(1)]
        arg = m.group(2).strip()
        if _RE_LITERAL.match(arg):
            scan.add(typ, arg, "ref", where, optional)
        elif not optional:
            scan.dynamic(typ, "ref", where, m.group(0).strip())
    for rx, typ in ((_RE_ACTOR, "actor"), (_RE_COMMON, "common_event")):
        for m in rx.finditer(text):
            arg = m.group(1).strip()
            if _RE_LITERAL.match(arg):
                scan.add(typ, arg, "ref", where, optional)
            elif not optional and arg:
                scan.dynamic(typ, "ref", where, m.group(0).strip())


def scan_text_codes(obj, scan: DbScan, where: str) -> None:
    """\\V[n] (reads a variable) and \\N[n] (actor name) in any string below obj."""
    if isinstance(obj, str):
        if "\\" in obj:
            for m in _RE_TEXT_V.finditer(obj):
                scan.add("variable", m.group(1), "read", where)
            for m in _RE_TEXT_N.finditer(obj):
                scan.add("actor", m.group(1), "ref", where)
    elif isinstance(obj, list):
        for x in obj:
            scan_text_codes(x, scan, where)
    elif isinstance(obj, dict):
        for v in obj.values():
            scan_text_codes(v, scan, where)


# --------------------------------------------------------------------------
# the walker
# --------------------------------------------------------------------------

TRAIT_STATE = {13, 14, 32}
TRAIT_SKILL = {35, 43, 44}
EFFECT_STATE = {21, 22}


class _Walker:
    def __init__(self, scan: DbScan, schema, plugin_enabled: dict, raw: dict):
        self.s = scan
        self.schema = schema
        self.enabled = plugin_enabled
        self.raw = raw                    # type -> {id: dict}  (for equip slot resolution)

    # ---- helpers -----------------------------------------------------
    def ref(self, typ, ident, where, kind="ref"):
        self.s.add(typ, ident, kind, where)

    def traits(self, traits, where):
        for t in traits or []:
            if not isinstance(t, dict):
                continue
            code, data = t.get("code"), t.get("dataId")
            if code in TRAIT_STATE:
                self.ref("state", data, f"{where}: trait")
            elif code in TRAIT_SKILL:
                self.ref("skill", data, f"{where}: trait")

    def effects(self, effects, where):
        for e in effects or []:
            if not isinstance(e, dict):
                continue
            code, data = e.get("code"), e.get("dataId")
            if code in EFFECT_STATE:
                self.ref("state", data, f"{where}: effect")
            elif code == 43:
                self.ref("skill", data, f"{where}: effect (learn skill)")
            elif code == 44:
                self.ref("common_event", data, f"{where}: effect (common event)")

    def equips(self, equips, where):
        for i, ident in enumerate(equips or []):
            if not isinstance(ident, int) or ident <= 0:
                continue
            if i == 0:
                self.ref("weapon", ident, f"{where}: equipment")
                continue
            armor = self.raw.get("armor", {}).get(ident)
            if armor and armor.get("etypeId") == i + 1:
                self.ref("armor", ident, f"{where}: equipment")
            elif ident in self.raw.get("weapon", {}) and not armor:
                self.ref("weapon", ident, f"{where}: equipment (dual wield)")
            else:
                self.ref("armor", ident, f"{where}: equipment")

    def var_if(self, flag, ident, where):
        if flag == 1:
            self.s.add("variable", ident, "read", where)

    # ---- database files ----------------------------------------------
    def database(self, typ, data, file):
        if not isinstance(data, list):
            return
        for item in data:
            if not isinstance(item, dict):
                continue
            where = f"{file} #{item.get('id')} {item.get('name', '')}".strip()
            self.s.cur = {"file": file, "event": item.get("id")}
            getattr(self, "db_" + typ, lambda *_: None)(item, where)

    def db_item(self, it, where):
        self.effects(it.get("effects"), where)
        if (it.get("animationId") or 0) > 0:
            self.ref("animation", it["animationId"], f"{where}: animation")

    def db_skill(self, it, where):
        self.effects(it.get("effects"), where)
        if (it.get("animationId") or 0) > 0:
            self.ref("animation", it["animationId"], f"{where}: animation")

    def db_weapon(self, it, where):
        self.traits(it.get("traits"), where)
        if (it.get("animationId") or 0) > 0:
            self.ref("animation", it["animationId"], f"{where}: animation")

    def db_armor(self, it, where):
        self.traits(it.get("traits"), where)

    def db_state(self, it, where):
        self.traits(it.get("traits"), where)

    def db_actor(self, it, where):
        self.ref("class", it.get("classId"), f"{where}: class")
        self.equips(it.get("equips"), where)
        self.traits(it.get("traits"), where)

    def db_class(self, it, where):
        for ln in it.get("learnings") or []:
            if isinstance(ln, dict):
                self.ref("skill", ln.get("skillId"), f"{where}: learns at level {ln.get('level')}")
        self.traits(it.get("traits"), where)

    def db_enemy(self, it, where):
        for act in it.get("actions") or []:
            if not isinstance(act, dict):
                continue
            self.ref("skill", act.get("skillId"), f"{where}: action")
            ct = act.get("conditionType")
            if ct == 4:
                self.ref("state", act.get("conditionParam1"), f"{where}: action condition")
            elif ct == 6:
                self.s.add("switch", act.get("conditionParam1"), "read", f"{where}: action condition")
        for d in it.get("dropItems") or []:
            if isinstance(d, dict):
                typ = {1: "item", 2: "weapon", 3: "armor"}.get(d.get("kind"))
                if typ:
                    self.ref(typ, d.get("dataId"), f"{where}: drop")
        self.traits(it.get("traits"), where)

    def db_troop(self, it, where):
        for m in it.get("members") or []:
            if isinstance(m, dict):
                self.ref("enemy", m.get("enemyId"), f"{where}: member")
        for pi, page in enumerate(it.get("pages") or [], 1):
            if not isinstance(page, dict):
                continue
            pw = f"{where}, page {pi}"
            self.s.cur["page"] = pi - 1
            c = page.get("conditions") or {}
            if c.get("switchValid"):
                self.s.add("switch", c.get("switchId"), "read", f"{pw}: condition")
            if c.get("actorValid"):
                self.ref("actor", c.get("actorId"), f"{pw}: condition")
            self.commands(page.get("list"), pw)

    def db_common_event(self, it, where):
        if it.get("trigger", 0) > 0 and (it.get("switchId") or 0) > 0:
            self.s.add("switch", it["switchId"], "read", f"{where}: auto-run condition")
            self.ref("common_event", it.get("id"), f"{where}: runs by itself (auto/parallel)")
        self.commands(it.get("list"), where)

    # ---- system ------------------------------------------------------
    def system(self, data):
        if not isinstance(data, dict):
            return
        self.s.cur = {"file": "System.json"}
        for a in data.get("partyMembers") or []:
            self.ref("actor", a, "System: starting party")
        for b in data.get("testBattlers") or []:
            if isinstance(b, dict):
                self.ref("actor", b.get("actorId"), "System: battle test")
                self.equips(b.get("equips"), "System: battle test")
        self.ref("troop", data.get("testTroopId"), "System: battle test troop")

    # ---- maps ----------------------------------------------------------
    def map(self, data, file):
        if not isinstance(data, dict):
            return
        self.s.cur = {"file": file}
        for enc in data.get("encounterList") or []:
            if isinstance(enc, dict):
                self.ref("troop", enc.get("troopId"), f"{file}: random encounter")
        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            ew = f"{file} event {ev.get('id')} '{ev.get('name', '')}'"
            self.s.cur = {"file": file, "event": ev.get("id")}
            for pi, page in enumerate(ev.get("pages") or [], 1):
                if not isinstance(page, dict):
                    continue
                pw = f"{ew} page {pi}"
                self.s.cur["page"] = pi - 1
                c = page.get("conditions") or {}
                if c.get("switch1Valid"):
                    self.s.add("switch", c.get("switch1Id"), "read", f"{pw}: condition")
                if c.get("switch2Valid"):
                    self.s.add("switch", c.get("switch2Id"), "read", f"{pw}: condition")
                if c.get("variableValid"):
                    self.s.add("variable", c.get("variableId"), "read", f"{pw}: condition")
                if c.get("itemValid"):
                    self.ref("item", c.get("itemId"), f"{pw}: condition")
                if c.get("actorValid"):
                    self.ref("actor", c.get("actorId"), f"{pw}: condition")
                self.commands(page.get("list"), pw)

    # ---- event commands --------------------------------------------------
    def commands(self, cmds, where):
        if not isinstance(cmds, list):
            return
        script_buf, script_where, script_idx = [], where, 0
        cur = self.s.cur
        for idx, cmd in enumerate(cmds):
            if not isinstance(cmd, dict):
                continue
            code, P = cmd.get("code"), cmd.get("parameters")
            if not isinstance(code, int) or not isinstance(P, list):
                continue
            w = f"{where}, command {idx + 1}"
            if code in (355, 655):                     # script lines belong together
                if code == 355:
                    self._flush_script(script_buf, script_where, script_idx)
                    script_buf, script_where, script_idx = [], w, idx
                script_buf.append(str(P[0]) if P else "")
                continue
            self._flush_script(script_buf, script_where, script_idx)
            script_buf = []
            cur["command"] = idx
            try:
                self.command(code, P, w)
            except (IndexError, TypeError, AttributeError):
                continue                               # malformed command: skip, never crash
        self._flush_script(script_buf, script_where, script_idx)
        cur.pop("command", None)

    def _flush_script(self, buf, where, idx=0):
        if buf:
            self.s.cur["command"] = idx
            scan_script("\n".join(buf), self.s, where)

    def command(self, code, P, w):
        s, ref, var_if = self.s, self.ref, self.var_if
        if code in (103, 104, 285):
            s.add("variable", P[0], "set", w)
            if code == 285:
                var_if(P[2], P[3], w)
                var_if(P[2], P[4], w)
        elif code == 111:
            t = P[0]
            if t == 0:
                s.add("switch", P[1], "read", w)
            elif t == 1:
                s.add("variable", P[1], "read", w)
                var_if(P[2], P[3], w)
            elif t == 4:
                ref("actor", P[1], w)
                sub = P[2]
                typ = {2: "class", 3: "skill", 4: "weapon", 5: "armor", 6: "state"}.get(sub)
                if typ:
                    ref(typ, P[3], w)
            elif t == 5 and P[2] == 1:
                ref("state", P[3], w)
            elif t == 8:
                ref("item", P[1], w)
            elif t == 9:
                ref("weapon", P[1], w)
            elif t == 10:
                ref("armor", P[1], w)
            elif t == 12:
                scan_script(str(P[1]), s, w)
        elif code == 117:
            ref("common_event", P[0], w)
        elif code == 121:
            for i in range(int(P[0]), int(P[1]) + 1):
                s.add("switch", i, "set", w)
        elif code == 122:
            for i in range(int(P[0]), int(P[1]) + 1):
                s.add("variable", i, "set", w)
            ot = P[3]
            if ot == 1:
                s.add("variable", P[4], "read", w)
            elif ot == 3:
                typ = {0: "item", 1: "weapon", 2: "armor", 3: "actor", 4: "enemy"}.get(P[4])
                if typ and typ != "enemy":
                    ref(typ, P[5], w)
            elif ot == 4:
                scan_script(str(P[4]), s, w)
        elif code in (126, 127, 128):
            ref({126: "item", 127: "weapon", 128: "armor"}[code], P[0], w)
            var_if(P[2], P[3], w)
        elif code == 125:
            var_if(P[1], P[2], w)
        elif code == 129:
            ref("actor", P[0], w)
        elif code == 201:
            if P[0] == 1:
                for i in (1, 2, 3):
                    s.add("variable", P[i], "read", w)
        elif code == 202:
            if P[1] == 1:
                for i in (2, 3, 4):
                    s.add("variable", P[i], "read", w)
        elif code == 203:
            if P[1] == 1:
                for i in (2, 3):
                    s.add("variable", P[i], "read", w)
        elif code in (212, 337):
            ref("animation", P[1], w)
        elif code in (231, 232):
            if P[3] == 1:
                s.add("variable", P[4], "read", w)
                s.add("variable", P[5], "read", w)
        elif code == 301:
            if P[0] == 0:
                ref("troop", P[1], w)
            else:
                s.add("variable", P[1], "read", w)
        elif code in (302, 605):
            typ = {0: "item", 1: "weapon", 2: "armor"}.get(P[0])
            if typ:
                ref(typ, P[1], w)
        elif code == 303:
            ref("actor", P[0], w)
        elif code in (311, 312, 326, 313, 314, 315, 316, 317, 318):
            if P[0] == 0:
                ref("actor", P[1], w)
            else:
                s.add("variable", P[1], "read", w)
            if code in (311, 312, 326, 315, 316):
                var_if(P[3], P[4], w)
            elif code == 317:
                var_if(P[4], P[5], w)
            elif code == 313:
                ref("state", P[3], w)
            elif code == 318:
                ref("skill", P[3], w)
        elif code == 319:
            ref("actor", P[0], w)
            if P[2]:
                ref("weapon" if P[1] == 1 else "armor", P[2], w)
        elif code in (320, 322, 324, 325, 323):
            if code != 323:
                ref("actor", P[0], w)
        elif code == 321:
            ref("actor", P[0], w)
            ref("class", P[1], w)
        elif code in (331, 332, 342):
            var_if(P[2], P[3], w)
        elif code == 333:
            ref("state", P[2], w)
        elif code == 336:
            ref("enemy", P[1], w)
        elif code == 339:
            if P[0] == 1:
                ref("actor", P[1], w)
            ref("skill", P[2], w)
        elif code == 45 or code == 205:
            self._route(code, P, w)
        elif code == 357:
            self._plugin_command(P, w)
        elif code == 505:
            if P and isinstance(P[0], dict) and P[0].get("code") == 45:
                scan_script(str((P[0].get("parameters") or [""])[0]), s, w)

    def _route(self, code, P, w):
        if code == 205 and len(P) > 1 and isinstance(P[1], dict):
            for rc in P[1].get("list") or []:
                if isinstance(rc, dict) and rc.get("code") == 45:
                    scan_script(str((rc.get("parameters") or [""])[0]), self.s, w)

    def _plugin_command(self, P, w):
        if len(P) < 4 or not isinstance(P[3], dict):
            return
        plugin = str(P[0])
        optional = not self.enabled.get(plugin, True)
        for leaf in plugin_refs.command_leaves(plugin, str(P[1]), P[3], self.schema):
            if leaf.db_type:
                self._leaf(leaf, f"{w}: plugin command {plugin}.{P[1]}", optional)

    def _leaf(self, leaf, where, optional):
        for tok in re.findall(r"\d+", leaf.value):
            self.s.add(leaf.db_type, tok, "ref", where, optional)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def scan_database(game_folder: Path, log=None) -> DbScan:
    game = Path(game_folder)
    scan = DbScan()
    data_dir = game / "data"
    raw: dict = {}
    loaded: dict = {}
    if not data_dir.is_dir():
        scan.errors.append("data/ folder not found")
        return scan

    for typ, fname in DB_FILES.items():
        p = data_dir / fname
        scan.entries[typ] = {}
        if not p.is_file():
            continue
        try:
            data = _load(p)
        except Exception as e:
            scan.errors.append(f"{fname}: {e}")
            continue
        scan.files_scanned += 1
        loaded[typ] = data
        raw[typ] = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), int) and item["id"] > 0:
                    scan.entries[typ][item["id"]] = str(item.get("name", ""))
                    raw[typ][item["id"]] = item

    system = None
    sp = data_dir / "System.json"
    scan.entries["switch"], scan.entries["variable"] = {}, {}
    if sp.is_file():
        try:
            system = _load(sp)
            scan.files_scanned += 1
        except Exception as e:
            scan.errors.append(f"System.json: {e}")
    if isinstance(system, dict):
        for typ, key in (("switch", "switches"), ("variable", "variables")):
            names = system.get(key)
            if isinstance(names, list):
                for i, n in enumerate(names):
                    if i > 0:
                        scan.entries[typ][i] = str(n or "")

    schema = plugin_refs.load_schema(game)
    enabled: dict = {}
    plugin_leaves: list = []
    try:
        pf = plugin_refs.read_plugins_js(game)
    except (ValueError, OSError) as e:
        pf = None
        scan.errors.append(f"js/plugins.js: {e}")
    if pf is not None:
        for plug in pf.data:
            if isinstance(plug, dict):
                enabled[str(plug.get("name", ""))] = bool(plug.get("status", True))
        plugin_leaves = list(plugin_refs.plugins_js_leaves(pf.data, schema))

    w = _Walker(scan, schema, enabled, raw)
    for typ, data in loaded.items():
        w.database(typ, data, DB_FILES[typ])
    w.system(system)

    # maps (and anything else with events) and text codes everywhere
    by_file = {f: loaded.get(t) for t, f in DB_FILES.items()}
    by_file["System.json"] = system
    cache = FileCache(game)
    names = sorted(p.name for p in data_dir.glob("*.json"))
    extra = [n for n in names if n not in by_file]               # maps, MapInfos, Tilesets ...
    frags, todo, sigs = {}, [], {}
    for n in extra:
        try:
            sigs[n] = signature(data_dir / n)
        except OSError:
            sigs[n] = [0, 0]
        hit = cache.get("db", n, sigs[n])
        if hit is not None:
            frags[n] = hit
        else:
            todo.append(n)
    total = len(names)
    done0 = len(frags)
    report(log, done0, total, "database references")

    def tick(k):
        report(log, done0 + k, total, f"database references ({done0 + k}/{total})")

    for n, fr in zip(todo, run_parallel(_db_file_task, todo, _db_init,
                                        (str(game), schema, enabled), tick,
                                        weights=[sigs[n][0] for n in todo])):
        frags[n] = fr
        if "error" not in fr:
            cache.put("db", n, sigs[n], fr)
    cache.flush()
    for name in names:
        if name in by_file:
            data = by_file[name]
            if data is not None:
                scan.cur = {"file": name}
                scan_text_codes(data, scan, name)
        else:
            fr = frags[name]
            if "error" in fr:
                scan.errors.append(fr["error"])
            else:
                scan.files_scanned += 1
                _replay(scan, fr)

    _plugins(scan, plugin_leaves, game, enabled)
    if log:
        log(f"Database scan: {sum(len(v) for v in scan.entries.values())} entries, "
            f"{sum(scan.ref_total.values())} references.")
    return scan


_G: dict = {}


def _db_init(game: str, schema, enabled: dict) -> None:
    _G["game"], _G["schema"], _G["enabled"] = Path(game), schema, enabled


def _db_file_task(name: str) -> dict:
    """References found in one non-database data file (a map ...): a fragment that can be cached."""
    try:
        data = _load(_G["game"] / "data" / name)
    except Exception as e:
        return {"error": f"{name}: {e}"}
    frag = DbScan(sample_cap=12)
    if re.fullmatch(r"Map\d+\.json", name):
        _Walker(frag, _G["schema"], _G["enabled"], {}).map(data, name)
    frag.cur = {"file": name}
    scan_text_codes(data, frag, name)
    return {
        "refs": [[t, i, r.kind, r.where, r.optional, r.loc]
                 for (t, i), lst in frag.refs.items() for r in lst],
        "kinds": [[t, i, k, opt] for (t, i), ks in frag.kinds.items() for (k, opt) in ks],
        "totals": {f"{t}:{i}": n for (t, i), n in frag.ref_total.items()},
        "dyn": [[t, k, w, x] for t, lst in frag.dynamic_uses.items() for (k, w, x) in lst],
    }


def _replay(scan: DbScan, fr: dict) -> None:
    for t, i, k, opt in fr["kinds"]:
        scan.kinds.setdefault((t, i), set()).add((k, opt))
    for t, i, kind, where, opt, loc in fr["refs"]:
        n = scan._sample_n.get(((t, i), kind, opt), 0)
        if n < scan.sample_cap:
            scan._sample_n[((t, i), kind, opt)] = n + 1
            scan.refs.setdefault((t, i), []).append(DbRef(kind, where, opt, loc))
    for k, n in fr["totals"].items():
        t, i = k.split(":")
        key = (t, int(i))
        scan.ref_total[key] = scan.ref_total.get(key, 0) + n
    for t, k, w, x in fr["dyn"]:
        scan.dynamic(t, k, w, x)


def _plugins(scan: DbScan, leaves: list, game: Path, enabled: dict) -> None:
    undeclared: dict = {}
    scan.cur = {"file": "js/plugins.js"}
    for leaf in leaves:
        if leaf.db_type:
            for tok in re.findall(r"\d+", leaf.value):
                scan.add(leaf.db_type, tok, "ref", f"Plugin {leaf.plugin}: {leaf.path.split('.', 1)[-1]}",
                         optional=not leaf.enabled)
        elif (leaf.enabled and not leaf.category and leaf.spec_type in ("", "string", "multiline_string", "note")
              and re.fullmatch(r"\s*\d{1,5}\s*", leaf.value)):
            undeclared.setdefault(leaf.plugin, []).append(leaf.path.split(".", 1)[-1])
    for plugin, params in sorted(undeclared.items()):
        shown = ", ".join(params[:4]) + ("..." if len(params) > 4 else "")
        scan.plugin_caveats.append(
            f"Plugin {plugin}: {shown} hold a number but are not declared as a database id. "
            f"If one is an item/switch/variable id, it will look unused. "
            f"(Map it in .asset_tools/plugin_params.json, e.g. \"{plugin}.{params[0]}\": \"switch\")")

    folder = game / "js" / "plugins"
    if folder.is_dir():
        for src in sorted(folder.glob("*.js")):
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")[:2_000_000]
            except OSError:
                continue
            scan.cur = {"file": f"js/plugins/{src.name}"}
            scan_script(text, scan, f"js/plugins/{src.name}",
                        optional=not enabled.get(src.stem, True))


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def classify(scan: DbScan, ann=None) -> list[DbRow]:
    rows: list[DbRow] = []
    for typ in TYPES:
        entries = scan.entries.get(typ, {})
        dyn = {k for (k, _w, _t) in scan.dynamic_uses.get(typ, [])}
        ids = set(entries) | {i for (t, i) in scan.kinds if t == typ}
        for ident in sorted(ids):
            name = entries.get(ident, "")
            refs = scan.refs.get((typ, ident), [])
            total = scan.ref_total.get((typ, ident), 0)
            kinds = scan.kinds.get((typ, ident), set())
            real = {k for (k, opt) in kinds if not opt}       # kinds of NON-optional references
            row = DbRow(typ, ident, name, USED, used_by=refs, total_refs=total)
            fixed = ENGINE_FIXED.get((typ, ident))
            kept = bool(ann) and row.key in ann.ignore
            in_db = ident in entries
            if fixed:
                row.status, row.note = ENGINE, fixed
            elif typ in ("switch", "variable"):
                _classify_flag(row, real, bool(kinds), dyn, name)
                if row.status == "skip":
                    continue
            else:
                if real:
                    row.status = USED
                    if not name and in_db:
                        row.status, row.note = BROKEN, "referenced, but this entry has no name (empty slot?)"
                    if not in_db:
                        row.status, row.note = BROKEN, "referenced, but this entry does not exist"
                elif kinds:
                    row.status, row.note = POSSIBLE, "only a disabled plugin refers to it"
                elif not name:
                    continue                       # an empty database slot: nothing to report
                elif "ref" in dyn:
                    row.status, row.note = POSSIBLE, "a script picks ids at run time"
                else:
                    row.status = UNUSED
            if row.status == POSSIBLE and not row.note:
                row.note = "see caveats"
            if kept and row.status in NEEDS_ATTENTION:
                row.status, row.note = KEPT, "flagged to keep"
            if row.status == UNUSED and not name:
                continue
            rows.append(row)
    return rows


def _classify_flag(row: DbRow, real: set, has_any: bool, dyn: set, name: str) -> None:
    has_set = bool(real & {"set", "ref"})
    has_read = bool(real & {"read", "ref"})
    if not has_any and not name:
        row.status = "skip"
        return
    if real:
        if has_set and has_read:
            row.status = USED
        elif has_set:
            row.status, row.note = WRITE_ONLY, "set by events but never checked"
            if "read" in dyn:
                row.status, row.note = POSSIBLE, "set, and a script reads switches/variables by computed id"
        else:
            row.status, row.note = READ_ONLY, "checked but never set (always off / 0)"
            if "set" in dyn:
                row.status, row.note = POSSIBLE, "checked, and a script sets by computed id"
    elif has_any:
        row.status, row.note = POSSIBLE, "only a disabled plugin refers to it"
    elif dyn:
        row.status, row.note = POSSIBLE, "a script uses computed ids"
    else:
        row.status = UNUSED


def summarize(rows: list[DbRow]) -> dict:
    """{type: {status: count}} for the summary line."""
    out: dict = {}
    for r in rows:
        out.setdefault(r.type, {}).setdefault(r.status, 0)
        out[r.type][r.status] += 1
    return out


def export_db_csv(rows: list[DbRow], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["Type", "ID", "Name", "Status", "Note", "References", "First references"])
        for r in rows:
            first = " | ".join(f"{x.kind}: {x.where}" for x in r.used_by[:5])
            wr.writerow([TYPE_LABELS[r.type], r.id, r.name, STATUS_TEXT.get(r.status, r.status),
                         r.note, r.total_refs, first])

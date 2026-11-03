"""
plugin_refs.py
--------------
Make plugin parameters and plugin commands REAL references.

Where the knowledge comes from
  1. The plugin's own header (js/plugins/<Name>.js). MZ plugins describe their
     parameters like this:
         @param Background   @type file   @dir img/pictures/
         @param Buttons      @type struct<Button>[]
         @command show  /  @arg pic   @type file  @dir img/pictures/
     so we know which parameters / command arguments hold asset names.
  2. Your own mapping  .asset_tools/plugin_params.json  for plugins that just
     say "@type string" (see add_user_mappings / the Health check "Plugin params" tab).

Plugin Manager stores structs and arrays as JSON *inside strings* (sometimes
several levels deep). Each leaf we find carries a `commit` function that writes
a new value back and re-encodes every enclosing level, so a rename can edit
exactly that one field without reformatting anything else.

Path syntax (used by the mapping file):
    parameter     "PluginName.Param"            nested: "PluginName.Param.field"
    command arg   "cmd:PluginName.command.arg"
Arrays add no segment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from annotations import tool_dir
from asset_paths import ASSET_ROOTS
from jsonio import detect_writer

USER_MAP_FILE = "plugin_params.json"

# Plugin parameter types (@type) that hold a database id (the value is a number in a string)
DB_TYPES = ("actor", "class", "skill", "item", "weapon", "armor", "enemy", "troop", "state",
            "animation", "common_event", "switch", "variable")


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

@dataclass
class ParamSpec:
    type: str = ""
    dir: str = ""


@dataclass
class PluginSchema:
    params: dict = field(default_factory=dict)     # plugin -> {param: ParamSpec}
    structs: dict = field(default_factory=dict)    # plugin -> {struct: {field: ParamSpec}}
    commands: dict = field(default_factory=dict)   # plugin -> {command: {arg: ParamSpec}}
    user: dict = field(default_factory=dict)       # path -> category
    plugins_with_header: set = field(default_factory=set)
    galv_layers: str | None = None      # Galv's Layer Graphics: None (absent) / "on" / "off" (disabled)


@dataclass
class Leaf:
    """One string value found inside plugin data."""
    plugin: str
    path: str                 # e.g. "GfxPlug.Buttons.icon"
    value: str
    commit: Callable[[str], None]
    category: str | None = None   # None = not (known to be) an asset name
    prefix: str = ""              # @dir below the category root: names are relative to it
    declared: bool = False        # came from a header or the user mapping
    enabled: bool = True
    db_type: str | None = None    # holds a database id of this type (item, switch, ...)
    spec_type: str = ""           # the @type the header gave (for "undeclared number" hints)


# --------------------------------------------------------------------------
# @dir -> (category, prefix)
# --------------------------------------------------------------------------

def category_from_dir(dir_value: str) -> tuple[str | None, str]:
    d = (dir_value or "").strip().replace("\\", "/").strip("/")
    best = (None, "")
    for cat, (folder, _exts) in ASSET_ROOTS.items():
        f = folder.as_posix()
        if d == f:
            return cat, ""
        if d.startswith(f + "/"):
            rest = d[len(f) + 1:] + "/"
            if best[0] is None or len(rest) < len(best[1]):
                best = (cat, rest)
    return best


# --------------------------------------------------------------------------
# header parsing
# --------------------------------------------------------------------------

_BLOCK_RE = re.compile(r"/\*(?:~struct~(\w+))?:(\w*)[ \t]*\r?\n(.*?)\*/", re.S)


def _tags(block: str) -> Iterator[tuple[str, str]]:
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("*"):
            line = line[1:].strip()
        if line.startswith("@"):
            tag, _, rest = line[1:].partition(" ")
            yield tag.strip(), rest.strip()


def parse_header(text: str):
    """Returns (params, structs, commands) for one plugin source file."""
    params: dict[str, ParamSpec] = {}
    structs: dict[str, dict[str, ParamSpec]] = {}
    commands: dict[str, dict[str, ParamSpec]] = {}
    seen_lang: dict[tuple, str] = {}

    for m in _BLOCK_RE.finditer(text):
        struct_name, lang, body = m.group(1), m.group(2), m.group(3)
        key = (struct_name or "")
        # same definitions repeat per language: keep the first, prefer the default one
        if key in seen_lang and (seen_lang[key] == "" or lang != ""):
            continue
        seen_lang[key] = lang
        target: dict[str, ParamSpec] = {}
        cmds: dict[str, dict[str, ParamSpec]] = {}
        cur: ParamSpec | None = None
        cur_cmd: str | None = None
        for tag, rest in _tags(body):
            if tag == "param" and rest:
                cur = target.setdefault(rest.split()[0], ParamSpec())
            elif tag == "command" and rest:
                cur_cmd = rest.split()[0]
                cmds.setdefault(cur_cmd, {})
                cur = None
            elif tag == "arg" and rest and cur_cmd:
                cur = cmds[cur_cmd].setdefault(rest.split()[0], ParamSpec())
            elif tag == "type" and cur is not None:
                cur.type = rest.strip()
            elif tag == "dir" and cur is not None:
                cur.dir = rest.strip()
        if struct_name:
            structs[struct_name] = target
        else:
            params, commands = target, cmds
    return params, structs, commands


# --------------------------------------------------------------------------
# schema + user mapping
# --------------------------------------------------------------------------

def user_map_path(game_folder: Path) -> Path:
    return tool_dir(game_folder) / USER_MAP_FILE


def load_user_map(game_folder: Path) -> dict[str, str]:
    p = Path(game_folder) / ".asset_tools" / USER_MAP_FILE
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        raw = data.get("params", {}) if isinstance(data, dict) else {}
        return {str(k): str(v) for k, v in raw.items() if str(v) in ASSET_ROOTS or str(v) in DB_TYPES}
    except Exception:
        return {}


def add_user_mappings(game_folder: Path, mappings: dict[str, str]) -> Path:
    """Remember 'this plugin parameter holds a <category> name'."""
    bad = [c for c in mappings.values() if c not in ASSET_ROOTS and c not in DB_TYPES]
    if bad:
        raise ValueError(f"unknown category: {bad[0]}")
    p = user_map_path(game_folder)
    current = load_user_map(game_folder)
    current.update(mappings)
    p.write_text(json.dumps({"format": 1, "params": dict(sorted(current.items()))},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


_TEXTS: dict = {}          # path -> ((size, mtime_ns), text): plugin sources already read in this run


def plugin_texts(game_folder: Path) -> dict[str, str]:
    """{plugin name: source text} for js/plugins/*.js.

    A project can have 150+ plugin files and every scan needs them several times (reference scan,
    database, maps, plugin files ...). Opening a file costs milliseconds on Windows (antivirus), so the
    files are read by several threads at once, and a file is not read again while its size and
    modification time are unchanged.
    """
    folder = Path(game_folder) / "js" / "plugins"
    out: dict[str, str] = {}
    if not folder.is_dir():
        return out
    todo = []
    for p in sorted(folder.glob("*.js")):
        try:
            st = p.stat()
        except OSError:
            continue
        sig = (st.st_size, st.st_mtime_ns)
        hit = _TEXTS.get(str(p))
        if hit and hit[0] == sig:
            out[p.stem] = hit[1]
        else:
            todo.append((p, sig))

    def read(item):
        try:
            return item[0].read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    if todo:
        from parallel import threaded_map
        for (p, sig), text in threaded_map(read, todo, 16).items():
            if text is not None:
                _TEXTS[str(p)] = (sig, text)
                out[p.stem] = text
    return dict(sorted(out.items()))


def load_schema(game_folder: Path) -> PluginSchema:
    game = Path(game_folder)
    schema = PluginSchema(user=load_user_map(game))
    folder = game / "js" / "plugins"
    if folder.is_dir():
        for stem, text in plugin_texts(game).items():
            params, structs, commands = parse_header(text)
            if params or structs or commands:
                schema.params[stem] = params
                schema.structs[stem] = structs
                schema.commands[stem] = commands
                schema.plugins_with_header.add(stem)
    schema.galv_layers = detect_galv_layers(game)
    return schema


def detect_galv_layers(game: Path) -> str | None:
    """Is Galv's Layer Graphics installed, and is it enabled in the Plugin Manager?"""
    game = Path(game)
    key = "layergraphics"
    in_folder = any(key in p.stem.lower() for p in (game / "js" / "plugins").glob("*.js")) \
        if (game / "js" / "plugins").is_dir() else False
    try:
        pf = read_plugins_js(game)
    except (ValueError, OSError):
        return "on" if in_folder else None          # cannot tell: assume it is active
    listed = [p for p in (pf.data if pf else []) if isinstance(p, dict)
              and key in str(p.get("name", "")).lower()]
    if listed:
        return "on" if any(bool(p.get("status", True)) for p in listed) else "off"
    return "off" if in_folder else None


# --------------------------------------------------------------------------
# walking values
# --------------------------------------------------------------------------

def _loads(value: str, kinds):
    if not value or value.lstrip()[:1] not in "{[":
        return None
    try:
        obj = json.loads(value)
    except ValueError:
        return None
    return obj if isinstance(obj, kinds) else None


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def walk_value(value: str, spec: ParamSpec | None, plugin: str, key_prefix: str,
               path: list[str], commit: Callable[[str], None], schema: PluginSchema,
               struct: str | None = None, enabled: bool = True) -> Iterator[Leaf]:
    if not isinstance(value, str) or not value.strip():
        return
    pathstr = key_prefix + "." + ".".join(path)
    t = (spec.type if spec else "").strip()

    def leaf(category=None, prefix="", declared=False, db_type=None) -> Leaf:
        return Leaf(plugin, pathstr, value, commit, category, prefix, declared, enabled,
                    db_type, t)

    user_cat = schema.user.get(pathstr)
    if user_cat in DB_TYPES:
        yield leaf(declared=True, db_type=user_cat)
        return
    if user_cat:
        yield leaf(user_cat, "", True)
        return
    if t in DB_TYPES:
        yield leaf(declared=True, db_type=t)
        return
    if t == "file":
        cat, prefix = category_from_dir(spec.dir)
        yield leaf(cat, prefix, cat is not None)
        return

    sname = t[7:-1] if t.startswith("struct<") and t.endswith(">") else None
    if t.endswith("[]"):
        arr = _loads(value, list)
        if arr is not None:
            base = ParamSpec(t[:-2], spec.dir if spec else "")
            yield from _walk_json(arr, lambda: commit(_dumps(arr)), base, plugin, key_prefix,
                                  path, schema, enabled)
            return
    elif sname is not None:
        obj = _loads(value, dict)
        if obj is not None:
            yield from _walk_json(obj, lambda: commit(_dumps(obj)), None, plugin, key_prefix,
                                  path, schema, enabled, struct=sname)
            return
    elif t in ("", "string", "multiline_string", "note"):
        obj = _loads(value, (dict, list))      # untyped: it may still be encoded JSON
        if obj is not None:
            yield from _walk_json(obj, lambda: commit(_dumps(obj)), None, plugin, key_prefix,
                                  path, schema, enabled)
            return
    yield leaf()


def _walk_json(obj, recommit: Callable[[], None], elem_spec: ParamSpec | None, plugin: str,
               key_prefix: str, path: list[str], schema: PluginSchema,
               enabled: bool, struct: str | None = None) -> Iterator[Leaf]:
    if isinstance(obj, dict):
        fields = schema.structs.get(plugin, {}).get(struct or "", {})
        for k in list(obj):
            v = obj[k]
            spec = fields.get(k)

            def commit(new, d=obj, kk=k):
                d[kk] = new
                recommit()
            if isinstance(v, str):
                yield from walk_value(v, spec, plugin, key_prefix, path + [k], commit, schema,
                                      enabled=enabled)
            elif isinstance(v, (dict, list)):
                yield from _walk_json(v, recommit, spec, plugin, key_prefix, path + [k],
                                      schema, enabled)
    elif isinstance(obj, list):
        base = elem_spec
        sname = None
        if base and base.type.startswith("struct<") and base.type.endswith(">"):
            sname = base.type[7:-1]
        for i in range(len(obj)):
            v = obj[i]

            def commit(new, a=obj, ii=i):
                a[ii] = new
                recommit()
            if isinstance(v, str):
                yield from walk_value(v, base, plugin, key_prefix, path, commit, schema,
                                      struct=sname, enabled=enabled)
            elif isinstance(v, (dict, list)):
                yield from _walk_json(v, recommit, base, plugin, key_prefix, path, schema,
                                      enabled, struct=sname)


def command_leaves(plugin: str, command: str, args: dict, schema: PluginSchema) -> Iterator[Leaf]:
    """Leaves of one Plugin Command (event command 357): args = parameters[3]."""
    specs = schema.commands.get(plugin, {}).get(command, {})
    for name in list(args):
        val = args[name]
        if isinstance(val, str):
            yield from walk_value(
                val, specs.get(name), plugin, f"cmd:{plugin}.{command}", [name],
                lambda new, d=args, k=name: d.__setitem__(k, new), schema)


# --------------------------------------------------------------------------
# js/plugins.js
# --------------------------------------------------------------------------

@dataclass
class PluginsFile:
    path: Path
    data: list                       # parsed array of plugin entries
    render: Callable[[list], str]    # data -> full file text, original layout preserved


def read_plugins_js(game_folder: Path) -> PluginsFile | None:
    game = Path(game_folder)
    for p in (game / "js" / "plugins.js",):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8-sig")
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("no plugin list ([ ... ]) found")
        body = text[start:end + 1]
        data = json.loads(body)             # raises on a broken file: caller decides
        if not isinstance(data, list):
            raise ValueError("the plugin list is not an array")
        writer = detect_writer(body, data)
        return PluginsFile(p, data, lambda arr, w=writer: text[:start] + w(arr) + text[end + 1:])
    return None


def plugins_js_leaves(data: list, schema: PluginSchema) -> Iterator[Leaf]:
    for plug in data:
        if not isinstance(plug, dict) or not isinstance(plug.get("parameters"), dict):
            continue
        name = str(plug.get("name", ""))
        enabled = bool(plug.get("status", True))
        params = plug["parameters"]
        specs = schema.params.get(name, {})
        for key in list(params):
            if isinstance(params[key], str):
                yield from walk_value(
                    params[key], specs.get(key), name, name, [key],
                    lambda new, d=params, k=key: d.__setitem__(k, new), schema,
                    enabled=enabled)


# --------------------------------------------------------------------------
# suggestions: undeclared parameters whose value IS an existing asset name
# --------------------------------------------------------------------------

@dataclass
class ParamSuggestion:
    path: str
    plugin: str
    values: list
    categories: list            # categories that fit ALL matching values
    @property
    def category(self) -> str | None:
        return self.categories[0] if len(self.categories) == 1 else None


def suggest_mappings(candidates: dict[str, set], disk_names: dict[str, set]) -> list[ParamSuggestion]:
    """
    candidates: path -> values seen in undeclared plugin parameters / command args
    disk_names: asset name -> set of categories that have such a file
    """
    out = []
    for path, values in sorted(candidates.items()):
        matched = [v for v in values if v in disk_names]
        if not matched:
            continue
        cats = set.intersection(*(disk_names[v] for v in matched))
        plugin = path[4:].split(".", 1)[0] if path.startswith("cmd:") else path.split(".", 1)[0]
        out.append(ParamSuggestion(path, plugin, sorted(matched), sorted(cats)))
    return out

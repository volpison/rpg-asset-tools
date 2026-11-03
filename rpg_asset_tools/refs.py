"""
refs.py
-------
THE walker. One generator that finds every asset reference inside a parsed
data/*.json document. Both the scanner (read) and the rename engine (write)
use it, so what the tool *sees* and what it *rewrites* can never disagree.

Each Ref carries a setter, so renaming is simply `ref.set(new_name)`.

Besides hard references, the walker also yields Mentions: free text that might
use an asset by name (notes, script calls, plugin commands, comments). Those
never create "missing" errors; they only protect files from being called
orphans.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator

from asset_paths import AUDIO_CATEGORIES

# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------


@dataclass
class Ref:
    name: str
    categories: tuple          # ANY-of: the file may live in one of these folders
    file: str
    context: str = ""
    event: int | None = None
    page: int | None = None
    command: int | None = None
    code: int | None = None
    setter: Callable[[str], None] | None = field(default=None, repr=False)
    prefix: str = ""          # plugin @dir below the category root: stored value is relative to it
    optional: bool = False    # e.g. a DISABLED plugin: may reference a file, never "missing"
    forbid: str = ""          # characters the stored text cannot contain (space in a map note ...)

    def check_name(self, new_name: str) -> str | None:
        """Why `new_name` cannot be written here (None = fine)."""
        bad = [c for c in self.forbid if c in new_name]
        if bad:
            shown = "space" if bad[0] == " " else repr(bad[0])
            return f"used where a {shown} is not allowed in the name ({self.context})"
        if self.prefix and not new_name.startswith(self.prefix):
            return (f"used by a plugin parameter that is relative to '{self.prefix}': "
                    f"the new name must start with '{self.prefix}'")
        return None

    def set(self, new_name: str) -> None:
        problem = self.check_name(new_name)
        if problem:
            raise ValueError(f"'{new_name}': {problem}")
        if self.prefix:
            if not new_name.startswith(self.prefix):
                raise ValueError(f"'{new_name}' must stay inside '{self.prefix}' "
                                 f"(the plugin parameter is relative to that folder)")
            new_name = new_name[len(self.prefix):]
        self.setter(new_name)

    def location(self) -> dict:
        """Plain dict (for the UI and reports)."""
        return {
            "file": self.file, "event": self.event, "page": self.page,
            "command": self.command, "code": self.code,
            "context": self.context, "category": self.categories[0],
        }


@dataclass
class Mention:
    text: str
    kind: str   # note | script | plugin | comment | effect | text
    file: str = ""
    path: str = ""   # plugin parameter / command-arg path, when known (for mapping suggestions)
    event: int | None = None     # where the text is (for "where is this used?")
    page: int | None = None
    command: int | None = None


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

# JSON key -> categories (any-of). battlerName depends on the file (below).
KEY_CATEGORIES = {
    "faceName": ("face",),
    "characterName": ("character",),
    "parallaxName": ("parallax",),
    "battleback1Name": ("battleback1",),
    "battleback2Name": ("battleback2",),
    "title1Name": ("title1",),
    "title2Name": ("title2",),
    "animation1Name": ("animation",),   # MV-style, harmless in MZ
    "animation2Name": ("animation",),
    "effectName": ("effect",),          # MZ Animations.json
}

BATTLER_BY_FILE = {
    "actors.json": ("sv_actor",),
    "enemies.json": ("enemy", "sv_enemy"),
    "system.json": ("enemy", "sv_enemy"),
}
BATTLER_DEFAULT = ("sv_actor", "enemy", "sv_enemy")

# Event command codes that are consumed explicitly (never walked generically)
CODE_LABELS = {
    101: "Show Text", 231: "Show Picture",
    241: "Play BGM", 245: "Play BGS", 249: "Play ME", 250: "Play SE",
    132: "Change Battle BGM", 133: "Change Victory ME", 139: "Change Defeat ME",
    140: "Change Vehicle BGM", 322: "Change Actor Images",
    323: "Change Vehicle Image", 283: "Change Battle Back",
    284: "Change Parallax", 261: "Play Movie", 205: "Set Movement Route",
}
# Plain dialogue text: not worth indexing as mentions
TEXT_CODES = {101, 401, 105, 405, 102, 402, 403}
# Mention kinds for the "other" command codes
MENTION_KIND_BY_CODE = {
    355: "script", 655: "script", 356: "plugin", 357: "plugin", 657: "plugin",
    108: "comment", 408: "comment",
}

_AUDIO_KEYS = ("name", "volume", "pitch")


def audio_categories_for_key(key: str | None) -> tuple:
    """Guess bgm/bgs/me/se from the JSON key that holds an audio object."""
    k = key or ""
    kl = k.lower()
    if kl == "bgm" or k.endswith("Bgm"):
        return ("bgm",)
    if kl == "bgs" or k.endswith("Bgs"):
        return ("bgs",)
    if kl == "me" or k.endswith("Me"):
        return ("me",)
    if kl in ("se", "sound", "sounds") or k.endswith("Se"):
        return ("se",)
    return AUDIO_CATEGORIES      # unknown: any audio folder is acceptable


def _is_audio_obj(obj: dict) -> bool:
    return isinstance(obj.get("name"), str) and all(k in obj for k in _AUDIO_KEYS)


def _setter(container, key) -> Callable[[str], None]:
    def _set(value, c=container, k=key):
        c[k] = value
    return _set


def _clean(name) -> str | None:
    if not isinstance(name, str):
        return None
    name = name.strip().replace("\\", "/")
    if not name or name in (".", "None", "null"):
        return None
    return name


# --------------------------------------------------------------------------
# The walker
# --------------------------------------------------------------------------


# ---- Galv's Layer Graphics (GALV_LayerGraphicsMZ.js) -------------------------
# Map note lines:   LAYER id graphic ...   /   LAYER_S id graphic ...
# The plugin does:  config = (mapId + line.replace('LAYER','')).split(" ")  -> graphic = config[2]
# We repeat that EXACTLY (first occurrence, single-space split), so we see what the plugin sees.
_NOTE_LINE = re.compile(r"[^\r\n]+")


def _galv_line_graphic(line: str):
    """[(keyword, graphic, keyword_pos, graphic_offset_in_rest)] for one note line."""
    out = []
    for key in ("LAYER ", "LAYER_S "):
        if key in line:
            tok = key.strip()
            p = line.find(tok)
            rest = line[:p] + line[p + len(tok):]
            parts = rest.split(" ")
            if len(parts) > 2 and parts[2].strip():
                out.append((tok, parts[2], p, sum(len(x) + 1 for x in parts[:2])))
    return out


def galv_note_layers(note: str) -> list:
    """[(line_index, line_number, keyword, graphic)] for every layer the plugin would create."""
    res = []
    for i, m in enumerate(_NOTE_LINE.finditer(note)):
        for tok, g, _p, _o in _galv_line_graphic(m.group()):
            res.append((i, i + 1, tok, g))
    return res


def _note_setter(container: dict, line_index: int, tok: str):
    def set_(new: str):
        note = container["note"]
        lines = list(_NOTE_LINE.finditer(note))
        m = lines[line_index]
        line = m.group()
        for t, _g, p, off in _galv_line_graphic(line):
            if t != tok:
                continue
            if off < p:
                raise ValueError("this note line cannot be rewritten safely; edit it by hand")
            rest = line[:p] + line[p + len(tok):]
            parts = rest.split(" ")
            parts[2] = new
            new_rest = " ".join(parts)
            new_line = new_rest[:p] + tok + new_rest[p:]
            container["note"] = note[:m.start()] + new_line + note[m.end():]
            return
        raise ValueError("the note changed since it was scanned")
    return set_


# ---- script calls that name an asset with a literal ----------------------------
SCRIPT_ASSET_CALLS = (
    (re.compile(r"""Galv\.LG\.bLayer\s*\(\s*[^,()]+,\s*(['"])([^'"\r\n]*)\1"""), "layer",
     "Galv.LG.bLayer"),
    (re.compile(r"""loadLayerGraphic\s*\(\s*(['"])([^'"\r\n]+)\1"""), "layer", "loadLayerGraphic"),
)


def _script_setter(P: list, n: int, rx):
    def set_(new: str):
        text = P[0]
        ms = list(rx.finditer(text))
        m = ms[n]
        P[0] = text[:m.start(2)] + new + text[m.end(2):]
    return set_


class _Walker:
    def __init__(self, file_name: str, plugins=None):
        self.file = file_name
        self.fl = file_name.lower()
        self.plugins = plugins          # plugin_refs.PluginSchema | None

    # ---- helpers ---------------------------------------------------------
    def _ref(self, container, key, cats, loc, context="", code=None, command=None):
        raw = container[key]
        name = _clean(raw)
        if name is None:
            return None
        return Ref(
            name=name, categories=tuple(cats), file=self.file, context=context,
            event=loc.get("event"), page=loc.get("page"),
            command=command if command is not None else loc.get("command"),
            code=code, setter=_setter(container, key),
        )

    def _audio(self, obj, cats, loc, context, code=None, command=None):
        if isinstance(obj, dict) and "name" in obj:
            r = self._ref(obj, "name", cats, loc, context, code, command)
            if r:
                yield r

    # ---- event commands --------------------------------------------------
    def commands(self, cmds, loc, route=False) -> Iterator:
        for idx, cmd in enumerate(cmds):
            if not isinstance(cmd, dict):
                continue
            code = cmd.get("code")
            params = cmd.get("parameters")
            if not isinstance(code, int) or not isinstance(params, list):
                continue
            yield from self.command(cmd, code, params, loc, idx, route)

    def command(self, cmd, code, P, loc, idx, route=False) -> Iterator:
        def pref(i, cats, ctx=None):
            if len(P) > i and isinstance(P[i], str):
                r = self._ref(P, i, cats, loc, ctx or CODE_LABELS.get(code, f"code {code}"), code, idx)
                if r:
                    yield r

        def paudio(i, cats):
            if len(P) > i:
                if isinstance(P[i], dict):
                    yield from self._audio(P[i], cats, loc, CODE_LABELS.get(code, f"code {code}"), code, idx)
                elif isinstance(P[i], str):
                    yield from pref(i, cats)

        if route:
            # Movement-route sub-commands
            if code == 41:
                yield from pref(0, ("character",), "Route: Change Image")
            elif code == 44:
                yield from self._audio(P[0], ("se",), loc, "Route: Play SE", code, idx) if P and isinstance(P[0], dict) else ()
            elif code == 45 and P and isinstance(P[0], str):
                yield from self.script_refs(P, loc, idx, 45)
                yield Mention(P[0], "script", self.file, event=loc.get("event"), page=loc.get("page"), command=idx)
            return

        if code == 101:
            yield from pref(0, ("face",))
        elif code == 231:
            yield from pref(1, ("picture",))
        elif code == 241:
            yield from paudio(0, ("bgm",))
        elif code == 245:
            yield from paudio(0, ("bgs",))
        elif code == 249:
            yield from paudio(0, ("me",))
        elif code == 250:
            yield from paudio(0, ("se",))
        elif code == 132:
            yield from paudio(0, ("bgm",))
        elif code in (133, 139):
            yield from paudio(0, ("me",))
        elif code == 140:
            yield from paudio(1, ("bgm",))
        elif code == 322:
            yield from pref(1, ("character",))
            yield from pref(3, ("face",))
            yield from pref(5, ("sv_actor",))
        elif code == 323:
            yield from pref(1, ("character",))
        elif code == 283:
            yield from pref(0, ("battleback1",))
            yield from pref(1, ("battleback2",))
        elif code == 284:
            yield from pref(0, ("parallax",))
        elif code == 261:
            yield from pref(0, ("movie",))
        elif code == 205:
            if len(P) > 1 and isinstance(P[1], dict) and isinstance(P[1].get("list"), list):
                yield from self.commands(P[1]["list"], loc, route=True)
        elif code == 505:
            if P and isinstance(P[0], dict):
                rc, rp = P[0].get("code"), P[0].get("parameters")
                if isinstance(rc, int) and isinstance(rp, list):
                    yield from self.command(P[0], rc, rp, loc, idx, route=True)
        elif code == 357 and self.plugins is not None and len(P) > 3 and isinstance(P[3], dict):
            yield from self.plugin_command(P, loc, idx)
        elif code in (355, 655) and P and isinstance(P[0], str):
            yield from self.script_refs(P, loc, idx, code)
            yield from self.mentions(P, MENTION_KIND_BY_CODE.get(code, "script"), loc, idx)
        elif code not in TEXT_CODES:
            kind = MENTION_KIND_BY_CODE.get(code, "plugin")
            yield from self.mentions(P, kind, loc, idx)

    def script_refs(self, P, loc, idx, code) -> Iterator:
        """Literal asset names inside script calls (e.g. Galv.LG.bLayer(1,'fog',...))."""
        text = P[0]
        if "bLayer" not in text and "loadLayerGraphic" not in text:
            return
        for rx, cat, label in SCRIPT_ASSET_CALLS:
            for n, m in enumerate(rx.finditer(text)):
                name = _clean(m.group(2))
                if name:
                    q = m.group(1)
                    yield Ref(name=name, categories=(cat,), file=self.file,
                              context=f"script: {label}", event=loc.get("event"),
                              page=loc.get("page"), command=idx, code=code,
                              setter=_script_setter(P, n, rx), forbid=q + "\\")

    def galv_note_refs(self, obj: dict, note: str) -> Iterator:
        state = getattr(self.plugins, "galv_layers", None)
        if not state or not re.fullmatch(r"map\d+\.json", self.fl):
            return
        for line_index, line_no, tok, graphic in galv_note_layers(note):
            name = _clean(graphic)
            if name:
                yield Ref(name=name, categories=("layer",), file=self.file,
                          context=f"map note, line {line_no} ({tok})" + ("" if state == "on" else " - plugin disabled"),
                          setter=_note_setter(obj, line_index, tok), forbid=" ",
                          optional=(state != "on"))

    def plugin_command(self, P, loc, idx) -> Iterator:
        """Plugin Command (MZ): [plugin, command, display text, {arg: value}]."""
        from plugin_refs import command_leaves
        for text in P[:3]:
            if isinstance(text, str) and text.strip():
                yield Mention(text, "plugin", self.file, event=loc.get("event"), page=loc.get("page"), command=idx)
        for leaf in command_leaves(str(P[0]), str(P[1]), P[3], self.plugins):
            if leaf.category:
                name = _clean(leaf.prefix + leaf.value)
                if name:
                    yield Ref(name=name, categories=(leaf.category,), file=self.file,
                              context=f"Plugin command {leaf.plugin}.{P[1]}: {leaf.path.rsplit('.', 1)[-1]}",
                              event=loc.get("event"), page=loc.get("page"), command=idx,
                              code=357, setter=leaf.commit, prefix=leaf.prefix)
            elif not leaf.db_type:
                yield Mention(leaf.value, "plugin", self.file, path=leaf.path,
                                  event=loc.get("event"), page=loc.get("page"), command=idx)

    def mentions(self, obj, kind, loc=None, idx=None) -> Iterator[Mention]:
        loc = loc or {}
        if isinstance(obj, str):
            if obj.strip():
                yield Mention(obj, kind, self.file, event=loc.get("event"),
                              page=loc.get("page"), command=idx)
        elif isinstance(obj, list):
            for x in obj:
                yield from self.mentions(x, kind, loc, idx)
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from self.mentions(v, kind, loc, idx)

    # ---- generic tree ----------------------------------------------------
    def node(self, obj, key, loc) -> Iterator:
        if isinstance(obj, dict):
            yield from self.dict_node(obj, key, loc)
        elif isinstance(obj, list):
            if not obj or isinstance(obj[0], (int, float)):
                return                       # tile data etc: skip fast
            if key == "tilesetNames":
                for i, n in enumerate(obj):
                    if isinstance(n, str):
                        r = self._ref(obj, i, ("tileset",), loc, f"tilesetNames[{i}]")
                        if r:
                            yield r
                return
            for i, item in enumerate(obj):
                child = loc
                if key == "pages":
                    child = {**loc, "page": i}
                yield from self.node(item, key, child)

    def dict_node(self, obj: dict, key, loc) -> Iterator:
        # Event / troop / common event identity (for readable locations)
        if isinstance(obj.get("id"), int) and ("pages" in obj or "list" in obj):
            loc = {**loc, "event": obj["id"]}

        if _is_audio_obj(obj):
            yield from self._audio(obj, audio_categories_for_key(key), loc, f"audio: {key}")
            return

        # A command list directly inside a dict ("list": [ {code,...} ])
        cmd_list = obj.get("list")
        handled_list = isinstance(cmd_list, list) and any(
            isinstance(c, dict) and isinstance(c.get("code"), int) for c in cmd_list
        )

        for k, v in obj.items():
            if k == "list" and handled_list:
                yield from self.commands(v, loc)
            elif k in KEY_CATEGORIES and isinstance(v, str):
                r = self._ref(obj, k, KEY_CATEGORIES[k], loc, k)
                if r:
                    yield r
            elif k == "battlerName" and isinstance(v, str):
                cats = BATTLER_BY_FILE.get(self.fl, BATTLER_DEFAULT)
                r = self._ref(obj, k, cats, loc, k)
                if r:
                    yield r
            elif k == "note" and isinstance(v, str):
                if v.strip():
                    yield Mention(v, "note", self.file, event=loc.get("event"))
                    if loc.get("event") is None:
                        yield from self.galv_note_refs(obj, v)
            elif isinstance(v, (dict, list)):
                yield from self.node(v, k, loc)


def walk_refs(data, file_name: str, plugins=None) -> Iterator:
    """Yield Ref and Mention objects for one parsed JSON document."""
    w = _Walker(file_name, plugins)
    yield from w.node(data, None, {})


# --------------------------------------------------------------------------
# Mention index (does any free text use this name?)
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""[^\s"'`<>()\[\]{},;:=\\|!?*]+""")
_STRIP_EXT = re.compile(r"\.(png|ogg|m4a|wav|mp3|webm|mp4|efkefc)$", re.I)


@functools.lru_cache(maxsize=200_000)
def _token_variants(tok: str) -> tuple:
    out = [tok]
    stripped = _STRIP_EXT.sub("", tok)
    out.append(stripped)
    if "/" in stripped:
        out.append(stripped.rsplit("/", 1)[-1])
        # img/pictures/Foo/Bar -> Foo/Bar (folder-relative)
        parts = stripped.split("/")
        for i in range(1, len(parts) - 1):
            out.append("/".join(parts[i:]))
    return tuple(out)


class MentionIndex:
    def __init__(self):
        self.tokens: dict[str, set[str]] = {}
        self._blob_parts: dict[str, list[str]] = {}
        self._blob: dict[str, str] = {}
        self.places: dict[str, dict] = {}     # token -> {(kind, file, event, page, command)}
        self._text_variants: dict[str, tuple] = {}

    MAX_PLACES = 40

    def add(self, text: str, kind: str, where: tuple | None = None) -> None:
        """`where` = (file, event, page, command), kept so the UI can say WHERE a name is mentioned."""
        if not text:
            return
        self._blob_parts.setdefault(kind, []).append(text)
        self._blob.pop(kind, None)
        place = (kind,) + tuple(where) if where else None
        variants = self._text_variants.get(text)
        if variants is None:
            seen: dict = {}
            for tok in _TOKEN_RE.findall(text):
                for v in _token_variants(tok):
                    seen[v] = None
            variants = tuple(seen)
            if len(text) < 400:                 # repeated short lines (script calls) are very common
                self._text_variants[text] = variants
        for variant in variants:
            self.tokens.setdefault(variant, set()).add(kind)
            if place:
                bucket = self.places.setdefault(variant, set())
                if len(bucket) < self.MAX_PLACES:
                    bucket.add(place)

    def find_places(self, name: str) -> list:
        """[(kind, file, event, page, command)] where free text mentions `name` (token match only)."""
        return sorted(self.places.get(name, ()), key=lambda p: tuple("" if x is None else str(x) for x in p))

    @staticmethod
    def _variants(tok: str):
        return _token_variants(tok)

    def find(self, name: str) -> set[str]:
        """Kinds of free text that mention `name` (empty set = none)."""
        kinds = set(self.tokens.get(name, ()))
        if kinds:
            return kinds
        if _TOKEN_RE.fullmatch(name):
            return kinds                    # token lookup is authoritative
        # names with spaces / odd characters: substring search
        for kind, parts in self._blob_parts.items():
            blob = self._blob.get(kind)
            if blob is None:
                blob = self._blob[kind] = "\n".join(parts)
            if name in blob:
                kinds.add(kind)
        return kinds

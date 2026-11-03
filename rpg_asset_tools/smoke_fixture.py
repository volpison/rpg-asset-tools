"""Builds a tiny fake RPG Maker MZ project (used by smoke_test_qt.py and the unit tests)."""
import json
import struct
import zlib
from pathlib import Path


def _png(tag: str = "", w: int = 1, h: int = 1) -> bytes:
    """A tiny PNG whose pixel colour depends on `tag` (same tag = identical bytes)."""
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    px = struct.pack(">I", zlib.crc32(tag.encode()) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + px))
            + chunk(b"IEND", b""))


def _mz_list(items):
    """MZ writes top-level arrays one element per line, elements compact."""
    c = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    return "[\n" + ",\n".join(c(i) for i in items) + "\n]"


def build(root: Path) -> Path:
    root = Path(root)
    for d in ("data", "js", "js/plugins", "effects", "movies", "audio/se", "audio/bgm", "audio/me",
              "img/pictures", "img/faces", "img/characters", "img/enemies",
              "img/sv_enemies", "img/sv_actors", "img/tilesets", "img/animations",
              "img/system", "img/pictures/sub", "img/pictures/ui"):
        (root / d).mkdir(parents=True, exist_ok=True)

    for rel in ("pictures/Pic1", "pictures/Lonely", "pictures/Mentioned", "pictures/sub/Deep",
                "faces/Actor1", "characters/Actor1", "characters/Hero2",
                "enemies/Slime", "sv_enemies/Slime", "tilesets/Town",
                "animations/Fire", "animations/Spare", "system/Window", "system/Custom",
                "pictures/Pic1Copy", "pictures/Plugged", "pictures/ui/ico", "pictures/PlugBtn",
                "pictures/PlugCmd", "pictures/OffPic", "pictures/Unmapped",
                "pictures/LooseSprite"):
        # Pic1Copy is a byte-identical copy of Pic1 (duplicate-finder fixture)
        tag = "pictures/Pic1" if rel == "pictures/Pic1Copy" else rel
        (root / "img" / (rel + ".png")).write_bytes(_png(tag))
    for rel in ("se/Hit", "se/Boom", "se/Step", "bgm/Theme", "bgm/Unused", "me/Win",
                "se/HitCopy", "se/PlugSe"):
        tag = "se/Hit" if rel == "se/HitCopy" else rel      # HitCopy = duplicate of Hit
        (root / "audio" / (rel + ".ogg")).write_bytes(b"ogg:" + tag.encode())
        (root / "audio" / (rel + ".m4a")).write_bytes(b"m4a:" + tag.encode())
    (root / "effects" / "Fire.efkefc").write_bytes(
        b"EFKE\x00" + "../img/animations/Fire.png".encode("utf-16le"))
    (root / "movies" / "Intro.webm").write_bytes(b"x")

    def audio(name):
        return {"name": name, "pan": 0, "pitch": 100, "volume": 90}

    (root / "data/Enemies.json").write_text(
        _mz_list([None, {"id": 1, "battlerName": "Slime", "name": "Slime", "note": ""}]) + "\n")
    (root / "data/Tilesets.json").write_text(
        _mz_list([None, {"id": 1, "tilesetNames": ["Town", "", "", "", "", "", "", "", ""]}]) + "\n")
    (root / "data/Animations.json").write_text(_mz_list([None, {
        "id": 1, "effectName": "Fire",
        "soundTimings": [{"frame": 1, "se": audio("Boom")}]}]) + "\n")
    (root / "data/Actors.json").write_text(_mz_list([None, {
        "id": 1, "characterName": "Actor1", "faceName": "Actor1", "battlerName": "",
        "note": "<picture: Mentioned>"}]) + "\n")
    (root / "data/System.json").write_text(json.dumps({
        "sounds": [audio("Hit")], "titleBgm": audio("Theme"),
        "victoryMe": audio("Win"), "battlerName": "",
        "title1Name": "", "title2Name": ""}, separators=(",", ":")) + "\n")

    cmds = [
        {"code": 322, "indent": 0, "parameters": [1, "Hero2", 0, "Actor1", 0, ""]},
        {"code": 205, "indent": 0, "parameters": [-1, {"list": [
            {"code": 44, "parameters": [audio("Step")]},
            {"code": 41, "parameters": ["Hero2", 0]}, {"code": 0}],
            "repeat": False, "skippable": False, "wait": True}]},
        {"code": 231, "indent": 0, "parameters": [1, "Pic1", 0, 0, 0, 0, 100, 100, 255, 0]},
        {"code": 231, "indent": 0, "parameters": [2, "sub/Deep", 0, 0, 0, 0, 100, 100, 255, 0]},
        {"code": 231, "indent": 0, "parameters": [3, "Pic1Copy", 0, 0, 0, 0, 100, 100, 255, 0]},
        {"code": 357, "indent": 0, "parameters": ["GfxPlug", "show", "Show", {"pic": "PlugCmd", "note": "hello"}]},
        {"code": 357, "indent": 0, "parameters": ["Loose", "go", "", {"img": "LooseSprite"}]},
        {"code": 261, "indent": 0, "parameters": ["Intro"]},
        {"code": 132, "indent": 0, "parameters": [audio("Theme")]},
        {"code": 357, "indent": 0, "parameters": ["Plug", "cmd", "", {"img": "Lonely2"}]},
        {"code": 0, "indent": 0, "parameters": []},
    ]
    m = {"bgm": audio(""), "bgs": audio(""), "parallaxName": "", "battleback1Name": "",
         "battleback2Name": "", "data": [0, 0, 0],
         "events": [None, {"id": 1, "name": "EV", "note": "", "pages": [
             {"image": {"characterName": "", "tileId": 0}, "list": cmds}]}]}
    c = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    text = "{" + ",".join(f'"{k}":' + (_mz_list(v) if k == "events" else c(v))
                           for k, v in m.items()) + "}\n"
    (root / "data/Map001.json").write_text(text)

    (root / "js/plugins/GfxPlug.js").write_text("""//=============================================================================
// GfxPlug.js
//=============================================================================
/*:
 * @target MZ
 * @plugindesc Test plugin with asset parameters
 * @param BgPic
 * @text Background
 * @type file
 * @dir img/pictures/
 * @param UiIcon
 * @type file
 * @dir img/pictures/ui/
 * @param Buttons
 * @type struct<Btn>[]
 * @param Banner
 * @type string
 * @param Mode
 * @type select
 * @option fast
 * @command show
 * @text Show
 * @arg pic
 * @type file
 * @dir img/pictures/
 * @arg note
 * @type string
 */
/*:ja
 * @param BgPic
 * @text haikei
 */
/*~struct~Btn:
 * @param icon
 * @type file
 * @dir img/pictures/
 * @param snd
 * @type file
 * @dir audio/se/
 */
(() => {})();
""")
    (root / "js/plugins/OffPlug.js").write_text("""/*:
 * @param Pic
 * @type file
 * @dir img/pictures/
 */
""")
    c = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    buttons = c([c({"icon": "PlugBtn", "snd": "PlugSe"})])        # JSON inside JSON, like Plugin Manager
    plugins = [
        {"name": "X", "status": True, "description": "d",
         "parameters": {"flag": "true", "title": "Hello World", "pic": "Mentioned",
                        "spaced": "Some Name", "cafe": "Caf\u00e9"}},
        {"name": "GfxPlug", "status": True, "description": "d",
         "parameters": {"BgPic": "Plugged", "UiIcon": "ico", "Buttons": buttons,
                        "Banner": "Unmapped", "Mode": "fast"}},
        {"name": "OffPlug", "status": False, "description": "d", "parameters": {"Pic": "OffPic"}},
        {"name": "Loose", "status": True, "description": "no header",
         "parameters": {"Sprite": "LooseSprite", "Face": "Actor1", "Flag": "true"}},
    ]
    (root / "js/plugins.js").write_text(
        "// Generated by RPG Maker.\n// Do not edit this file directly.\nvar $plugins =\n"
        + _mz_list(plugins) + ";\n")
    return root

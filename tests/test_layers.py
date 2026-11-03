"""Galv's Layer Graphics (img/layers/) support."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "rpg_asset_tools"))

import fixture
import refs as refs_mod
import usage
from index import build_asset_index
from rename_engine import plan_rename, rename_many, RenameItem, undo_last

PLUGIN_HEADER = """/*:
 * @target MZ
 * @command create
 * @text Create Layer
 * @arg mapId
 * @default 0
 * @arg layerId
 * @default 0
 * @arg graphic
 * @type file
 * @dir img/layers/
 * @arg xSpeed
 * @default 0
 * @command createStatic
 * @arg mapId
 * @arg layerId
 * @arg graphic
 * @type file
 * @dir img/layers/
 * @command refresh
 * @text Refresh Layer
 */
"""

NOTE = "LAYER 1 trees 0 0 255 5 10 0 0\nsome text\nLAYER_S 2 fog 80 64 85 12 0.5 0.5 0\r\nLAYER 3 Ghost 0 0 255 5 0 0 0"


def png(tag):
    return fixture._png(tag)


def build(root: Path, plugin_status=True, with_plugin=True) -> Path:
    game = fixture.build(root)
    (game / "img/layers").mkdir(parents=True, exist_ok=True)
    for n in ("trees", "fog", "cmdlayer", "static1", "battle1", "scriptload", "Orphan", "sub/deep"):
        (game / "img/layers" / (n + ".png")).parent.mkdir(parents=True, exist_ok=True)
        (game / "img/layers" / (n + ".png")).write_bytes(png("layer/" + n))
    if with_plugin:
        (game / "js/plugins/GALV_LayerGraphicsMZ.js").write_text(PLUGIN_HEADER)
        pj = game / "js/plugins.js"
        text = pj.read_text()
        start, end = text.find("["), text.rfind("]")
        data = json.loads(text[start:end + 1])
        data.append({"name": "GALV_LayerGraphicsMZ", "status": plugin_status, "description": "",
                     "parameters": {"fixPosition": "true"}})
        pj.write_text(text[:start] + json.dumps(data) + text[end + 1:])
    mp = game / "data/Map001.json"
    m = json.loads(mp.read_text())
    m["note"] = NOTE
    cmds = m["events"][1]["pages"][0]["list"]
    cmds.insert(0, {"code": 357, "indent": 0, "parameters": [
        "GALV_LayerGraphicsMZ", "create", "Create Layer",
        {"mapId": "0", "layerId": "4", "graphic": "cmdlayer", "xSpeed": "0"}]})
    cmds.insert(1, {"code": 357, "indent": 0, "parameters": [
        "GALV_LayerGraphicsMZ", "createStatic", "x",
        {"mapId": "1", "layerId": "5", "graphic": "static1"}]})
    cmds.insert(2, {"code": 355, "indent": 0, "parameters": ["Galv.LG.bLayer(1,'battle1',2,0,200,0,0);"]})
    cmds.insert(3, {"code": 655, "indent": 0, "parameters": ["var b = ImageManager.loadLayerGraphic(\"scriptload\");"]})
    cmds.insert(4, {"code": 355, "indent": 0, "parameters": ["Galv.LG.bLayer(1);"]})   # removal: no graphic
    mp.write_text(json.dumps(m, separators=(",", ":")) + "\n")
    return game


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = build(self.tmp / "g")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self):
        assets, scan = build_asset_index(self.game)
        return {(a["category"], a["name"]): a for a in assets}, scan

    def test_layers_are_listed_with_status(self):
        rows, _ = self.rows()
        st = lambda n: rows[("layer", n)]["status"]
        self.assertEqual(st("trees"), "ok")          # map note LAYER
        self.assertEqual(st("fog"), "ok")            # map note LAYER_S (CRLF line)
        self.assertEqual(st("cmdlayer"), "ok")       # plugin command create
        self.assertEqual(st("static1"), "ok")        # plugin command createStatic
        self.assertEqual(st("battle1"), "ok")        # Galv.LG.bLayer script call
        self.assertEqual(st("scriptload"), "ok")     # loadLayerGraphic("...") in a 655 line
        self.assertEqual(st("Orphan"), "unused")
        self.assertEqual(st("sub/deep"), "unused")
        self.assertEqual(rows[("layer", "Ghost")]["status"], "missing")   # note names a file that is absent

    def test_where_used_names_the_map_note_line(self):
        rows, _ = self.rows()
        items = usage.from_asset_refs(usage.Locator(self.game), rows[("layer", "fog")]["references"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].file, "data/Map001.json")
        self.assertIn("map note, line 3 (LAYER_S)", items[0].what)
        cmd = usage.from_asset_refs(usage.Locator(self.game), rows[("layer", "cmdlayer")]["references"])[0]
        self.assertEqual(cmd.line, "line 1")

    def test_note_parsing_matches_the_plugin(self):
        got = refs_mod.galv_note_layers(NOTE)
        self.assertEqual([(g, k) for _i, _n, k, g in got],
                         [("trees", "LAYER"), ("fog", "LAYER_S"), ("Ghost", "LAYER")])
        # double space shifts the fields exactly like the plugin's split(" ") does
        self.assertEqual(refs_mod.galv_note_layers("LAYER  1 trees 0")[0][3], "1")
        self.assertEqual(refs_mod.galv_note_layers("LAYERS 1 a"), [])      # needs "LAYER " / "LAYER_S "
        self.assertEqual(refs_mod.galv_note_layers("<other>nothing</other>"), [])

    def test_rename_rewrites_note_commands_and_scripts(self):
        ok, _plan = rename_many(self.game, [RenameItem("layer", "trees", "forest"),
                                    RenameItem("layer", "fog", "mist"),
                                    RenameItem("layer", "cmdlayer", "cl2"),
                                    RenameItem("layer", "battle1", "bat2"),
                                    RenameItem("layer", "scriptload", "sl2")], False, lambda *_: None)
        self.assertTrue(ok)
        m = json.loads((self.game / "data/Map001.json").read_text())
        self.assertEqual(m["note"].replace("\r\n", "\n"),
                         "LAYER 1 forest 0 0 255 5 10 0 0\nsome text\nLAYER_S 2 mist 80 64 85 12 0.5 0.5 0\n"
                         "LAYER 3 Ghost 0 0 255 5 0 0 0")
        self.assertIn("\r\n", m["note"])                       # line endings preserved
        text = (self.game / "data/Map001.json").read_text()
        self.assertIn('"graphic":"cl2"', text)
        self.assertIn("Galv.LG.bLayer(1,'bat2',2,0,200,0,0);", text)
        self.assertIn('loadLayerGraphic(\\"sl2\\")', text)
        self.assertTrue((self.game / "img/layers/forest.png").is_file())
        self.assertFalse((self.game / "img/layers/trees.png").exists())
        rows, _ = self.rows()
        self.assertEqual(rows[("layer", "forest")]["status"], "ok")
        undo_last(self.game, lambda *_: None)
        self.assertIn("LAYER 1 trees", json.loads((self.game / "data/Map001.json").read_text())["note"])
        self.assertTrue((self.game / "img/layers/trees.png").is_file())

    def test_rename_to_a_name_with_a_space_is_refused_for_notes(self):
        plan = plan_rename(self.game, [RenameItem("layer", "trees", "big forest")])
        self.assertTrue(any("space" in e for e in plan.item_errors[0]))
        plan = plan_rename(self.game, [RenameItem("layer", "battle1", "it's")])
        self.assertTrue(plan.item_errors[0])                  # quote would break the script string

    def test_notes_ignored_when_plugin_absent(self):
        with tempfile.TemporaryDirectory() as t:
            game = build(Path(t) / "g", with_plugin=False)
            assets, _ = build_asset_index(game)
            rows = {(a["category"], a["name"]): a for a in assets}
            self.assertNotIn(("layer", "Ghost"), rows)        # another plugin's LAYER text is not ours
            self.assertEqual(rows[("layer", "trees")]["status"], "possible")   # mentioned in the note only
            self.assertEqual(rows[("layer", "trees")]["mentions"][0]["file"], "Map001.json")
            self.assertEqual(rows[("layer", "battle1")]["status"], "ok")      # bLayer literal is unambiguous

    def test_disabled_plugin_makes_layers_possible_never_missing(self):
        with tempfile.TemporaryDirectory() as t:
            game = build(Path(t) / "g", plugin_status=False)
            assets, _ = build_asset_index(game)
            rows = {(a["category"], a["name"]): a for a in assets}
            self.assertEqual(rows[("layer", "trees")]["status"], "possible")
            self.assertNotIn(("layer", "Ghost"), rows)

    def test_mentions_listed_in_usage(self):
        with tempfile.TemporaryDirectory() as t:
            game = build(Path(t) / "g", with_plugin=False)
            assets, _ = build_asset_index(game)
            a = next(x for x in assets if x["name"] == "trees")
            items = usage.from_asset_refs(usage.Locator(game), a["references"], a["mentions"])
            self.assertEqual(len(items), 1)
            self.assertIn("map note", items[0].what)
            self.assertIn("possible", items[0].what)

    def test_layers_included_in_deploy_oversize_check(self):
        from deploy import build_deploy_report
        big = self.game / "img/layers/huge.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (5000).to_bytes(4, "big")
                        + (3000).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)
        assets, _ = build_asset_index(self.game)
        rep = build_deploy_report(self.game, assets)
        self.assertTrue(any("layers/huge.png" in o[0] for o in rep.oversized))


if __name__ == "__main__":
    unittest.main()

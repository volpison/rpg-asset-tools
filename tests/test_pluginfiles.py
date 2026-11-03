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
import pluginscan
from annotations import Annotations
from deploy import build_deploy_report, move_unused_dirs_out
from index import build_asset_index
from rename_engine import move_to_unused, undo_last, describe_last_undoable


def header(*lines):
    return "/*:\n" + "".join(f" * {l}\n" for l in lines) + " */\n"


def build(root: Path) -> Path:
    game = fixture.build(root)
    pl = game / "js/plugins"
    (pl / "Enabled.js").write_text(header("@target MZ", "@plugindesc Menu layout"))
    (pl / "Disabled.js").write_text(header("@target MZ"))
    (pl / "DeadWeight.js").write_text(header("@target MZ", "@plugindesc never loaded"))
    (pl / "BaseLib.js").write_text(header("@target MZ"))                            # not loaded...
    (pl / "NeedsBase.js").write_text(header("@target MZ", "@base BaseLib"))          # ...but needed
    (pl / "Caller.js").write_text(header("@target MZ", "@command go", "@arg x", "@type string"))
    text = (game / "js/plugins.js").read_text()
    start, end = text.find("["), text.rfind("]")
    data = json.loads(text[start:end + 1])
    data += [
        {"name": "Enabled", "status": True, "description": "", "parameters": {}},
        {"name": "Disabled", "status": False, "description": "", "parameters": {}},
        {"name": "NeedsBase", "status": True, "description": "", "parameters": {}},
        {"name": "Caller", "status": True, "description": "", "parameters": {}},
        {"name": "GoneEntry", "status": True, "description": "", "parameters": {}},   # no file
    ]
    (game / "js/plugins.js").write_text(text[:start] + json.dumps(data) + text[end + 1:])
    mp = game / "data/Map001.json"
    m = json.loads(mp.read_text())
    m["events"][1]["pages"][0]["list"].insert(0, {"code": 357, "indent": 0, "parameters": [
        "Caller", "go", "Go", {"x": "1"}]})
    mp.write_text(json.dumps(m, separators=(",", ":")) + "\n")
    return game


class PluginFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = build(self.tmp / "g")
        _a, self.scan = build_asset_index(self.game)
        self.res = pluginscan.scan_plugins(self.game, self.scan)
        self.rows = {r.name: r for r in self.res.rows}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_statuses(self):
        self.assertEqual(self.rows["Enabled"].status, "enabled")
        self.assertEqual(self.rows["Disabled"].status, "disabled")
        self.assertEqual(self.rows["DeadWeight"].status, "not_loaded")
        self.assertEqual(self.rows["GoneEntry"].status, "missing_file")
        self.assertEqual(self.rows["GfxPlug"].status, "enabled")       # from the base fixture

    def test_enabled_without_usage_is_never_called_unused(self):
        r = self.rows["Enabled"]
        self.assertEqual(r.status, "enabled")
        self.assertIn("Not reported as unused", r.hint)
        self.assertFalse(r.movable)

    def test_movable_only_when_not_loaded_and_not_needed(self):
        self.assertTrue(self.rows["DeadWeight"].movable)
        self.assertFalse(self.rows["BaseLib"].movable)                  # NeedsBase @base BaseLib
        self.assertIn("NeedsBase", self.rows["BaseLib"].needed_by)
        self.assertIn("needed", self.rows["BaseLib"].note)
        self.assertFalse(self.rows["Disabled"].movable)
        self.assertFalse(self.rows["GoneEntry"].movable)

    def test_command_and_reference_counts(self):
        self.assertEqual(self.rows["Caller"].commands_declared, 1)
        self.assertEqual(self.rows["Caller"].commands_called, 1)
        self.assertGreaterEqual(self.rows["GfxPlug"].asset_refs, 1)     # declared file parameters
        self.assertEqual(self.rows["Disabled"].commands_called, 0)

    def test_keep_flag(self):
        Annotations(self.game).set_flagged("plugin:DeadWeight", True)
        res = pluginscan.scan_plugins(self.game, self.scan, Annotations(self.game))
        r = {x.name: x for x in res.rows}["DeadWeight"]
        self.assertEqual(r.status, "kept")
        self.assertFalse(r.movable)

    def test_move_is_undoable_and_deploy_warns(self):
        rels = [r.file for r in self.res.rows if r.movable]
        self.assertEqual(rels, ["js/plugins/DeadWeight.js"])
        n, op = move_to_unused(self.game, rels, False, lambda *_: None, "_unused")
        self.assertEqual(n, 1)
        self.assertTrue((self.game / "js/plugins/_unused/DeadWeight.js").is_file())
        self.assertFalse((self.game / "js/plugins/DeadWeight.js").exists())
        res = pluginscan.scan_plugins(self.game, self.scan)
        self.assertNotIn("DeadWeight", {r.name for r in res.rows})       # _unused/ is ignored
        assets, _ = build_asset_index(self.game)
        rep = build_deploy_report(self.game, assets)
        self.assertTrue(any(d[0] == "js/plugins/_unused" for d in rep.unused_dirs))
        self.assertIsNotNone(describe_last_undoable(self.game))
        self.assertTrue(undo_last(self.game, lambda *_: None))
        self.assertTrue((self.game / "js/plugins/DeadWeight.js").is_file())

    def test_archive_out_takes_the_plugin_folder_too(self):
        move_to_unused(self.game, ["js/plugins/DeadWeight.js"], False, lambda *_: None, "_unused")
        dest = self.tmp / "arch"
        move_unused_dirs_out(self.game, dest, lambda *_: None)
        self.assertTrue((dest / "js/plugins/_unused/DeadWeight.js").is_file())
        self.assertFalse((self.game / "js/plugins/_unused").exists())

    def test_broken_plugins_js_reports_and_lists_nothing_risky(self):
        (self.game / "js/plugins.js").write_text("var $plugins = [ {broken ;")
        res = pluginscan.scan_plugins(self.game, None)
        self.assertTrue(res.errors)
        self.assertEqual(res.rows, [])

    def test_csv(self):
        out = self.tmp / "p.csv"
        pluginscan.export_plugins_csv(self.res.rows, out)
        self.assertIn("DeadWeight", out.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rpg_asset_tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dbscan
import fixture
import fixture_db
import usage
from index import build_asset_index


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = fixture_db.build(Path(self.tmp.name) / "g")
        self.scan = dbscan.scan_database(self.game)
        self.loc = usage.Locator(self.game)

    def tearDown(self):
        self.tmp.cleanup()

    def items(self, typ, ident):
        return usage.from_db_refs(self.loc, self.scan.refs[(typ, ident)])

    def test_db_ref_in_map_has_exact_place(self):
        it = self.items("item", 1)[0]                       # shop command
        self.assertEqual(it.file, "data/Map001.json")
        self.assertIn("Town (Map001)", it.place)
        self.assertEqual(it.event, "event 1 'EV' at (12, 8)")
        self.assertEqual(it.page, "page 1")
        self.assertRegex(it.line, r"^line \d+$")
        self.assertIn("uses it", it.what)

    def test_line_number_matches_command(self):
        it = self.items("item", 1)[0]
        m = dbscan._load(self.game / "data/Map001.json")
        cmds = m["events"][1]["pages"][0]["list"]
        n = int(it.line.split()[1])
        self.assertEqual(cmds[n - 1]["code"], 302)          # 1-based line is the shop command

    def test_script_ref_points_at_first_script_line(self):
        it = self.items("item", 5)[0]                       # $dataItems[5] in a 355/655 script
        m = dbscan._load(self.game / "data/Map001.json")
        n = int(it.line.split()[1])
        self.assertEqual(m["events"][1]["pages"][0]["list"][n - 1]["code"], 355)

    def test_database_and_special_files(self):
        it = self.items("skill", 3)[0]                      # class learning
        self.assertEqual(it.place, "Class 1 'Fighter'")
        troop_it = self.items("switch", 6)
        self.assertTrue(any(i.place.startswith("Troop 1") and i.page == "page 1" for i in troop_it))
        ce = self.items("switch", 9)[0]
        self.assertTrue(ce.place.startswith("Common event 3"))

    def test_plugin_refs(self):
        it = self.items("switch", 7)
        self.assertTrue(any(i.file == "js/plugins.js" for i in it))
        off = self.items("switch", 10)[0]
        self.assertTrue(off.optional)
        self.assertIn("(disabled plugin)", off.text())

    def test_sorting_maps_numerically(self):
        a = self.loc.describe("data/Map010.json", 1)
        b = self.loc.describe("data/Map002.json", 1)
        self.assertEqual([i.place for i in usage._sorted([a, b])], ["Map002", "Map010"])

    def test_missing_files_do_not_crash(self):
        it = self.loc.describe("data/Map099.json", 4, 0, 2, "x")
        self.assertEqual(it.place, "Map099")
        self.assertEqual(it.event, "event 4")

    def test_usage_text(self):
        text = usage.usage_text("Items: 1 Potion", self.items("item", 1), total=400)
        self.assertIn("- Town (Map001)", text)
        self.assertIn("and 399 more", text)

    def test_asset_references(self):
        with tempfile.TemporaryDirectory() as t:
            game = fixture.build(Path(t) / "g")
            assets, _scan = build_asset_index(game)
            pic = next(a for a in assets if a["name"] == "Pic1" and a["category"] == "picture")
            items = usage.from_asset_refs(usage.Locator(game), pic["references"])
            self.assertTrue(items)
            self.assertTrue(all(i.file for i in items))
            self.assertTrue(any("Map001" in i.place and i.line for i in items))
            plug = next(a for a in assets if a["name"] == "Plugged")
            pi = usage.from_asset_refs(usage.Locator(game), plug["references"])
            self.assertEqual(pi[0].file, "js/plugins.js")


if __name__ == "__main__":
    unittest.main()

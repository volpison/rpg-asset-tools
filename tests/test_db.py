import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rpg_asset_tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dbscan
import fixture_db
from annotations import Annotations
from scanner import scan_project


def statuses(game, ann=None):
    scan = dbscan.scan_database(game)
    rows = dbscan.classify(scan, ann)
    return scan, {(r.type, r.id): r for r in rows}


class DbScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = fixture_db.build(Path(self.tmp.name) / "g")
        self.scan, self.rows = statuses(self.game)

    def tearDown(self):
        self.tmp.cleanup()

    def st(self, typ, i):
        r = self.rows.get((typ, i))
        return r.status if r else None

    def test_items(self):
        for i in (1, 3, 4, 5, 6, 9):
            self.assertEqual(self.st("item", i), "used", i)
        self.assertEqual(self.st("item", 2), "unused")
        self.assertEqual(self.st("item", 10), "unused")        # it uses others, nobody uses it
        self.assertEqual(self.st("item", 8), "possible")       # only a disabled plugin
        self.assertIsNone(self.st("item", 7))                  # blank slot is not reported
        self.assertEqual(self.st("item", 99), "broken")        # referenced, missing

    def test_equipment_and_traits(self):
        self.assertEqual(self.st("weapon", 1), "used")
        self.assertEqual(self.st("weapon", 2), "unused")
        self.assertEqual(self.st("weapon", 3), "unused")
        self.assertEqual(self.st("armor", 1), "used")          # slot 1 + etype 2
        self.assertEqual(self.st("armor", 2), "unused")

    def test_skills_states(self):
        self.assertEqual(self.st("skill", 1), "engine")
        self.assertEqual(self.st("skill", 2), "engine")
        self.assertEqual(self.st("skill", 3), "used")          # class learning
        self.assertEqual(self.st("skill", 4), "unused")
        self.assertEqual(self.st("skill", 5), "used")          # enemy action + weapon trait
        self.assertEqual(self.st("skill", 6), "used")          # item effect (even an unused item)
        self.assertEqual(self.st("state", 1), "engine")
        self.assertEqual(self.st("state", 2), "used")
        self.assertEqual(self.st("state", 3), "unused")
        self.assertEqual(self.st("state", 4), "used")

    def test_actors_classes_enemies_troops(self):
        self.assertEqual(self.st("actor", 1), "used")          # starting party
        self.assertEqual(self.st("actor", 2), "used")          # command 129 and \N[2]
        self.assertEqual(self.st("actor", 3), "unused")
        self.assertEqual(self.st("class", 1), "used")
        self.assertEqual(self.st("class", 2), "used")
        self.assertEqual(self.st("class", 3), "unused")
        self.assertEqual(self.st("enemy", 1), "used")
        self.assertEqual(self.st("enemy", 2), "unused")
        self.assertEqual(self.st("troop", 1), "used")          # encounter list
        self.assertEqual(self.st("troop", 2), "unused")
        self.assertEqual(self.st("troop", 3), "used")          # command 301

    def test_common_events_and_animations(self):
        self.assertEqual(self.st("common_event", 1), "used")
        self.assertEqual(self.st("common_event", 2), "unused")
        self.assertEqual(self.st("common_event", 3), "used")   # auto-run with a switch
        self.assertEqual(self.st("common_event", 4), "used")   # reserveCommonEvent(4)
        self.assertEqual(self.st("common_event", 5), "used")   # item effect
        self.assertEqual(self.st("animation", 1), "used")      # skill 3
        self.assertEqual(self.st("animation", 2), "used")      # weapon 3
        self.assertEqual(self.st("animation", 3), "unused")
        self.assertEqual(self.st("animation", 4), "used")      # command 212

    def test_switches(self):
        self.assertEqual(self.st("switch", 1), "used")         # 121 set + 111 read
        self.assertEqual(self.st("switch", 2), "write_only")
        self.assertEqual(self.st("switch", 3), "read_only")    # page condition only
        self.assertEqual(self.st("switch", 4), "unused")
        self.assertEqual(self.st("switch", 5), "write_only")   # range 5-6 set
        self.assertEqual(self.st("switch", 6), "used")         # range set + troop page condition
        self.assertEqual(self.st("switch", 7), "used")         # plugin @type switch counts as both
        self.assertEqual(self.st("switch", 8), "write_only")   # script setValue
        self.assertEqual(self.st("switch", 9), "read_only")    # common event auto-run
        self.assertEqual(self.st("switch", 10), "possible")    # disabled plugin only
        self.assertIsNone(self.st("switch", 11))               # unnamed + unreferenced

    def test_variables(self):
        self.assertEqual(self.st("variable", 1), "used")       # set + \V[1]
        self.assertEqual(self.st("variable", 2), "write_only")
        self.assertEqual(self.st("variable", 3), "read_only")
        self.assertEqual(self.st("variable", 4), "unused")
        self.assertEqual(self.st("variable", 5), "write_only")
        self.assertEqual(self.st("variable", 6), "read_only")  # read by command 122 operand
        self.assertEqual(self.st("variable", 7), "used")       # script read + write across 355/655

    def test_used_by_has_places(self):
        r = self.rows[("item", 1)]
        self.assertTrue(any("Map001" in x.where for x in r.used_by))
        self.assertGreaterEqual(r.total_refs, 1)

    def test_plugin_caveat_for_untyped_number(self):
        self.assertTrue(any("DbPlug" in c and "Num" in c for c in self.scan.plugin_caveats))
        self.assertFalse(any("OffDb" in c for c in self.scan.plugin_caveats))

    def test_keep_flag(self):
        ann = Annotations(self.game)
        ann.set_ignored("db:item", "2", True)
        _scan, rows = statuses(self.game, Annotations(self.game))
        self.assertEqual(rows[("item", 2)].status, "kept")
        self.assertEqual(rows[("item", 8)].status, "possible")

    def test_dynamic_script_makes_unused_possible(self):
        with tempfile.TemporaryDirectory() as t:
            game = fixture_db.build(Path(t) / "g", dynamic=True)
            _scan, rows = statuses(game)
            self.assertEqual(rows[("switch", 4)].status, "possible")     # could be read by computed id
            self.assertEqual(rows[("switch", 2)].status, "possible")     # write-only, a dynamic read exists
            self.assertEqual(rows[("switch", 3)].status, "read_only")    # a dynamic READ cannot set it
            self.assertEqual(rows[("variable", 4)].status, "unused")     # no dynamic variable use
            self.assertEqual(rows[("item", 2)].status, "unused")

    def test_csv_export(self):
        out = Path(self.tmp.name) / "db.csv"
        dbscan.export_db_csv(list(self.rows.values()), out)
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("Elixir", text)
        self.assertIn("Set, never checked", text)

    def test_summary(self):
        s = dbscan.summarize(list(self.rows.values()))
        self.assertEqual(s["switch"]["write_only"], 3)

    def test_asset_scan_unaffected_by_db_params(self):
        sc = scan_project(self.game)
        self.assertFalse(any("Sw" in p or "Item" in p for p in sc.plugin_candidates))

    def test_corrupt_file_reported_not_crashing(self):
        (self.game / "data/Items.json").write_text("{not json")
        scan = dbscan.scan_database(self.game)
        self.assertTrue(any("Items.json" in e for e in scan.errors))

    def test_no_data_folder(self):
        with tempfile.TemporaryDirectory() as t:
            scan = dbscan.scan_database(Path(t))
            self.assertTrue(scan.errors)
            self.assertEqual(dbscan.classify(scan), [])

    def test_malformed_command_is_skipped(self):
        p = self.game / "data/Map001.json"
        m = json.loads(p.read_text())
        m["events"][1]["pages"][0]["list"].append({"code": 111, "parameters": []})
        p.write_text(json.dumps(m))
        _scan, rows = statuses(self.game)
        self.assertEqual(rows[("item", 1)].status, "used")


if __name__ == "__main__":
    unittest.main()

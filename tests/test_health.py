import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rpg_asset_tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import deploy  # noqa: E402
import fixture  # noqa: E402
import lint  # noqa: E402
from duplicates import find_duplicates  # noqa: E402
from index import build_asset_index, movable_orphans  # noqa: E402
from rename_engine import (merge_assets, move_to_unused, plan_merge,  # noqa: E402
                           plan_rename, rename_many, RenameItem, undo_last,
                           describe_last_undoable)


def quiet(_):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = fixture.build(self.tmp / "game")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def index(self):
        return build_asset_index(self.game)

    def snapshot(self):
        return {p.relative_to(self.game).as_posix(): p.read_bytes()
                for p in self.game.rglob("*")
                if p.is_file() and ".asset_tools" not in p.parts}

    def read(self, rel):
        return (self.game / rel).read_text(encoding="utf-8")


class TestDuplicates(Base):
    def test_finds_identical_assets_only_within_a_category(self):
        assets, _ = self.index()
        groups = find_duplicates(self.game, assets)
        found = {(g.category, tuple(sorted(m["name"] for m in g.members))) for g in groups}
        self.assertIn(("picture", ("Pic1", "Pic1Copy")), found)
        self.assertIn(("se", ("Hit", "HitCopy")), found)           # twins compared as a set
        self.assertEqual(len(groups), 2)                            # nothing else is identical
        pic = next(g for g in groups if g.category == "picture")
        self.assertEqual(pic.keeper["name"], "Pic1")                # shorter name wins the tie
        se = next(g for g in groups if g.category == "se")
        self.assertEqual(se.keeper["name"], "Hit")                  # referenced beats unreferenced
        self.assertGreater(se.freed_bytes, 0)

    def test_twin_set_must_match(self):
        (self.game / "audio/se/HitCopy.m4a").write_bytes(b"different")
        assets, _ = self.index()
        names = [m["name"] for g in find_duplicates(self.game, assets) for m in g.members]
        self.assertNotIn("HitCopy", names)

    def test_set_keeper(self):
        assets, _ = self.index()
        g = next(g for g in find_duplicates(self.game, assets) if g.category == "picture")
        g.set_keeper("Pic1Copy")
        self.assertEqual(g.merges(), [("picture", "Pic1", "Pic1Copy")])


class TestMerge(Base):
    def test_merge_repoints_refs_moves_copy_and_undoes(self):
        before = self.snapshot()
        ok, plan = merge_assets(self.game, [("picture", "Pic1Copy", "Pic1")], False, quiet)
        self.assertTrue(ok, plan.problems())
        self.assertNotIn("Pic1Copy", self.read("data/Map001.json"))
        self.assertTrue((self.game / "img/pictures/_unused/Pic1Copy.png").is_file())
        self.assertTrue((self.game / "img/pictures/Pic1.png").is_file())
        assets, _ = self.index()
        self.assertNotIn("missing", {a["status"] for a in assets})
        self.assertEqual(describe_last_undoable(self.game), "merge of 1 duplicate(s)")
        self.assertTrue(undo_last(self.game, quiet))
        self.assertEqual(before, self.snapshot())

    def test_merge_twins_and_unreferenced_copy(self):
        ok, _ = merge_assets(self.game, [("se", "HitCopy", "Hit")], False, quiet)
        self.assertTrue(ok)
        for ext in (".ogg", ".m4a"):
            self.assertTrue((self.game / f"audio/se/_unused/HitCopy{ext}").is_file())

    def test_preview_changes_nothing(self):
        before = self.snapshot()
        ok, plan = merge_assets(self.game, [("picture", "Pic1Copy", "Pic1")], True, quiet)
        self.assertTrue(ok)
        self.assertEqual(plan.ref_counts[0], 1)
        self.assertEqual(before, self.snapshot())

    def test_blocked_cases(self):
        cases = [
            [("picture", "Pic1", "Pic1")],                          # same asset
            [("picture", "Pic1Copy", "Lonely")],                    # not identical
            [("enemy", "Slime", "Slime")],                          # linked category
            [("picture", "Pic1Copy", "Pic1"), ("picture", "Pic1", "Lonely")],  # keeper merged away
            [("picture", "Nope", "Pic1")],                          # no file
        ]
        before = self.snapshot()
        for merges in cases:
            self.assertFalse(plan_merge(self.game, merges).ok, merges)
        self.assertFalse(merge_assets(self.game, cases[1], False, quiet)[0])
        self.assertEqual(before, self.snapshot())

    def test_blocked_if_file_changed_after_scan(self):
        assets, scan = self.index()
        (self.game / "img/pictures/Pic1Copy.png").write_bytes(b"edited meanwhile")
        self.assertFalse(plan_merge(self.game, [("picture", "Pic1Copy", "Pic1")], scan).ok)


class TestLint(Base):
    RULES = {"no_spaces": True, "ascii_only": True, "case_conflicts": True,
             "max_length": 0, "categories": {}}

    def issues(self, rules=None):
        assets, _ = self.index()
        return {(i.category, i.name): i for i in lint.lint_assets(assets, rules or self.RULES)}

    def test_clean_project_has_no_issues(self):
        self.assertEqual(self.issues(), {})

    def test_case_mismatch_is_detected_and_fixable(self):
        p = self.game / "data/Map001.json"
        p.write_text(p.read_text().replace('"sub/Deep"', '"SUB/deep"'))
        iss = self.issues()
        i = iss[("picture", "SUB/deep")]
        self.assertTrue(i.repoint)
        self.assertEqual(i.suggestion, "sub/Deep")
        self.assertIn("Android", i.problems[0])
        # the suggested fix goes through the normal rename path (JSON-only re-point)
        ok, plan = rename_many(self.game, [RenameItem("picture", "SUB/deep", "sub/Deep")], False, quiet)
        self.assertTrue(ok, plan.problems())
        self.assertIn('"sub/Deep"', self.read("data/Map001.json"))
        self.assertEqual(self.issues(), {})

    def test_spaces_and_non_ascii(self):
        for n in ("My Pic", "Café"):
            (self.game / f"img/pictures/{n}.png").write_bytes(fixture._png(n))
        iss = self.issues()
        self.assertEqual(iss[("picture", "My Pic")].suggestion, "My_Pic")
        self.assertEqual(iss[("picture", "Café")].suggestion, "Cafe")

    def test_user_rules_prefix_and_style(self):
        rules = dict(self.RULES, categories={"se": {"prefix": "se_", "style": "snake"}})
        iss = self.issues(rules)
        self.assertEqual(iss[("se", "Boom")].suggestion, "se_boom")
        self.assertEqual(iss[("se", "HitCopy")].suggestion, "se_hit_copy")
        self.assertNotIn(("picture", "Pic1"), iss)

    def test_suggestion_collisions_are_not_offered(self):
        (self.game / "img/pictures/My Pic.png").write_bytes(fixture._png("a"))
        (self.game / "img/pictures/My_Pic.png").write_bytes(fixture._png("b"))
        i = self.issues()[("picture", "My Pic")]
        self.assertIsNone(i.suggestion)
        self.assertTrue(any("already taken" in p for p in i.problems))

    def test_case_conflicts(self):
        (self.game / "img/pictures/lonely.png").write_bytes(fixture._png("x"))
        if len(list((self.game / "img/pictures").glob("[Ll]onely.png"))) < 2:
            self.skipTest("case-insensitive file system: the two names cannot coexist")
        iss = self.issues()
        for name in ("Lonely", "lonely"):
            self.assertTrue(any("letter case" in p for p in iss[("picture", name)].problems), name)
        # and the check can be switched off
        off = dict(TestLint.RULES, case_conflicts=False)
        self.assertEqual(self.issues(off), {})

    def test_rules_file_created_and_merged(self):
        r = lint.load_rules(self.game)
        self.assertTrue((self.game / ".asset_tools/rules.json").is_file())
        self.assertTrue(r["no_spaces"])
        (self.game / ".asset_tools/rules.json").write_text(
            json.dumps({"no_spaces": False, "categories": {"se": {"prefix": "x_"}}}))
        r = lint.load_rules(self.game)
        self.assertFalse(r["no_spaces"])
        self.assertTrue(r["ascii_only"])                      # default kept
        (self.game / ".asset_tools/rules.json").write_text("{ broken")
        self.assertTrue(lint.load_rules(self.game)["no_spaces"])   # bad file -> defaults


class TestDeploy(Base):
    def test_sizes_add_up(self):
        assets, _ = self.index()
        rep = deploy.build_deploy_report(self.game, assets)
        on_disk = sum(f.stat().st_size for a in assets for f in
                      [self.game / x for x in a["files"]])
        self.assertEqual(rep.total_bytes, on_disk)
        self.assertEqual(sum(c["bytes"] for c in rep.categories), on_disk)
        unused = sum((self.game / f).stat().st_size
                     for a in assets if a["status"] == "unused" for f in a["files"])
        self.assertEqual(rep.unused_bytes, unused)
        self.assertGreater(rep.unused_bytes, 0)
        self.assertLessEqual(len(rep.largest), deploy.LARGEST_N)

    def test_oversized_and_heavy(self):
        (self.game / "img/parallaxes").mkdir(exist_ok=True)
        (self.game / "img/parallaxes/Huge.png").write_bytes(fixture._png("h", 5000, 10))
        (self.game / "audio/bgm/Long.ogg").write_bytes(b"0" * (deploy.HEAVY_AUDIO_BYTES + 1))
        assets, _ = self.index()
        rep = deploy.build_deploy_report(self.game, assets)
        self.assertEqual([o[0] for o in rep.oversized], ["img/parallaxes/Huge.png"])
        self.assertEqual(rep.oversized[0][1:3], (5000, 10))
        self.assertIn("audio/bgm/Long.ogg", [h[0] for h in rep.heavy])

    def test_png_size_reader(self):
        p = self.tmp / "t.png"
        p.write_bytes(fixture._png("a", 123, 45))
        self.assertEqual(deploy.png_size(p), (123, 45))
        p.write_bytes(b"not a png")
        self.assertIsNone(deploy.png_size(p))

    def test_leftovers_and_unused_dirs_then_move_out(self):
        (self.game / ".git").mkdir()
        (self.game / ".git/x").write_bytes(b"1")
        (self.game / "data_backup_1").mkdir()
        (self.game / "save").mkdir()
        assets, _ = self.index()
        move_to_unused(self.game, [f for a in movable_orphans(assets) for f in a["files"]],
                       False, quiet)
        assets, _ = self.index()
        rep = deploy.build_deploy_report(self.game, assets)
        names = {l[0] for l in rep.leftovers}
        self.assertTrue({".git", "data_backup_1", "save", ".asset_tools"} <= names)
        self.assertTrue(rep.unused_dirs)
        self.assertTrue(any("unused/" in w for w in rep.warnings))

        dest = self.tmp / "archive"
        n = deploy.move_unused_dirs_out(self.game, dest, quiet)
        self.assertGreater(n, 0)
        self.assertTrue((dest / "img/pictures/_unused/Lonely.png").is_file())
        self.assertEqual(list(self.game.rglob("_unused")), [])      # folders removed
        assets, _ = self.index()
        self.assertEqual(deploy.build_deploy_report(self.game, assets).unused_dirs, [])

    def test_move_out_refuses_destination_inside_project(self):
        with self.assertRaises(ValueError):
            deploy.move_unused_dirs_out(self.game, self.game / "img", quiet)

    def test_undo_after_moving_out_is_refused_cleanly(self):
        assets, _ = self.index()
        move_to_unused(self.game, [f for a in movable_orphans(assets) for f in a["files"]],
                       False, quiet)
        deploy.move_unused_dirs_out(self.game, self.tmp / "arch", quiet)
        self.assertFalse(undo_last(self.game, quiet))              # files are gone: refuse, no crash


if __name__ == "__main__":
    unittest.main(verbosity=2)

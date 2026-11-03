import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rpg_asset_tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from annotations import Annotations  # noqa: E402
from index import build_asset_index, movable_orphans  # noqa: E402
from rename_engine import (RenameItem, move_to_unused, plan_rename,  # noqa: E402
                           rename_many, undo_last)


def quiet(_):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = fixture.build(self.tmp / "game")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def status(self):
        assets, scan = build_asset_index(self.game)
        return {(a["category"], a["name"]): a["status"] for a in assets}, assets, scan

    def read(self, rel):
        return (self.game / rel).read_text(encoding="utf-8")


class TestScanner(Base):
    def test_statuses(self):
        s, _, scan = self.status()
        self.assertEqual(scan.errors, [])
        # references found in places the old scanner missed
        for key in [("se", "Step"), ("se", "Boom"), ("character", "Hero2"),
                    ("effect", "Fire"), ("movie", "Intro"), ("bgm", "Theme"),
                    ("picture", "sub/Deep")]:
            self.assertEqual(s[key], "ok", key)
        # animation texture is only referenced inside the .efkefc -> possible
        self.assertEqual(s[("animation", "Fire")], "possible")
        # mentioned in actor note / plugins.js
        self.assertEqual(s[("picture", "Mentioned")], "possible")
        # genuinely unused
        for key in [("picture", "Lonely"), ("bgm", "Unused"), ("animation", "Spare")]:
            self.assertEqual(s[key], "unused", key)
        self.assertEqual(s[("system_img", "Window")], "engine")
        self.assertEqual(s[("system_img", "Custom")], "unused")

    def test_no_false_missing_from_plugins(self):
        s, _, _ = self.status()
        self.assertNotIn("missing", s.values())
        self.assertFalse([k for k in s if k[1] in ("true", "Hello World")])

    def test_audio_twins_are_one_row(self):
        _, assets, _ = self.status()
        hit = [a for a in assets if a["category"] == "se" and a["name"] == "Hit"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(len(hit[0]["files"]), 2)

    def test_linked_battler_is_one_row_two_files(self):
        _, assets, _ = self.status()
        slime = [a for a in assets if a["name"] == "Slime"]
        self.assertEqual(len(slime), 1)
        self.assertEqual(len(slime[0]["files"]), 2)

    def test_faces_and_characters_independent(self):
        s, _, _ = self.status()
        self.assertEqual(s[("face", "Actor1")], "ok")
        self.assertEqual(s[("character", "Actor1")], "ok")

    def test_broken_json_blocks_everything(self):
        (self.game / "data/Items.json").write_text("{ nope")
        _, assets, scan = self.status()
        self.assertEqual(len(scan.errors), 1)
        plan = plan_rename(self.game, [RenameItem("picture", "Pic1", "Pic9")], scan)
        self.assertFalse(plan.ok)

    def test_missing_detected(self):
        (self.game / "img/pictures/Pic1.png").unlink()
        s, _, _ = self.status()
        self.assertEqual(s[("picture", "Pic1")], "missing")


class TestRename(Base):
    def do(self, items, dry=False):
        ok, plan = rename_many(self.game, items, dry, quiet)
        return ok, plan

    def test_sv_enemy_and_enemy_move_together(self):
        for cat in ("enemy", "sv_enemy"):
            self.tearDown(); self.setUp()
            ok, _ = self.do([RenameItem(cat, "Slime", "Slime2")])
            self.assertTrue(ok)
            self.assertIn('"battlerName":"Slime2"', self.read("data/Enemies.json"))
            self.assertTrue((self.game / "img/enemies/Slime2.png").is_file())
            self.assertTrue((self.game / "img/sv_enemies/Slime2.png").is_file())
            self.assertFalse((self.game / "img/enemies/Slime.png").exists())

    def test_tileset_rename_updates_list(self):
        ok, _ = self.do([RenameItem("tileset", "Town", "Castle")])
        self.assertTrue(ok)
        self.assertIn('"Castle"', self.read("data/Tilesets.json"))
        self.assertTrue((self.game / "img/tilesets/Castle.png").is_file())

    def test_face_rename_leaves_character(self):
        ok, _ = self.do([RenameItem("face", "Actor1", "Hero")])
        self.assertTrue(ok)
        actors = json.loads(self.read("data/Actors.json"))
        self.assertEqual(actors[1]["faceName"], "Hero")
        self.assertEqual(actors[1]["characterName"], "Actor1")
        cmd322 = json.loads(self.read("data/Map001.json"))["events"][1]["pages"][0]["list"][0]
        self.assertEqual(cmd322["parameters"][3], "Hero")      # Change Actor Images
        self.assertTrue((self.game / "img/characters/Actor1.png").is_file())

    def test_character_rename_hits_322_and_route(self):
        ok, _ = self.do([RenameItem("character", "Hero2", "Knight")])
        self.assertTrue(ok)
        lst = json.loads(self.read("data/Map001.json"))["events"][1]["pages"][0]["list"]
        self.assertEqual(lst[0]["parameters"][1], "Knight")
        self.assertEqual(lst[1]["parameters"][1]["list"][1]["parameters"][0], "Knight")

    def test_se_rename_in_route_and_animation_and_twins(self):
        ok, _ = self.do([RenameItem("se", "Step", "Walk"), RenameItem("se", "Boom", "Bang")])
        self.assertTrue(ok)
        self.assertIn('"name":"Bang"', self.read("data/Animations.json"))
        self.assertIn('"name":"Walk"', self.read("data/Map001.json"))
        for ext in (".ogg", ".m4a"):
            self.assertTrue((self.game / f"audio/se/Walk{ext}").is_file())

    def test_swap_works(self):
        (self.game / "img/pictures/Pic2.png").write_bytes(b"two")
        orig1 = (self.game / "img/pictures/Pic1.png").read_bytes()
        ok, plan = self.do([RenameItem("picture", "Pic1", "Pic2"),
                            RenameItem("picture", "Pic2", "Pic1")])
        self.assertTrue(ok, plan.problems())
        self.assertEqual((self.game / "img/pictures/Pic2.png").read_bytes(), orig1)
        self.assertEqual((self.game / "img/pictures/Pic1.png").read_bytes(), b"two")

    def test_destination_exists_blocked(self):
        ok, plan = self.do([RenameItem("picture", "Pic1", "Lonely")])
        self.assertFalse(ok)
        self.assertTrue(plan.item_errors[0])
        self.assertTrue((self.game / "img/pictures/Pic1.png").is_file())
        self.assertFalse((self.game / ".asset_tools/backups").exists())

    def test_invalid_names_blocked(self):
        for bad in ("", "a/../b", "a:b", "sp ace /y", "same"):
            ok, plan = self.do([RenameItem("picture", "Pic1", bad)])
            if bad == "same":
                continue
            self.assertFalse(ok, bad)
        ok, _ = self.do([RenameItem("picture", "Pic1", "Pic1")])
        self.assertFalse(ok)

    def test_engine_default_blocked(self):
        ok, _ = self.do([RenameItem("system_img", "Window", "Win")])
        self.assertFalse(ok)

    def test_animation_texture_inside_effect_blocked(self):
        ok, plan = self.do([RenameItem("animation", "Fire", "Flame")])
        self.assertFalse(ok)

    def test_move_to_subfolder(self):
        ok, _ = self.do([RenameItem("picture", "Pic1", "UI/Pic1")])
        self.assertTrue(ok)
        self.assertTrue((self.game / "img/pictures/UI/Pic1.png").is_file())
        self.assertIn('"UI/Pic1"', self.read("data/Map001.json"))

    def test_json_only_repoint_of_missing_asset(self):
        (self.game / "img/pictures/Pic1.png").unlink()
        ok, plan = self.do([RenameItem("picture", "Pic1", "Lonely")])
        self.assertTrue(ok, plan.problems())
        self.assertIn('"Lonely"', self.read("data/Map001.json"))

    def test_preview_changes_nothing(self):
        def snap():     # the app's own disposable cache (.asset_tools) is not "your project"
            return {p: p.read_bytes() for p in self.game.rglob("*")
                    if p.is_file() and ".asset_tools" not in p.parts}
        before = snap()
        ok, plan = self.do([RenameItem("face", "Actor1", "Hero")], dry=True)
        self.assertTrue(ok)
        self.assertEqual(plan.json_changes.get("Actors.json"), 1)
        after = snap()
        self.assertEqual(before, after)

    def test_format_preserved_minimal_diff(self):
        before = self.read("data/Actors.json").splitlines()
        self.do([RenameItem("face", "Actor1", "Hero")])
        after = self.read("data/Actors.json").splitlines()
        self.assertEqual(len(before), len(after))
        diff = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(diff), 1)
        # Map file: only the changed lines differ, still one event per line
        self.assertTrue(self.read("data/Map001.json").count("\n") <= 5)

    def test_one_backup_per_batch(self):
        self.do([RenameItem("face", "Actor1", "Hero"), RenameItem("character", "Hero2", "K"),
                 RenameItem("se", "Step", "Walk")])
        self.assertEqual(len(list((self.game / ".asset_tools/backups").iterdir())), 1)
        self.assertEqual([p.name for p in self.game.iterdir() if p.name.startswith("data_backup")], [])

    def test_warning_for_plugin_mention(self):
        ok, plan = self.do([RenameItem("picture", "Mentioned", "M2")], dry=True)
        self.assertTrue(any("plugin" in w for w in plan.warnings))

    def test_annotations_follow_rename(self):
        ann = Annotations(self.game)
        ann.set_comment("face", "Actor1", "hello"); ann.set_color("face", "Actor1", "#aabbcc")
        self.do([RenameItem("face", "Actor1", "Hero")])
        ann2 = Annotations(self.game)
        self.assertEqual(ann2.get_comment("face", "Hero"), "hello")
        self.assertEqual(ann2.get_color("face", "Hero"), "#AABBCC")
        self.assertEqual(ann2.get_comment("face", "Actor1"), "")


class TestUndoAndOrphans(Base):
    def snapshot(self):
        return {p.relative_to(self.game).as_posix(): p.read_bytes()
                for p in self.game.rglob("*")
                if p.is_file() and ".asset_tools" not in p.parts}

    def test_undo_rename_restores_everything(self):
        before = self.snapshot()
        rename_many(self.game, [RenameItem("enemy", "Slime", "Slime2"),
                                RenameItem("se", "Step", "Walk")], False, quiet)
        self.assertNotEqual(before, self.snapshot())
        self.assertTrue(undo_last(self.game, quiet))
        self.assertEqual(before, self.snapshot())
        self.assertFalse(undo_last(self.game, quiet))   # nothing left

    def test_undo_refused_if_data_edited_afterwards(self):
        rename_many(self.game, [RenameItem("face", "Actor1", "Hero")], False, quiet)
        p = self.game / "data/Actors.json"
        p.write_text(p.read_text().replace('"id":1', '"id":1,"x":1', 1))
        self.assertFalse(undo_last(self.game, quiet))
        self.assertIn('"Hero"', p.read_text())

    def test_move_orphans_only_true_orphans_and_undo(self):
        assets, scan = build_asset_index(self.game)
        orphans = movable_orphans(assets)
        names = {(a["category"], a["name"]) for a in orphans}
        self.assertIn(("picture", "Lonely"), names)
        self.assertNotIn(("picture", "Mentioned"), names)       # mentioned -> protected
        self.assertNotIn(("animation", "Fire"), names)          # used by effect
        before = self.snapshot()
        rels = [f for a in orphans for f in a["files"]]
        n, _ = move_to_unused(self.game, rels, False, quiet)
        self.assertEqual(n, len(rels))
        self.assertTrue((self.game / "img/pictures/_unused/Lonely.png").is_file())
        assets2, _ = build_asset_index(self.game)
        self.assertEqual(movable_orphans(assets2), [])
        self.assertTrue(undo_last(self.game, quiet))
        self.assertEqual(before, self.snapshot())

    def test_rollback_on_failure(self):
        import rename_engine as re_
        before = self.snapshot()
        real = re_.write_json_atomic
        calls = {"n": 0}
        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return real(*a, **k)
        re_.write_json_atomic = boom
        try:
            ok, _ = rename_many(self.game, [RenameItem("face", "Actor1", "Hero"),
                                            RenameItem("picture", "Pic1", "P9"),
                                            RenameItem("character", "Hero2", "K")], False, quiet)
        finally:
            re_.write_json_atomic = real
        self.assertFalse(ok)
        self.assertEqual(before, self.snapshot())


class TestBatchTools(unittest.TestCase):
    def test_helpers(self):
        import batch_tools as b
        self.assertEqual(b.apply_find_replace("Hero_old", "old", "new"), "Hero_new")
        self.assertEqual(b.apply_find_replace("A1", r"(\d)", r"_\1", regex=True), "A_1")
        self.assertEqual(b.apply_find_replace("x", "(", "y", regex=True), "x")   # bad regex = no-op
        self.assertEqual(b.add_prefix_suffix("UI/Btn", "pre_", "_s"), "UI/pre_Btn_s")
        self.assertEqual(b.set_folder("a/b/Btn", "UI/Menu"), "UI/Menu/Btn")
        self.assertEqual(b.set_folder("a/Btn", ""), "Btn")

    def test_parse(self):
        import batch_tools as b
        pairs, w = b.parse_mapping_text("A\tB\nC,D\n# c\nE -> F\nG;H\n")
        self.assertEqual(pairs, [("A", "B"), ("C", "D"), ("E", "F"), ("G", "H")])
        pairs, _ = b.parse_mapping_text("x\ny")
        self.assertEqual(pairs, [("", "x"), ("", "y")])
        res, w = b.apply_pairs_to_rows(["a", "b"], [("", "x"), ("", "y")])
        self.assertEqual(res, {0: "x", 1: "y"})
        res, w = b.apply_pairs_to_rows(["a", "b"], [("b", "z"), ("q", "r")])
        self.assertEqual(res, {1: "z"}); self.assertEqual(len(w), 1)

    def test_legacy_json_mapping_file(self):
        import batch_tools as b
        p = Path(tempfile.mkdtemp()) / "m.json"
        p.write_text('{"Old": "New"}')
        self.assertEqual(b.parse_mapping_file(p)[0], [("Old", "New")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

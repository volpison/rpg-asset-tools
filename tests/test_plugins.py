import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rpg_asset_tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
import plugin_refs as pr  # noqa: E402
from asset_paths import list_disk_assets  # noqa: E402
from index import build_asset_index, format_reference  # noqa: E402
from rename_engine import (RenameItem, plan_rename, rename_many, undo_last,  # noqa: E402
                           merge_assets)


def quiet(_):
    pass


HEADER = '''/*:
 * @target MZ
 * @param Bg
 * @type file
 * @dir img/pictures/ui/
 * @param Buttons
 * @type struct<Btn>[]
 * @command show
 * @arg pic
 * @type file
 * @dir img/pictures/
 */
/*:ja
 * @param Bg
 * @text ja
 */
/*~struct~Btn:
 * @param icon
 * @type file
 * @dir img/pictures/
 * @param snd
 * @type file
 * @dir audio/se/
 */'''


class TestHeaderParsing(unittest.TestCase):
    def test_params_structs_commands(self):
        params, structs, commands = pr.parse_header(HEADER)
        self.assertEqual(params["Bg"], pr.ParamSpec("file", "img/pictures/ui/"))
        self.assertEqual(params["Buttons"].type, "struct<Btn>[]")
        self.assertEqual(structs["Btn"]["snd"], pr.ParamSpec("file", "audio/se/"))
        self.assertEqual(commands["show"]["pic"].type, "file")

    def test_localised_block_does_not_override(self):
        params, _, _ = pr.parse_header(HEADER)
        self.assertEqual(params["Bg"].type, "file")        # the :ja block has no @type

    def test_dir_to_category(self):
        self.assertEqual(pr.category_from_dir("img/pictures/"), ("picture", ""))
        self.assertEqual(pr.category_from_dir("img/pictures/ui/"), ("picture", "ui/"))
        self.assertEqual(pr.category_from_dir("audio/se"), ("se", ""))
        self.assertEqual(pr.category_from_dir("img/"), (None, ""))

    def test_header_free_file_gives_empty_schema(self):
        self.assertEqual(pr.parse_header("// nothing\nvar x = 1;"), ({}, {}, {}))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = fixture.build(self.tmp / "game")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def index(self):
        assets, scan = build_asset_index(self.game)
        return {(a["category"], a["name"]): a for a in assets}, scan

    def plugins_text(self):
        return (self.game / "js/plugins.js").read_text(encoding="utf-8")

    def plugin_entries(self):
        t = self.plugins_text()
        return {p["name"]: p for p in json.loads(t[t.index("["):t.rindex("]") + 1])}

    def snapshot(self):
        return {p.relative_to(self.game).as_posix(): p.read_bytes()
                for p in self.game.rglob("*")
                if p.is_file() and ".asset_tools" not in p.parts}


class TestScan(Base):
    def test_declared_parameters_become_references(self):
        s, scan = self.index()
        for key in [("picture", "Plugged"), ("picture", "ui/ico"), ("picture", "PlugBtn"),
                    ("se", "PlugSe"), ("picture", "PlugCmd")]:
            self.assertEqual(s[key]["status"], "ok", key)
            self.assertGreaterEqual(s[key]["ref_count"], 1, key)
        self.assertEqual(scan.plugin_declared, 6)       # 5 above + the disabled plugin's one
        self.assertEqual(scan.errors, [])

    def test_plugin_command_argument(self):
        s, _ = self.index()
        refs = s[("picture", "PlugCmd")]["references"]
        self.assertEqual(refs[0]["code"], 357)
        self.assertIn("Plugin command GfxPlug.show", format_reference(refs[0]))

    def test_disabled_plugin_never_missing_and_never_moved(self):
        s, _ = self.index()
        self.assertEqual(s[("picture", "OffPic")]["status"], "possible")
        self.assertEqual(s[("picture", "OffPic")]["mention_kinds"], ["disabled plugin"])
        (self.game / "img/pictures/OffPic.png").unlink()
        s, _ = self.index()
        self.assertNotIn(("picture", "OffPic"), s)       # not a "missing" error either

    def test_undeclared_parameters_stay_mentions(self):
        s, scan = self.index()
        self.assertEqual(s[("picture", "Unmapped")]["status"], "possible")
        # LooseSprite appears only in an undeclared parameter and an undeclared command arg
        self.assertEqual(s[("picture", "LooseSprite")]["status"], "possible")
        self.assertEqual(s[("picture", "LooseSprite")]["ref_count"], 0)

    def test_no_missing_anywhere(self):
        s, _ = self.index()
        self.assertEqual([k for k, a in s.items() if a["status"] == "missing"], [])

    def test_missing_declared_file_is_reported(self):
        (self.game / "img/pictures/Plugged.png").unlink()
        s, _ = self.index()
        self.assertEqual(s[("picture", "Plugged")]["status"], "missing")

    def test_broken_plugins_js_blocks_and_falls_back(self):
        (self.game / "js/plugins.js").write_text("var $plugins = [ {broken ;")
        s, scan = self.index()
        self.assertTrue(scan.plugins_js_error)
        self.assertTrue(any("plugins.js" in e for e in scan.errors))
        self.assertFalse(plan_rename(self.game, [RenameItem("picture", "Pic1", "Z")], scan).ok)

    def test_plugins_js_without_headers_still_scans(self):
        shutil.rmtree(self.game / "js/plugins")
        s, scan = self.index()
        # nothing is declared any more -> everything plugin-related is just mentioned
        self.assertEqual(s[("picture", "Plugged")]["status"], "possible")
        self.assertEqual(scan.plugin_declared, 0)


class TestFileArrays(Base):
    def test_file_array_parameter(self):
        (self.game / "js/plugins/ListPlug.js").write_text(
            "/*:\n * @param Imgs\n * @type file[]\n * @dir img/pictures/\n */\n")
        p = self.game / "js/plugins.js"
        entry = {"name": "ListPlug", "status": True, "description": "",
                 "parameters": {"Imgs": json.dumps(["Lonely", "Mentioned"], separators=(",", ":"))}}
        t = p.read_text()
        p.write_text(t[:t.rindex("\n]")] + ",\n" + json.dumps(entry, separators=(",", ":")) + t[t.rindex("\n]"):])
        assets, _ = build_asset_index(self.game)
        st = {(a["category"], a["name"]): a["status"] for a in assets}
        self.assertEqual(st[("picture", "Lonely")], "ok")           # was "unused" before
        ok, plan = rename_many(self.game, [RenameItem("picture", "Lonely", "Alone")], False, quiet)
        self.assertTrue(ok, plan.problems())
        entries = {e["name"]: e for e in json.loads(p.read_text()[p.read_text().index("["):p.read_text().rindex("]") + 1])}
        self.assertEqual(json.loads(entries["ListPlug"]["parameters"]["Imgs"]), ["Alone", "Mentioned"])


class TestRename(Base):
    def rename(self, *items, dry=False):
        return rename_many(self.game, [RenameItem(*i) for i in items], dry, quiet)

    def test_top_level_parameter_changes_exactly_one_line(self):
        before = self.plugins_text().splitlines()
        ok, plan = self.rename(("picture", "Plugged", "Plugged2"))
        self.assertTrue(ok, plan.problems())
        after = self.plugins_text().splitlines()
        self.assertEqual(len(before), len(after))
        self.assertEqual(sum(a != b for a, b in zip(before, after)), 1)
        self.assertEqual(before[:3], after[:3])                       # header comments intact
        self.assertEqual(self.plugin_entries()["GfxPlug"]["parameters"]["BgPic"], "Plugged2")
        self.assertTrue((self.game / "img/pictures/Plugged2.png").is_file())

    def test_nested_struct_inside_array_inside_string(self):
        ok, plan = self.rename(("picture", "PlugBtn", "Btn2"))
        self.assertTrue(ok, plan.problems())
        raw = self.plugin_entries()["GfxPlug"]["parameters"]["Buttons"]
        arr = json.loads(raw)
        self.assertEqual(json.loads(arr[0]), {"icon": "Btn2", "snd": "PlugSe"})
        compact = dict(separators=(",", ":"))               # Plugin Manager's own format
        self.assertEqual(raw, json.dumps([json.dumps({"icon": "Btn2", "snd": "PlugSe"}, **compact)],
                                         **compact))

    def test_audio_twins_in_plugin_struct(self):
        ok, _ = self.rename(("se", "PlugSe", "Click"))
        self.assertTrue(ok)
        arr = json.loads(self.plugin_entries()["GfxPlug"]["parameters"]["Buttons"])
        self.assertEqual(json.loads(arr[0])["snd"], "Click")
        for ext in (".ogg", ".m4a"):
            self.assertTrue((self.game / f"audio/se/Click{ext}").is_file())

    def test_plugin_command_argument_rewritten_in_map(self):
        ok, _ = self.rename(("picture", "PlugCmd", "Cmd2"))
        self.assertTrue(ok)
        text = (self.game / "data/Map001.json").read_text()
        self.assertIn('"pic":"Cmd2"', text)
        self.assertNotIn("PlugCmd", text)

    def test_other_parameters_and_unicode_untouched(self):
        self.rename(("picture", "Plugged", "Plugged2"))
        x = self.plugin_entries()["X"]["parameters"]
        self.assertEqual(x["cafe"], "Café")
        self.assertEqual(self.plugin_entries()["GfxPlug"]["parameters"]["Mode"], "fast")

    def test_relative_dir_parameter(self):
        ok, plan = self.rename(("picture", "ui/ico", "other/ico"))
        self.assertFalse(ok)
        self.assertTrue(any("must start with 'ui/'" in p for p in plan.problems()))
        ok, plan = self.rename(("picture", "ui/ico", "ui/icon2"))
        self.assertTrue(ok, plan.problems())
        self.assertEqual(self.plugin_entries()["GfxPlug"]["parameters"]["UiIcon"], "icon2")
        self.assertTrue((self.game / "img/pictures/ui/icon2.png").is_file())

    def test_disabled_plugin_reference_is_updated(self):
        ok, _ = self.rename(("picture", "OffPic", "Off2"))
        self.assertTrue(ok)
        self.assertEqual(self.plugin_entries()["OffPlug"]["parameters"]["Pic"], "Off2")

    def test_batch_with_plugins_maps_and_one_backup(self):
        ok, _ = self.rename(("picture", "Plugged", "A"), ("picture", "PlugCmd", "B"),
                            ("se", "PlugSe", "C"))
        self.assertTrue(ok)
        self.assertEqual(len(list((self.game / ".asset_tools/backups").iterdir())), 1)

    def test_preview_counts_plugin_fields_and_changes_nothing(self):
        before = self.snapshot()
        ok, plan = self.rename(("picture", "Plugged", "P2"), dry=True)
        self.assertTrue(ok)
        self.assertEqual(plan.json_changes, {"plugins.js": 1})
        self.assertEqual(before, self.snapshot())

    def test_undo_restores_plugins_js_byte_for_byte(self):
        before = self.snapshot()
        ok, _ = self.rename(("picture", "Plugged", "A"), ("picture", "PlugBtn", "B"),
                            ("picture", "PlugCmd", "C"))
        self.assertTrue(ok)
        self.assertNotEqual(before, self.snapshot())
        self.assertTrue(undo_last(self.game, quiet))
        self.assertEqual(before, self.snapshot())

    def test_undo_refused_if_plugins_js_edited_afterwards(self):
        self.rename(("picture", "Plugged", "A"))
        p = self.game / "js/plugins.js"
        p.write_text(p.read_text() + "// edited in the Plugin Manager\n")
        self.assertFalse(undo_last(self.game, quiet))

    def test_warning_only_for_undeclared_mentions(self):
        _, plan = self.rename(("picture", "Plugged", "A"), dry=True)
        self.assertEqual(plan.warnings, [])                 # fully handled: nothing to warn about
        _, plan = self.rename(("picture", "Unmapped", "U"), dry=True)
        self.assertTrue(any("plugin" in w for w in plan.warnings))

    def test_merge_repoints_plugin_references(self):
        (self.game / "img/pictures/PlugDup.png").write_bytes(
            (self.game / "img/pictures/Plugged.png").read_bytes())
        p = self.game / "js/plugins.js"
        p.write_text(p.read_text().replace('"BgPic":"Plugged"', '"BgPic":"PlugDup"'))
        ok, plan = merge_assets(self.game, [("picture", "PlugDup", "Plugged")], False, quiet)
        self.assertTrue(ok, plan.problems())
        self.assertEqual(self.plugin_entries()["GfxPlug"]["parameters"]["BgPic"], "Plugged")
        self.assertTrue((self.game / "img/pictures/_unused/PlugDup.png").is_file())


class TestUserMappingAndSuggestions(Base):
    def suggestions(self):
        _, scan = self.index()
        names = {}
        for (cat, name) in list_disk_assets(self.game):
            names.setdefault(name, set()).add(cat)
        return {s.path: s for s in pr.suggest_mappings(scan.plugin_candidates, names)}

    def test_suggestions(self):
        sug = self.suggestions()
        self.assertEqual(sug["Loose.Sprite"].category, "picture")
        self.assertEqual(sug["GfxPlug.Banner"].category, "picture")
        self.assertEqual(sug["cmd:Loose.go.img"].category, "picture")        # command arg too
        self.assertEqual(sug["Loose.Face"].categories, ["character", "face"])  # ambiguous
        self.assertIsNone(sug["Loose.Face"].category)
        self.assertNotIn("GfxPlug.BgPic", sug)             # already declared
        self.assertNotIn("Loose.Flag", sug)                # "true" is not an asset

    def test_mapping_turns_a_mention_into_a_reference_and_is_renamable(self):
        s, _ = self.index()
        self.assertNotEqual(s[("picture", "LooseSprite")]["ref_count"], 2)
        pr.add_user_mappings(self.game, {"Loose.Sprite": "picture",
                                         "cmd:Loose.go.img": "picture"})
        s, scan = self.index()
        a = s[("picture", "LooseSprite")]
        self.assertEqual((a["status"], a["ref_count"]), ("ok", 2))
        self.assertEqual(scan.plugin_user_mapped, 1)
        ok, plan = rename_many(self.game, [RenameItem("picture", "LooseSprite", "Sprite2")],
                               False, quiet)
        self.assertTrue(ok, plan.problems())
        self.assertEqual(self.plugin_entries()["Loose"]["parameters"]["Sprite"], "Sprite2")
        self.assertIn('"img":"Sprite2"', (self.game / "data/Map001.json").read_text())

    def test_mapping_file_roundtrip_and_validation(self):
        p = pr.add_user_mappings(self.game, {"A.b": "se"})
        pr.add_user_mappings(self.game, {"C.d": "picture"})
        self.assertEqual(pr.load_user_map(self.game), {"A.b": "se", "C.d": "picture"})
        with self.assertRaises(ValueError):
            pr.add_user_mappings(self.game, {"A.b": "nonsense"})
        p.write_text("{ broken")
        self.assertEqual(pr.load_user_map(self.game), {})            # bad file -> ignored

    def test_mapping_inside_encoded_json_of_an_untyped_parameter(self):
        entries = self.plugin_entries()
        p = self.game / "js/plugins.js"
        enc = json.dumps({"slots": [json.dumps({"img": "Lonely"})]}, separators=(",", ":"))
        text = p.read_text().replace('"flag":"true"', '"flag":' + json.dumps(enc))
        p.write_text(text)
        pr.add_user_mappings(self.game, {"X.flag.slots.img": "picture"})
        s, _ = self.index()
        self.assertEqual(s[("picture", "Lonely")]["status"], "ok")
        ok, _ = rename_many(self.game, [RenameItem("picture", "Lonely", "Alone")], False, quiet)
        self.assertTrue(ok)
        flag = self.plugin_entries()["X"]["parameters"]["flag"]
        self.assertEqual(json.loads(json.loads(flag)["slots"][0])["img"], "Alone")


if __name__ == "__main__":
    unittest.main(verbosity=2)

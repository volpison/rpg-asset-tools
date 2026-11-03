"""Drives gui.py's own logic against a fake Qt (see qt_stub.py)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "rpg_asset_tools"))

import qt_stub  # noqa: E402
qt_stub.install()
import fixture  # noqa: E402
import gui  # noqa: E402
import health_gui  # noqa: E402
import usage_gui  # noqa: E402
import progress_gui  # noqa: E402
from qt_stub import QDialog, QMessageBox  # noqa: E402


class WindowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = fixture.build(self.tmp / "g")
        QMessageBox.next_accept = True
        QDialog.result = "accept"
        QMessageBox.shown = []
        self.w = gui.MainWindow()
        self.w.open_project(self.game)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def row_of(self, name, cat=None):
        t = self.w.table
        for r in range(t.rowCount()):
            a = self.w.shown[t.item(r, 2).data(gui.Qt.UserRole)]
            if a["name"] == name and (cat is None or a["category"] == cat):
                return r
        raise AssertionError(f"{name} not shown")

    def select(self, *names):
        self.w.table.select_rows([self.row_of(n) for n in names])
        self.w.on_selection()


class TestWindow(WindowBase):
    def test_open_and_filter(self):
        w = self.w
        self.assertGreater(len(w.assets), 20)
        self.assertEqual(w.table.rowCount(), len(w.assets))
        w.status_combo.setCurrentIndex(3)       # Unused
        w.apply_filter()
        names = {w.shown[w.table.item(r, 2).data(gui.Qt.UserRole)]["name"]
                 for r in range(w.table.rowCount())}
        self.assertIn("Lonely", names)
        self.assertNotIn("Pic1", names)
        w.status_combo.setCurrentIndex(0)
        w.search.setText("pic")
        w.apply_filter()
        self.assertTrue(all("pic" in a["name"].lower() for a in w.shown))
        w.search.setText("")
        w.cat_combo.setCurrentIndex(w.cat_combo.findData("se"))
        w.apply_filter()
        self.assertTrue(all(a["category"] == "se" for a in w.shown))

    def test_details_and_notes_autosave(self):
        w = self.w
        self.select("Pic1")
        self.assertTrue(any("Show Picture" in x for x in w.ref_list.items))
        w.comment.setPlainText("remember me")
        w._comment_changed()
        self.select("Lonely")                    # switching selection flushes
        self.assertEqual(w.ann.get_comment("picture", "Pic1"), "remember me")
        self.assertEqual(w.comment.toPlainText(), "")

    def test_color_and_keep(self):
        w = self.w
        self.select("Lonely")
        w.set_color("#FFDBBB")
        self.assertEqual(w.ann.get_color("picture", "Lonely"), "#FFDBBB")
        w.toggle_keep(True)
        a = next(x for x in w.assets if x["name"] == "Lonely")
        self.assertEqual(a["status"], "ignored")
        w.toggle_keep(False)
        self.assertEqual(a["status"], "unused")

    def test_rename_flow_and_undo(self):
        w = self.w
        self.select("Pic1")
        dlg = gui.RenameDialog(self.game, w._selected(), w.scan)
        dlg.table.item(0, gui.RenameDialog.COL_NEW).setText("Renamed")
        dlg._recheck()
        self.assertTrue(dlg.btn_ok.enabled)
        self.assertEqual(dlg.result_items()[0].new, "Renamed")
        self.assertIn("OK", dlg.table.item(0, gui.RenameDialog.COL_CHECK).text())
        self.assertEqual(dlg._timer.fired, 0)            # recheck did not re-trigger itself

        # run through the window (dialog stubbed to accept with a prepared new name)
        orig = gui.RenameDialog

        class Prefilled(orig):
            def __init__(s, *a, **k):
                super().__init__(*a, **k)
                s.table.item(0, s.COL_NEW).setText("Renamed")

        gui.RenameDialog = Prefilled
        try:
            w.rename_selected()
        finally:
            gui.RenameDialog = orig
        self.assertTrue((self.game / "img/pictures/Renamed.png").is_file())
        self.assertTrue(any(a["name"] == "Renamed" for a in w.assets))
        self.assertTrue(w.act_undo.enabled)
        w.undo()
        self.assertTrue((self.game / "img/pictures/Pic1.png").is_file())
        self.assertFalse(w.act_undo.enabled)

    def test_rename_dialog_blocks_conflict(self):
        w = self.w
        self.select("Pic1")
        dlg = gui.RenameDialog(self.game, w._selected(), w.scan)
        dlg.table.item(0, gui.RenameDialog.COL_NEW).setText("Lonely")   # exists
        dlg._recheck()
        self.assertFalse(dlg.btn_ok.enabled)
        self.assertIn("exists", dlg.table.item(0, gui.RenameDialog.COL_CHECK).text())

    def test_bulk_tools_in_dialog(self):
        w = self.w
        self.select("Pic1", "Lonely")
        dlg = gui.RenameDialog(self.game, w._selected(), w.scan)
        dlg.prefix.setText("ui_")
        dlg._do_prefix_suffix()
        news = sorted(i.new for i in dlg.result_items())
        self.assertEqual(news, ["ui_Lonely", "ui_Pic1"])
        dlg._reset()
        dlg.find.setText("Pic"); dlg.repl.setText("Img")
        dlg._do_find_replace()
        self.assertEqual([i.new for i in dlg.result_items()], ["Img1"])
        dlg._apply_pairs([("Lonely", "Alone")], [])
        self.assertEqual(sorted(i.new for i in dlg.result_items()), ["Alone", "Img1"])

    def test_move_unused_confirm_and_cancel(self):
        w = self.w
        QMessageBox.next_accept = False
        w.move_unused()
        self.assertTrue((self.game / "img/pictures/Lonely.png").is_file())   # cancelled
        QMessageBox.next_accept = True
        w.move_unused()
        self.assertTrue((self.game / "img/pictures/_unused/Lonely.png").is_file())
        self.assertTrue((self.game / "img/pictures/Mentioned.png").is_file())  # possible: kept
        w.undo()
        self.assertTrue((self.game / "img/pictures/Lonely.png").is_file())

    def test_parse_errors_block_move(self):
        (self.game / "data/Items.json").write_text("{ broken")
        self.w.refresh()
        self.assertTrue(self.w.scan.errors)
        self.assertFalse(self.w.act_unused.enabled)

    def test_export(self):
        out = self.tmp / "r.csv"
        gui.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))
        self.w.export()
        self.assertIn("Lonely", out.read_text(encoding="utf-8-sig"))


class TestProgress(WindowBase):
    def test_long_jobs_get_a_progress_window_that_is_closed_at_the_end(self):
        made = []
        real = gui.ProgressDialog

        class Rec(real):
            def __init__(s, *a, **k):
                super().__init__(*a, **k)
                made.append(s)
        gui.ProgressDialog = Rec
        self.addCleanup(setattr, gui, "ProgressDialog", real)
        self.w.refresh()
        self.assertEqual(len(made), 1)
        self.assertFalse(made[0].active)                       # finished -> closed

    def test_progress_dialog_numbers(self):
        d = progress_gui.ProgressDialog("Scanning")
        d.set_progress(3, 10, "maps")
        d.set_progress(0, 0, "")                                # unknown total: busy bar, no crash
        d.add_log("hello")
        d.finish()
        self.assertFalse(d.active)

    def test_worker_hands_a_reporter_with_progress_to_the_job(self):
        got = []

        def job(log):
            log("text")
            log.progress(1, 2, "half")
            return 5
        w = gui.Worker(job)
        w.message.connect(got.append)
        w.progress.connect(lambda *a: got.append(a))
        w.succeeded.connect(got.append)
        w.run()
        self.assertEqual(got, ["text", (1, 2, "half"), 5])


class TestHealthWindow(WindowBase):
    """Health check dialog + how the main window runs its actions."""

    def report(self):
        from health import build_health
        return build_health(self.game, self.w.assets)

    def dialog(self):
        return health_gui.HealthDialog(self.game, self.report())

    def run_with(self, action_fn):
        """Open the health check from the window; `action_fn(dlg)` plays the user."""
        real = gui.HealthDialog

        class Scripted(real):
            def exec(s):
                s.check_all()                  # the real dialog starts empty
                action_fn(s)
                return QDialog.Accepted

        gui.HealthDialog = Scripted
        try:
            self.w.health_check()
        finally:
            gui.HealthDialog = real

    def test_tabs_are_filled(self):
        d = self.dialog()
        self.assertEqual(d.dup_table.rowCount(), 4)                  # 2 groups x 2 members
        roles = [d.dup_table.item(r, 4).text() for r in range(4)]
        self.assertEqual(roles.count("KEEP"), 2)
        self.assertEqual(d.name_table.rowCount(), 0)                  # fixture names are clean
        self.assertGreater(d.cat_table.rowCount(), 5)
        self.assertTrue(any("Unused" in l for l in d.check_lines()))
        self.assertTrue(any(".asset_tools" in l for l in d.check_lines()))

    def test_make_keeper_changes_roles_and_merge_direction(self):
        d = self.dialog()
        row = next(r for r in range(d.dup_table.rowCount())
                   if d.dup_table.item(r, 1).text() == "Pic1Copy")
        d.dup_table.select_rows([row])
        d._dup_make_keeper()
        d.dup_table.select_rows([next(r for r in range(d.dup_table.rowCount())
                                      if d.dup_table.item(r, 1).text() == "Pic1Copy")])
        d._dup_merge_selected()
        self.assertEqual(d.action[0], "merge")
        self.assertEqual(d.action[1], [("picture", "Pic1", "Pic1Copy")])

    def test_cancelled_confirmation_does_nothing(self):
        d = self.dialog()
        QMessageBox.next_accept = False
        d._dup_merge_all()
        self.assertIsNone(d.action)

    def test_merge_all_through_the_window_and_undo(self):
        self.run_with(lambda d: d._dup_merge_all())
        self.assertTrue((self.game / "img/pictures/_unused/Pic1Copy.png").is_file())
        self.assertTrue((self.game / "audio/se/_unused/HitCopy.ogg").is_file())
        self.assertNotIn("Pic1Copy", (self.game / "data/Map001.json").read_text())
        self.assertIn("merge", self.w.act_undo.text_)
        self.w.undo()
        self.assertTrue((self.game / "img/pictures/Pic1Copy.png").is_file())

    def test_naming_fix_all_opens_prefilled_rename_and_applies(self):
        (self.game / "img/pictures/My Pic.png").write_bytes(fixture._png("mp"))
        (self.game / "img/pictures/Café.png").write_bytes(fixture._png("cf"))
        self.w.refresh()
        self.run_with(lambda d: d._names_fix_all())
        self.assertTrue((self.game / "img/pictures/My_Pic.png").is_file())
        self.assertTrue((self.game / "img/pictures/Cafe.png").is_file())
        self.assertFalse((self.game / "img/pictures/My Pic.png").exists())

    def test_naming_fix_without_suggestion_explains(self):
        (self.game / "img/pictures/My Pic.png").write_bytes(fixture._png("a"))
        (self.game / "img/pictures/My_Pic.png").write_bytes(fixture._png("b"))
        self.w.refresh()
        d = self.dialog()
        d._names_fix_all()
        self.assertIsNone(d.action)                                   # nothing fixable -> info box

    def test_case_mismatch_fix_repoints_references(self):
        p = self.game / "data/Map001.json"
        p.write_text(p.read_text().replace('"sub/Deep"', '"SUB/deep"'))
        self.w.refresh()
        self.run_with(lambda d: d._names_fix_all())
        self.assertIn('"sub/Deep"', p.read_text())

    def test_archive_unused_folders(self):
        self.w.move_unused()
        self.w.refresh()
        gui.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(self.tmp / "arch"))
        d = self.dialog()
        self.assertTrue(d.btn_archive.enabled)
        self.run_with(lambda dd: dd._archive())
        self.assertTrue((self.tmp / "arch/img/pictures/_unused/Lonely.png").is_file())
        self.assertEqual(list(self.game.rglob("_unused")), [])

    def test_recheck_reopens(self):
        calls = []
        self.w.health_check = lambda: calls.append(1)
        self.w.handle_health_action(("recheck",))
        self.w.handle_health_action(None)               # no action: nothing happens
        self.assertEqual(calls, [1])

    # ---- Database tab -------------------------------------------------
    def _db_rows_shown(self, d):
        return [(d.db_table.item(r, 0).text(), d.db_table.item(r, 2).text(),
                 d.db_table.item(r, 3).text()) for r in range(d.db_table.rowCount())]

    def test_database_tab_filters(self):
        d = self.dialog()
        d.db_type.setCurrentIndex(d.db_type.findData("enemy"))
        d._fill_db()
        self.assertIn(("Enemies", "Slime", "Unused"), self._db_rows_shown(d))   # no troop uses it
        d.db_type.setCurrentIndex(0)
        d.db_filter.setCurrentIndex(2)                                            # everything
        d._fill_db()
        self.assertEqual(d.db_table.rowCount(), len(d.report.db_rows))           # nothing hidden
        d.db_filter.setCurrentIndex(0)
        d._fill_db()
        self.assertLessEqual(d.db_table.rowCount(), len(d.report.db_rows))

    def test_database_tab_shows_references(self):
        d = self.dialog()
        d.db_filter.setCurrentIndex(2)
        d._fill_db()
        row = next(i for i, x in enumerate(d._db_visible) if x.type == "actor" and x.id == 1)
        d.db_table.select_rows([row])
        d._db_show_refs()
        self.assertTrue(d.db_refs.items)

    def test_database_keep_flag_is_saved_and_toggles(self):
        d = self.dialog()
        d.db_type.setCurrentIndex(d.db_type.findData("enemy"))
        d._fill_db()
        d.db_table.select_rows([0])
        d._db_toggle_keep()
        self.assertIn("db:enemy:1", (self.game / ".asset_tools/ignore.json").read_text())
        d.db_filter.setCurrentIndex(2)
        d._fill_db()
        self.assertIn(("Enemies", "Slime", "Kept (flagged)"), self._db_rows_shown(d))
        d.db_table.select_rows([next(i for i, x in enumerate(d._db_visible)
                                     if x.type == "enemy" and x.id == 1)])
        d._db_toggle_keep()                                   # second press removes the flag
        self.assertNotIn("db:enemy:1", (self.game / ".asset_tools/ignore.json").read_text())

    def test_database_export_csv(self):
        d = self.dialog()
        out = self.tmp / "db.csv"
        gui.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))
        health_gui.QFileDialog.getSaveFileName = gui.QFileDialog.getSaveFileName
        d._db_export()
        self.assertIn("Slime", out.read_text(encoding="utf-8-sig"))

    # ---- Where is this used? ------------------------------------------
    def capture_usage_dialog(self):
        shown = []
        real = usage_gui.UsageDialog

        class Rec(real):
            def exec(s):
                shown.append(s)
                return QDialog.Accepted
        gui.UsageDialog = Rec
        health_gui.UsageDialog = Rec
        self.addCleanup(setattr, gui, "UsageDialog", real)
        self.addCleanup(setattr, health_gui, "UsageDialog", real)
        return shown

    def test_where_used_from_main_window(self):
        shown = self.capture_usage_dialog()
        self.select("Pic1")
        self.w.show_usage()
        self.assertEqual(len(shown), 1)
        d = shown[0]
        self.assertTrue(d.items)
        self.assertEqual(d.table.rowCount(), len(d.items))
        self.assertIn("Map001", d.table.item(0, 0).text())
        clip = []

        class FakeApp:
            @staticmethod
            def clipboard():
                class C:
                    setText = staticmethod(clip.append)
                return C
        real = usage_gui.QApplication
        usage_gui.QApplication = FakeApp
        self.addCleanup(setattr, usage_gui, "QApplication", real)
        d.copy_all()
        self.assertIn("Pic1", clip[0])
        self.assertIn("Map001", clip[0])
        d.table.select_rows([0])
        d.copy_selected()
        self.assertEqual(clip[1].count("\n- "), 1)

    def test_where_used_unused_asset_shows_no_window(self):
        shown = self.capture_usage_dialog()
        self.select("Lonely")
        self.w.show_usage()
        self.assertEqual(shown, [])

    def test_where_used_needs_exactly_one_selection(self):
        shown = self.capture_usage_dialog()
        self.select("Pic1", "Lonely")
        self.w.show_usage()
        self.assertEqual(shown, [])

    def test_where_used_in_database_tab(self):
        shown = self.capture_usage_dialog()
        d = self.dialog()
        d.db_filter.setCurrentIndex(2)
        d._fill_db()
        row = next(i for i, x in enumerate(d._db_visible) if x.type == "actor" and x.id == 1)
        d.db_table.select_rows([row])
        d._db_where()
        self.assertEqual(len(shown), 1)
        self.assertTrue(shown[0].items)
        d.db_table.select_rows([])                      # nothing selected: an info box, no window
        d._db_where()
        self.assertEqual(len(shown), 1)

    # ---- Maps tab ------------------------------------------------------
    def test_maps_tab_lists_and_keeps(self):
        d = self.dialog()
        self.assertIsNotNone(d.report.maps)
        d.map_filter.setCurrentIndex(2)
        d._fill_maps()
        self.assertEqual(d.map_table.rowCount(), len(d.report.maps.rows))
        self.assertGreaterEqual(d.map_table.rowCount(), 1)
        row = next(i for i, r in enumerate(d._map_visible) if r.id == 1)
        d.map_table.select_rows([row])
        d._maps_show_detail()
        d._maps_where()                          # map 1 has no inbound in this fixture: info box only
        out = self.tmp / "m.csv"
        health_gui.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))
        d._maps_export()
        self.assertIn("Map001.json", out.read_text(encoding="utf-8-sig"))

    def test_maps_keep_flag_roundtrip(self):
        d = self.dialog()
        d.map_filter.setCurrentIndex(2)
        d._fill_maps()
        r = next(x for x in d._map_visible if x.id == 1)
        before = r.status
        self.assertIn(before, ("unused", "possible", "orphan_file"))      # fixture has no start map
        d.map_table.select_rows([d._map_visible.index(r)])
        d._maps_toggle_keep()
        self.assertEqual(r.status, "kept")
        self.assertIn("map:1", (self.game / ".asset_tools/ignore.json").read_text())
        d.map_table.select_rows([d._map_visible.index(r)])
        d._maps_toggle_keep()
        self.assertEqual(r.status, before)
        self.assertNotIn("map:1", (self.game / ".asset_tools/ignore.json").read_text())

    # ---- Plugin files tab ----------------------------------------------
    def _add_dead_plugin(self):
        (self.game / "js/plugins/DeadWeight.js").write_text("/*:\n * @target MZ\n */\n")

    def test_plugin_files_tab_and_move_through_window(self):
        self._add_dead_plugin()
        d = self.dialog()
        names = [d.pf_table.item(r, 0).text() for r in range(d.pf_table.rowCount())]
        self.assertIn("DeadWeight", names)
        # enabled plugins never appear under "needs attention"
        self.assertNotIn("GfxPlug", names)
        d.pf_table.select_rows([names.index("DeadWeight")])
        d._pf_show_hint()
        self.assertIn("Safe to move", d.pf_hint.text())
        self.run_with(lambda dd: dd._pf_move_all())
        self.assertTrue((self.game / "js/plugins/_unused/DeadWeight.js").is_file())
        self.assertFalse((self.game / "js/plugins/DeadWeight.js").exists())
        self.assertIn("move", self.w.act_undo.text_)
        self.w.undo()
        self.assertTrue((self.game / "js/plugins/DeadWeight.js").is_file())

    def test_plugin_files_move_refuses_non_movable(self):
        d = self.dialog()
        d.pf_filter.setCurrentIndex(1)
        d._fill_plugin_files()
        row = next(i for i, r in enumerate(d._pf_visible) if r.name == "GfxPlug")
        d.pf_table.select_rows([row])
        d._pf_move_selected()
        self.assertIsNone(d.action)                       # info box only

    def test_plugin_files_keep_flag(self):
        self._add_dead_plugin()
        d = self.dialog()
        row = next(i for i, r in enumerate(d._pf_visible) if r.name == "DeadWeight")
        d.pf_table.select_rows([row])
        d._pf_toggle_keep()
        self.assertIn("plugin:DeadWeight", (self.game / ".asset_tools/ignore.json").read_text())
        d.pf_filter.setCurrentIndex(1)
        d._fill_plugin_files()
        self.assertTrue(any(r.name == "DeadWeight" and r.status == "kept" for r in d._pf_visible))

    def test_plugins_tab_lists_undeclared_parameters(self):
        d = self.dialog()
        rows = [(d.plugin_table.item(r, 0).text(), d.plugin_table.item(r, 1).text(),
                 d.plugin_table.item(r, 4).text()) for r in range(d.plugin_table.rowCount())]
        self.assertEqual(len(rows), 5)
        self.assertIn(("Loose", "Sprite", "ready"), rows)
        self.assertIn(("Loose", "command go.img", "ready"), rows)
        self.assertTrue(any(r[1] == "Face" and r[2].startswith("ambiguous") for r in rows))
        self.assertIn("recognised", d.plugin_info.text())

    def test_plugins_map_all_through_the_window(self):
        before = next(x for x in self.w.assets if x["name"] == "LooseSprite")
        self.assertEqual(before["status"], "possible")
        self.run_with(lambda d: d._plugins_map_all())
        after = next(x for x in self.w.assets if x["name"] == "LooseSprite")
        self.assertEqual((after["status"], after["ref_count"]), ("ok", 2))
        self.assertTrue((self.game / ".asset_tools/plugin_params.json").is_file())
        # and the ambiguous one was NOT mapped
        self.assertNotIn("Loose.Face", (self.game / ".asset_tools/plugin_params.json").read_text())

    def test_plugins_ambiguous_only_does_nothing(self):
        d = self.dialog()
        row = next(r for r in range(d.plugin_table.rowCount())
                   if d.plugin_table.item(r, 1).text() == "Face")
        d.plugin_table.select_rows([row])
        d._plugins_map_selected()
        self.assertIsNone(d.action)

    def test_mapping_then_rename_updates_plugin(self):
        self.run_with(lambda d: d._plugins_map_all())
        row = self.row_of("LooseSprite")
        self.w.table.select_rows([row])
        self.w.on_selection()
        orig = gui.RenameDialog

        class Prefilled(orig):
            def __init__(s, *a, **k):
                super().__init__(*a, **k)
                s.table.item(0, s.COL_NEW).setText("Sprite2")
        gui.RenameDialog = Prefilled
        try:
            self.w.rename_selected()
        finally:
            gui.RenameDialog = orig
        text = (self.game / "js/plugins.js").read_text()
        self.assertIn('"Sprite":"Sprite2"', text)
        self.w.undo()
        self.assertIn('"Sprite":"LooseSprite"', (self.game / "js/plugins.js").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestLazyHealth(WindowBase):
    def open_empty(self):
        captured = {}
        real = gui.HealthDialog

        class Probe(real):
            def exec(s):
                captured["d"] = s
                captured["before"] = set(s.report.done)
                return QDialog.Accepted

        gui.HealthDialog = Probe
        try:
            self.w.health_check()
        finally:
            gui.HealthDialog = real
        return captured

    def test_opens_with_nothing_checked(self):
        c = self.open_empty()
        self.assertEqual(c["before"], set())
        self.assertIsNone(c["d"].report.deploy)

    def test_single_tab_runs_only_that_check(self):
        d = self.open_empty()["d"]
        d._run_keys(["names"])
        self.assertEqual(d.report.done, {"names"})
        self.assertEqual(d.name_table.rowCount(), 0)
        self.assertEqual(d.report.dups, [])           # not computed
        d._run_keys(["dups"])
        self.assertEqual(d.report.done, {"names", "dups"})
        self.assertEqual(d.dup_table.rowCount(), 4)

    def test_check_all_only_runs_missing_ones(self):
        d = self.open_empty()["d"]
        d._run_keys(["db"])
        calls = []
        import health
        real = health.run_sections
        health.run_sections = lambda rep, keys, *a, **k: (calls.append(list(keys)), real(rep, keys, *a, **k))[1]
        try:
            d.check_all()
        finally:
            health.run_sections = real
        self.assertEqual(calls, [[k for k in health.SECTIONS if k != "db"]])
        self.assertEqual(d.report.done, set(health.SECTIONS))

    def test_results_survive_closing_and_reopening(self):
        c1 = self.open_empty()
        c1["d"]._run_keys(["dups"])
        c2 = self.open_empty()
        self.assertEqual(c2["before"], {"dups"})

    def test_rescan_discards_old_results(self):
        c1 = self.open_empty()
        c1["d"]._run_keys(["dups"])
        self.w.refresh()
        self.assertEqual(self.open_empty()["before"], set())

    def test_close_blocked_while_running(self):
        d = self.open_empty()["d"]
        d._busy = True
        d.reject()                                    # must not raise / close
        d._busy = False

    def test_full_report_still_opens_everything(self):
        from health import build_health
        d = health_gui.HealthDialog(self.game, build_health(self.game, self.w.assets))
        self.assertEqual(d.dup_table.rowCount(), 4)


class TestSafetyMessages(WindowBase):
    def test_cancelling_the_copy_notice_stops_a_rename(self):
        a = next(x for x in self.w.assets if x["name"] == "Lonely")
        before = (self.game / "img/pictures/Lonely.png").is_file()
        QMessageBox.next_accept = False
        try:
            self.w._run_rename([dict(a)])
        finally:
            QMessageBox.next_accept = True
        self.assertTrue(before and (self.game / "img/pictures/Lonely.png").is_file())

    def test_undo_refusal_explains_why(self):
        # move the unused files aside, then make Undo impossible by deleting the moved files
        self.w.move_unused()
        for p in (self.game / "img/pictures/_unused").glob("*"):
            p.unlink()
        QMessageBox.shown.clear()
        self.w.undo()
        warned = [x for x in QMessageBox.shown if isinstance(x, tuple) and x[0] == "warning"]
        self.assertTrue(warned)
        self.assertIn("Nothing was undone", warned[-1][1][2])
        self.assertIn("CANNOT UNDO", warned[-1][1][2])

    def test_collect_reasons(self):
        c = gui._Collect(lambda t: None)
        c("fine")
        c("CANNOT UNDO: x was modified after the operation")
        self.assertIn("CANNOT UNDO", c.reasons())
        self.assertNotIn("fine", c.reasons())

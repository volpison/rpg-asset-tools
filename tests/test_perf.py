import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "rpg_asset_tools"))

import dbscan
import filecache
import fixture
import fixture_db
import mapscan
import parallel
import scanner
from index import RefLocations, build_asset_index


def square(x):
    return x * x


def _sig(sc):
    return (sorted((r.name, r.categories, r.file, r.context, r.event, r.page, r.command, r.code)
                   for r in sc.refs),
            sorted(sc.mentions.tokens.items(), key=lambda kv: kv[0]),
            sorted(sc.plugin_candidates.items()), sc.errors, sc.files_scanned, sc.plugin_declared)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = fixture.build(self.tmp / "g")
        self._old = filecache.MIN_BYTES
        filecache.MIN_BYTES = 0                      # fixture files are tiny: cache them anyway

    def tearDown(self):
        filecache.MIN_BYTES = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cached_scan_equals_fresh_scan(self):
        cold = scanner.scan_project(self.game)
        self.assertTrue(list((self.game / ".asset_tools/cache").glob("assets__*.json")))
        warm = scanner.scan_project(self.game)
        fresh = scanner.scan_project(self.game, use_cache=False)
        self.assertEqual(_sig(cold), _sig(warm))
        self.assertEqual(_sig(cold), _sig(fresh))

    def test_hit_does_not_reparse(self):
        scanner.scan_project(self.game)
        with mock.patch.object(scanner, "walk_refs", side_effect=AssertionError("re-walked")):
            scanner.scan_project(self.game)           # everything comes from the cache

    def test_changed_file_is_rescanned(self):
        scanner.scan_project(self.game)
        mp = self.game / "data/Map001.json"
        mp.write_text(mp.read_text().replace('"Pic1"', '"Lonely"'))
        sc = scanner.scan_project(self.game)
        names = {r.name for r in sc.refs if r.file == "Map001.json"}
        self.assertIn("Lonely", names)
        self.assertNotIn("Pic1", names)

    def test_plugin_change_invalidates(self):
        scanner.scan_project(self.game)
        before = filecache.fingerprint(self.game)
        (self.game / "js/plugins/GfxPlug.js").write_text(
            (self.game / "js/plugins/GfxPlug.js").read_text() + "\n// edited\n")
        self.assertNotEqual(before, filecache.fingerprint(self.game))
        (self.game / ".asset_tools/plugin_params.json").write_text('{"params": {}}')
        self.assertNotEqual(before, filecache.fingerprint(self.game))

    def test_corrupt_or_foreign_cache_files_are_ignored(self):
        scanner.scan_project(self.game)
        for p in (self.game / ".asset_tools/cache").glob("*.json"):
            p.write_text("{not json")
        fresh = scanner.scan_project(self.game, use_cache=False)
        self.assertEqual(_sig(scanner.scan_project(self.game)), _sig(fresh))
        for p in (self.game / ".asset_tools/cache").glob("*.json"):
            p.write_text(json.dumps({"v": 999, "fp": "x", "sig": [0, 0], "data": {"refs": [], "mentions": []}}))
        self.assertEqual(_sig(scanner.scan_project(self.game)), _sig(fresh))

    def test_errors_are_never_cached(self):
        (self.game / "data/Broken.json").write_text("{oops")
        a = scanner.scan_project(self.game)
        self.assertTrue(any("Broken.json" in e for e in a.errors))
        (self.game / "data/Broken.json").write_text("{}")
        b = scanner.scan_project(self.game)
        self.assertFalse(any("Broken.json" in e for e in b.errors))

    def test_cache_is_json_not_pickle(self):
        scanner.scan_project(self.game)
        for p in (self.game / ".asset_tools/cache").glob("*.json"):
            json.loads(p.read_text(encoding="utf-8"))

    def test_clear(self):
        scanner.scan_project(self.game)
        self.assertGreater(filecache.FileCache(self.game).clear(), 0)
        self.assertEqual(list((self.game / ".asset_tools/cache").glob("*.json")), [])

    def test_db_and_map_scans_use_the_cache_and_agree(self):
        with tempfile.TemporaryDirectory() as t:
            game = fixture_db.build(Path(t) / "g")
            db1 = dbscan.scan_database(game)
            db2 = dbscan.scan_database(game)
            self.assertEqual({k: sorted(v) for k, v in db1.kinds.items()},
                             {k: sorted(v) for k, v in db2.kinds.items()})
            self.assertEqual(db1.ref_total, db2.ref_total)
            self.assertTrue(list((game / ".asset_tools/cache").glob("db__*.json")))
            assets, _ = build_asset_index(game)
            m1 = mapscan.scan_maps(game, assets)
            m2 = mapscan.scan_maps(game, assets)
            self.assertEqual([(r.id, r.status, r.note) for r in m1.rows],
                             [(r.id, r.status, r.note) for r in m2.rows])
            self.assertTrue(list((game / ".asset_tools/cache").glob("map__*.json")))


class ParallelTests(unittest.TestCase):
    def test_pool_results_keep_order(self):
        self.assertEqual(parallel.run_parallel(square, list(range(12)), workers=2), [i * i for i in range(12)])

    def test_progress_callback_counts_everything(self):
        seen = []
        parallel.run_parallel(square, list(range(9)), on_done=seen.append, workers=2)
        self.assertEqual(max(seen), 9)

    def test_falls_back_when_the_pool_cannot_start(self):
        with mock.patch.object(parallel, "ProcessPoolExecutor", side_effect=RuntimeError("no processes")):
            self.assertEqual(parallel.run_parallel(square, list(range(10)), workers=2),
                             [i * i for i in range(10)])

    def test_small_jobs_skip_the_pool(self):
        with mock.patch.object(parallel, "ProcessPoolExecutor", side_effect=AssertionError("pool used")):
            out = parallel.run_parallel(square, list(range(10)), workers=4, weights=[100] * 10)
        self.assertEqual(out, [i * i for i in range(10)])


class LazyRefTests(unittest.TestCase):
    def test_reflocations_behaves_like_a_list_of_dicts(self):
        with tempfile.TemporaryDirectory() as t:
            game = fixture.build(Path(t) / "g")
            assets, _ = build_asset_index(game)
            a = next(x for x in assets if x["name"] == "Pic1" and x["category"] == "picture")
            refs = a["references"]
            self.assertIsInstance(refs, RefLocations)
            self.assertEqual(len(refs), a["ref_count"])
            self.assertIsInstance(refs[0], dict)
            self.assertEqual(refs[:1], [refs[0]])
            self.assertEqual(list(refs), [refs[i] for i in range(len(refs))])
            self.assertEqual(refs[0]["file"], "Map001.json")


class ProgressReportTests(unittest.TestCase):
    def test_scan_reports_progress_when_the_log_supports_it(self):
        with tempfile.TemporaryDirectory() as t:
            game = fixture.build(Path(t) / "g")
            calls = []

            class Log:
                def __call__(self, text):
                    pass

                def progress(self, done, total, text=""):
                    calls.append((done, total))
            scanner.scan_project(game, Log())
            self.assertTrue(calls)
            self.assertTrue(all(0 <= d <= tot for d, tot in calls))
            scanner.scan_project(game, print if False else (lambda s: None))     # a plain callable still works


if __name__ == "__main__":
    unittest.main()


class TestFitWidths(unittest.TestCase):
    def test_fills_width_when_roomy(self):
        from fit_table import fit_widths
        w = fit_widths([100, 200, 100], 800)
        self.assertEqual(sum(w), 800)
        self.assertTrue(w[1] > w[0])

    def test_narrow_shares_fairly(self):
        from fit_table import fit_widths
        w = fit_widths([60, 2000, 3000], 600)
        self.assertEqual(w[0], 60)                 # small column keeps its size
        self.assertEqual(w[1], w[2])               # big ones split the rest
        self.assertLessEqual(sum(w), 600)

    def test_tiny_window(self):
        from fit_table import fit_widths
        self.assertEqual(fit_widths([500, 500], 50), [48, 48])


class TestFileFacts(unittest.TestCase):
    def setUp(self):
        import fixture
        self.tmp = Path(tempfile.mkdtemp())
        self.game = fixture.build(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assets(self):
        from index import build_asset_index
        return build_asset_index(self.game)[0]

    def test_second_deploy_run_reads_no_png_headers(self):
        import deploy
        assets = self._assets()
        first = deploy.build_deploy_report(self.game, assets)
        calls = []
        real = deploy.png_size
        deploy.png_size = lambda p: (calls.append(p), real(p))[1]
        try:
            second = deploy.build_deploy_report(self.game, assets)
        finally:
            deploy.png_size = real
        self.assertEqual(calls, [])
        self.assertEqual(first.total_bytes, second.total_bytes)
        self.assertEqual(first.oversized, second.oversized)

    def test_changed_file_is_read_again(self):
        import deploy
        assets = self._assets()
        deploy.build_deploy_report(self.game, assets)
        p = self.game / "img/pictures/Pic1.png"
        p.write_bytes(p.read_bytes() + b"\0")
        import os
        os.utime(p, ns=(1, 1))
        calls = []
        real = deploy.png_size
        deploy.png_size = lambda q: (calls.append(Path(q).name), real(q))[1]
        try:
            deploy.build_deploy_report(self.game, assets)
        finally:
            deploy.png_size = real
        self.assertEqual(calls, ["Pic1.png"])

    def test_duplicate_hashes_are_cached_and_results_equal(self):
        import duplicates
        assets = self._assets()
        g1 = duplicates.find_duplicates(self.game, assets)
        calls = []
        real = duplicates.sha256_of
        duplicates.sha256_of = lambda p: (calls.append(p), real(p))[1]
        try:
            g2 = duplicates.find_duplicates(self.game, assets)
        finally:
            duplicates.sha256_of = real
        self.assertEqual(calls, [])
        self.assertEqual([[m["name"] for m in g.members] for g in g1],
                         [[m["name"] for m in g.members] for g in g2])
        self.assertTrue(g1)

    def test_corrupt_facts_file_is_ignored(self):
        import deploy
        assets = self._assets()
        (self.game / ".asset_tools/cache").mkdir(parents=True, exist_ok=True)
        (self.game / ".asset_tools/cache/filefacts.json").write_text("{ nope")
        rep = deploy.build_deploy_report(self.game, assets)
        self.assertGreater(rep.total_bytes, 0)


class TestWarmPathSpeedups(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = fixture.build(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disk_listing_nested_twins_and_unused_dirs(self):
        import asset_paths
        for d in ("_unused", "unused"):
            (self.game / f"img/pictures/{d}").mkdir()
            (self.game / f"img/pictures/{d}/Hidden.png").write_bytes(fixture._png(d))
        (self.game / "img/pictures/sub/Deep.txt").write_text("not an image")
        got = asset_paths.list_disk_assets(self.game)
        self.assertIn(("picture", "sub/Deep"), got)
        self.assertNotIn(("picture", "_unused/Hidden"), got)
        self.assertNotIn(("picture", "unused/Hidden"), got)
        twins = got[("se", "Hit")]
        self.assertEqual(sorted(p.suffix for p in twins), [".m4a", ".ogg"])
        self.assertTrue(all(p.is_file() for v in got.values() for p in v))

    def test_plugin_command_counts_same_with_and_without_cache(self):
        import json
        import pluginscan
        # a data file big enough to be cached, holding plugin commands
        cmds = [{"code": 357, "indent": 0, "parameters": ["GfxPlug", "show", "Show", {}]}] * 400
        ev = {"events": [None, {"id": 1, "pages": [{"list": cmds + [{"code": 0}]}]}], "note": "x" * 40000}
        (self.game / "data/Map050.json").write_text(json.dumps(ev), encoding="utf-8")
        first = pluginscan._count_calls(self.game / "data")
        self.assertTrue((self.game / ".asset_tools/cache").is_dir())
        second = pluginscan._count_calls(self.game / "data")
        self.assertEqual(first, second)
        self.assertGreaterEqual(first.get("GfxPlug", {}).get("show", 0), 400)
        (self.game / "data/Map050.json").write_text(json.dumps(ev) + " ", encoding="utf-8")   # changed
        self.assertEqual(pluginscan._count_calls(self.game / "data"), first)

    def test_reflocations_files(self):
        assets, _ = build_asset_index(self.game)
        a = next(x for x in assets if x["name"] == "Pic1")
        self.assertEqual(a["references"].files(), {loc["file"] for loc in a["references"]})


class TestSmallFileStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "d.json"
        self.f.write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_small_results_are_kept_in_one_shared_file(self):
        c = filecache.FileCache(self.tmp)
        sig = filecache.signature(self.f)
        self.assertLess(sig[0], filecache.MIN_BYTES)
        self.assertIsNone(c.get("map", "d.json", sig))
        c.put("map", "d.json", sig, {"n": 1})
        c.flush()
        files = sorted(p.name for p in (self.tmp / ".asset_tools/cache").iterdir())
        self.assertEqual(files, ["small__map.json"])
        c2 = filecache.FileCache(self.tmp)
        self.assertEqual(c2.get("map", "d.json", sig), {"n": 1})

    def test_changed_file_or_plugin_change_invalidates(self):
        c = filecache.FileCache(self.tmp)
        sig = filecache.signature(self.f)
        c.put("map", "d.json", sig, {"n": 1})
        c.flush()
        self.f.write_text("{ }")
        import os
        os.utime(self.f, ns=(5, 5))
        self.assertIsNone(filecache.FileCache(self.tmp).get("map", "d.json", filecache.signature(self.f)))
        (self.tmp / "js").mkdir()
        (self.tmp / "js/plugins.js").write_text("var $plugins=[];")      # fingerprint input
        self.assertIsNone(filecache.FileCache(self.tmp).get("map", "d.json", sig))

    def test_deleted_files_are_forgotten_and_corrupt_store_ignored(self):
        c = filecache.FileCache(self.tmp)
        sig = filecache.signature(self.f)
        c.put("map", "gone.json", sig, 1)
        c.put("map", "d.json", sig, 2)
        c.flush()
        c2 = filecache.FileCache(self.tmp)
        c2.get("map", "d.json", sig)                  # only d.json is seen in this run
        c2._small_dirty.add("map")
        c2.flush()
        import json
        raw = json.loads((self.tmp / ".asset_tools/cache/small__map.json").read_text())
        self.assertEqual(list(raw["e"]), ["d.json"])
        (self.tmp / ".asset_tools/cache/small__map.json").write_text("{ nope")
        self.assertIsNone(filecache.FileCache(self.tmp).get("map", "d.json", sig))

    def test_scan_results_identical_with_small_store(self):
        import fixture
        game = fixture.build(self.tmp / "g")
        a = scanner.scan_project(game)
        b = scanner.scan_project(game)                # second run reads the shared small-file store
        self.assertEqual(_sig(a), _sig(b))
        self.assertTrue((game / ".asset_tools/cache/small__assets.json").is_file())

    def test_db_and_map_scans_same_on_second_run_with_small_store(self):
        import test_maps
        from annotations import Annotations
        game = test_maps.build(self.tmp / "m")
        d1 = dbscan.classify(dbscan.scan_database(game), Annotations(game))
        m1 = mapscan.scan_maps(game)
        d2 = dbscan.classify(dbscan.scan_database(game), Annotations(game))
        m2 = mapscan.scan_maps(game)
        self.assertEqual([(r.type, r.id, r.status) for r in d1], [(r.type, r.id, r.status) for r in d2])
        self.assertEqual([(r.id, r.status, r.note) for r in m1.rows], [(r.id, r.status, r.note) for r in m2.rows])
        cache_files = sorted(p.name for p in (game / ".asset_tools/cache").iterdir())
        self.assertIn("small__map.json", cache_files)
        self.assertIn("small__db.json", cache_files)


class TestPluginTexts(unittest.TestCase):
    def setUp(self):
        import plugin_refs
        self.pr = plugin_refs
        self.pr._TEXTS.clear()
        self.tmp = Path(tempfile.mkdtemp())
        self.pdir = self.tmp / "js/plugins"
        self.pdir.mkdir(parents=True)
        for n in ("A", "B", "My.Dotted"):
            (self.pdir / f"{n}.js").write_text(f"/*:\n * @plugindesc {n}\n * @param P{n}\n * @type file\n * @dir img/pictures/\n */", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.pr._TEXTS.clear()

    def test_texts_and_names(self):
        t = self.pr.plugin_texts(self.tmp)
        self.assertEqual(list(t), ["A", "B", "My.Dotted"])
        self.assertIn("@plugindesc B", t["B"])

    def test_dotted_plugin_names_survive_load_schema(self):
        s = self.pr.load_schema(self.tmp)
        self.assertIn("My.Dotted", s.plugins_with_header)

    def test_second_call_does_not_reopen_unchanged_files(self):
        self.pr.plugin_texts(self.tmp)
        opened = []
        real = Path.read_text
        Path.read_text = lambda self_, *a, **k: (opened.append(self_.name), real(self_, *a, **k))[1]
        try:
            self.pr.plugin_texts(self.tmp)
            self.pr.load_schema(self.tmp)
        finally:
            Path.read_text = real
        self.assertEqual([n for n in opened if n.endswith(".js")], [])

    def test_changed_file_is_read_again(self):
        self.pr.plugin_texts(self.tmp)
        p = self.pdir / "A.js"
        p.write_text(p.read_text() + "\n// edited", encoding="utf-8")
        self.assertIn("// edited", self.pr.plugin_texts(self.tmp)["A"])

"""
smoke_test_qt.py - drives the REAL window (real PySide6, hidden) through the main flows.

    python smoke_test_qt.py                  # builds a small fake project in a temp folder
    python smoke_test_qt.py --assets 6000    # ... with more assets (table / speed stress)
    python smoke_test_qt.py --show           # same, but with visible windows
    python smoke_test_qt.py --project "D:\\Games\\MyGame"
                                            # READ-ONLY timings on your own project: open it,
                                            # rescan, run every Health tab. Moves/renames are
                                            # never done in this mode (it only writes the
                                            # .asset_tools/cache folder the app always writes).

Prints PASS/FAIL per step with timings. Exit code 0 = everything passed.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("RPGAT_NO_WATCHDOG", "1")

RESULTS: list[tuple[str, bool, float, str]] = []
APP = None


def step(name):
    """Decorator-free helper: `with step("name") as s:` records PASS/FAIL + seconds."""
    class _S:
        def __enter__(self_):
            self_.t = time.monotonic()
            self_.note = ""
            print(f"... {name}", flush=True)
            return self_

        def __exit__(self_, et, ev, tb):
            dt = time.monotonic() - self_.t
            ok = et is None
            note = self_.note
            if not ok:
                note = "".join(traceback.format_exception(et, ev, tb)).strip().splitlines()[-1]
                print("".join(traceback.format_exception(et, ev, tb)), flush=True)
            RESULTS.append((name, ok, dt, note))
            print(f"    {'PASS' if ok else 'FAIL'}  {dt:.2f}s  {note}", flush=True)
            return True            # keep going: report every failure, not just the first
    return _S()


def pump(n=20):
    for _ in range(n):
        APP.processEvents()


def wait_idle(win, timeout=300.0):
    """Let background jobs finish while the event loop keeps running (like the real app)."""
    t0 = time.monotonic()
    pump(5)
    while win._running:
        APP.processEvents()
        time.sleep(0.005)
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"job still running after {timeout:.0f}s")
    pump(30)


def make_project(root: Path, extra_assets: int) -> Path:
    import json
    import smoke_fixture
    game = smoke_fixture.build(root)
    (game / "data/Map001 - Copy.json").write_text("{}", encoding="utf-8")     # a stray file
    (game / "data/Leftover.json").write_text("{}", encoding="utf-8")          # another stray
    png = smoke_fixture._png("stress")
    d = game / "img/pictures/stress"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(extra_assets):
        (d / f"S{i:05d}.png").write_bytes(png + i.to_bytes(4, "big"))         # unique content
    # a few bulky maps so the process pool is used (it skips tiny projects)
    pad = [{"code": 401, "indent": 0, "parameters": ["lorem ipsum " * 8]} for _ in range(2500)]
    for n in range(10, 18):
        m = {"data": [0], "note": "", "events": [None, {"id": 1, "name": "EV", "x": 1, "y": 1, "pages": [
            {"conditions": {}, "list": pad + [{"code": 0}]}]}]}
        (game / f"data/Map{n:03d}.json").write_text(json.dumps(m), encoding="utf-8")
    return game


def count_pool_use():
    import parallel
    box = {"n": 0}
    real = parallel.ProcessPoolExecutor

    class Counting(real):
        def __init__(self, *a, **k):
            box["n"] += 1
            super().__init__(*a, **k)
    parallel.ProcessPoolExecutor = Counting
    return box


def main() -> int:
    global APP
    import faulthandler
    faulthandler.enable()          # a crash inside Qt or numpy then prints where, instead of vanishing
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", type=int, default=3000, help="extra unused pictures to create")
    ap.add_argument("--project", help="read-only timing run on this existing project")
    ap.add_argument("--show", action="store_true", help="visible windows (default: hidden)")
    ap.add_argument("--audio", help="time the loop editor on this .ogg file (read-only; nothing is written)")
    ap.add_argument("--max-table-seconds", type=float, default=8.0)
    args = ap.parse_args()
    if not args.show:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    with step("import PySide6 and the app") as s:
        from PySide6.QtCore import QSettings, Qt
        from PySide6.QtWidgets import QApplication, QDialog, QSplitter
        import PySide6
        import gui
        import health
        import health_gui
        from fit_table import FitTable
        s.note = f"PySide6 {getattr(PySide6, '__version__', '?')}"
    if not RESULTS[-1][1]:
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="rpgat_smoke_"))
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp / "settings"))
    APP = QApplication.instance() or QApplication(sys.argv)
    APP.setOrganizationName("rpg_asset_tools_smoke")
    APP.setApplicationName("smoke")
    pool = count_pool_use()
    popups = []                                  # message boxes would block a hidden window: answer them

    from PySide6.QtWidgets import QMessageBox

    def _quiet(kind, answer):
        def f(*a, **k):
            popups.append((kind, [str(x) for x in a[1:3]]))
            return answer
        return staticmethod(f)
    QMessageBox.question = _quiet("question", QMessageBox.Yes)
    QMessageBox.information = _quiet("information", QMessageBox.Ok)
    QMessageBox.warning = _quiet("warning", QMessageBox.Ok)
    QMessageBox.critical = _quiet("critical", QMessageBox.Ok)
    read_only = bool(args.project)

    with step("create the main window") as s:
        win = gui.MainWindow()
        win.show()
        pump()
    if not RESULTS[-1][1]:
        return 1

    if read_only:
        game = Path(args.project)
    else:
        with step(f"build a fake project (+{args.assets} pictures)"):
            game = make_project(tmp / "game", args.assets)

    real_ack = gui.MainWindow._ack_backup_notice
    acks = []
    gui.MainWindow._ack_backup_notice = lambda self: (acks.append(1), True)[1]   # never block on it

    with step("open the project (cold)") as s:
        win.open_project(game)
        wait_idle(win)
        assert win.assets, "no assets were listed"
        assert win.table.rowCount() == len(win.shown), "table and list disagree"
        s.note = f"{len(win.assets)} assets"

    with step("rescan and refill the table (the old freeze)") as s:
        for i in range(2):
            t = time.monotonic()
            win.refresh()
            wait_idle(win)
            s.note = f"pass {i + 1}: {time.monotonic() - t:.1f}s"
        t = time.monotonic()
        win.apply_filter()
        dt = time.monotonic() - t
        s.note += f"; table fill {dt:.1f}s"
        assert dt < args.max_table_seconds, f"table fill took {dt:.1f}s"

    with step("search, filter and sort the table") as s:
        win.search.setText("S0001")
        pump()
        n1 = win.table.rowCount()
        win.search.setText("")
        pump()
        t = time.monotonic()
        win.table.sortByColumn(2, Qt.DescendingOrder)
        win.table.sortByColumn(0, Qt.AscendingOrder)
        pump()
        assert win.table.rowCount() == len(win.shown)
        s.note = f"search rows={n1}, sort {time.monotonic() - t:.1f}s"

    with step("colour menu has a swatch for every colour") as s:
        from annotations import PRESET_COLORS
        acts = [a for a in win.color_menu.actions() if not a.isSeparator()]
        assert len(acts) == len(PRESET_COLORS) + 2, len(acts)
        assert all(not a.icon().isNull() for a in acts), "an entry has no swatch"
        pm = acts[1].icon().pixmap(16, 16).toImage()
        want = list(PRESET_COLORS.values())[0].lstrip("#").upper()
        got = "%02X%02X%02X" % (pm.pixelColor(8, 8).red(), pm.pixelColor(8, 8).green(), pm.pixelColor(8, 8).blue())
        assert got == want, f"swatch is {got}, wanted {want}"

    with step("select a row and show its details") as s:
        win.table.selectRow(0)
        pump()
        assert win._selected(), "selection did not register"

    with step("external player button for sounds") as s:
        def row_of(category):
            for r in range(win.table.rowCount()):
                idx = win.table.item(r, 2).data(Qt.UserRole)
                if idx is not None and win.shown[idx]["category"] == category:
                    return r
            return None
        opened = []
        real_open = gui.open_in_external_player
        gui.open_in_external_player = lambda path, parent=None, ask=True: (opened.append((path.name, ask)), True)[1]
        try:
            wanted = []
            for cat in ("bgm", "se"):
                r = row_of(cat)
                assert r is not None, f"the fake project has no {cat} row"
                win.table.selectRow(r)
                pump()
                wanted.append(win._selected()[0]["name"])
                assert not win.btn_external.isHidden(), f"no external player button for a {cat}"
                assert win.btn_external.text().startswith("Open in external player")
                assert not hasattr(win, "btn_play"), "the old Play audio button is still there"
                win.btn_external.click()
            assert len(opened) == 2 and all(not ask for _n, ask in opened), opened   # no confirmation question
            # the file of the selected asset (a row may be the .ogg or its .m4a twin), and an audio file
            assert all(n.startswith(w) and n.lower().endswith((".ogg", ".m4a")) for (n, _a), w in zip(opened, wanted)), (opened, wanted)
            r = row_of("bgm")
            win.table.selectRow(r)
            pump()
            assert not win.btn_loop.isHidden(), "the loop editor button is missing for a bgm"
            r = row_of("se")
            win.table.selectRow(r)
            pump()
            assert win.btn_loop.isHidden(), "an se must not get a loop editor button"
            r = row_of("picture")
            if r is not None:
                win.table.selectRow(r)
                pump()
                assert win.btn_external.isHidden(), "a picture must not get the external player button"
        finally:
            gui.open_in_external_player = real_open
        s.note = f"shown for bgm and se, opens {opened[0][0]} directly (no question), hidden for pictures"

    with step("progress window opens and closes") as s:
        from progress_gui import ProgressDialog
        p = ProgressDialog("Test")
        p.show()
        p.set_progress(3, 10, "maps")
        p.set_progress(0, 0, "")
        p.add_log("hello")
        p.finish()
        pump()
        assert not p.active

    from health import empty_report
    rep = empty_report(game)
    dlg = health_gui.HealthDialog(game, rep, win, runner=win._run, assets=win.assets, scan=win.scan)
    dlg.show()
    dlg.resize(940, 600)
    pump()
    with step("Health check opens instantly with empty tabs") as s:
        assert not rep.done
        assert dlg.tabs.count() == len(health.SECTIONS)

    for key in health.SECTIONS:
        with step(f"Health tab: {health.SECTION_LABELS[key]}") as s:
            t = time.monotonic()
            dlg._run_keys([key])
            wait_idle(win)
            assert rep.has(key), "the check did not complete"
            s.note = dlg.tabs.tabText(health.SECTIONS.index(key))

    with step("Health tabs: columns fit the window") as s:
        bad = []
        for i in range(dlg.tabs.count()):
            dlg.tabs.setCurrentIndex(i)
            pump()
            for t in dlg.tabs.widget(i).findChildren(FitTable):
                if not t.isVisible():
                    continue
                w = [t.columnWidth(c) for c in range(t.columnCount())]
                vp = t.viewport().width()
                if sum(w) > vp + 4 or min(w) < 30:
                    bad.append(f"{dlg.tabs.tabText(i)}: widths={w} viewport={vp}")
        dlg.resize(700, 500)
        pump()
        dlg.resize(1100, 700)
        pump()
        assert not bad, "; ".join(bad[:3])

    with step("Health: Maps/Database panes can be resized") as s:
        for key in ("maps", "db"):
            sp = dlg._content[key].findChildren(QSplitter)
            assert sp, f"no splitter in {key}"
            sizes = sp[0].sizes()
            assert sizes[0] >= max(sizes[1:]), f"{key}: list pane is not the biggest {sizes}"

    with step("Health: Maps tab lists the stray files") as s:
        import mapscan
        strays = {r.filename for r in rep.maps.rows if r.status == mapscan.STRAY}
        if read_only:
            s.note = f"{len(strays)} stray file(s) in your project"
        else:
            assert "Map001 - Copy.json" in strays and "Leftover.json" in strays, strays
            s.note = f"{len(strays)} stray file(s)"

    with step("Health: 'Check all tabs' and 'Refresh' do not break") as s:
        dlg.check_all()
        wait_idle(win)
        dlg._refresh_current()
        wait_idle(win)
        assert set(health.SECTIONS) <= rep.done

    if not read_only:
        with step("move stray files to data/_unused, rescan, undo") as s:
            rel = ["data/Map001 - Copy.json", "data/Leftover.json"]
            dlg._finish(("move_stray", rel))
            action = dlg.action
            assert action and action[0] == "move_stray", action
            dlg.close()
            t = time.monotonic()
            win.handle_health_action(action)
            wait_idle(win)
            for r in rel:
                assert not (game / r).exists(), f"{r} was not moved"
                assert (game / "data/_unused" / Path(r).name).is_file()
            assert win.table.rowCount() == len(win.shown)
            s.note = f"move + rescan {time.monotonic() - t:.1f}s"
            win.undo()
            wait_idle(win)
            for r in rel:
                assert (game / r).is_file(), f"{r} was not restored by undo"

        with step("Health through the main window (cached results)") as s:
            real_exec = gui.HealthDialog.exec

            def fake_exec(self):
                self.check_all()
                wait_idle(win)
                return QDialog.Accepted
            gui.HealthDialog.exec = fake_exec
            try:
                win.health_check()
                pump()
                assert win._health is not None and win._health.done
            finally:
                gui.HealthDialog.exec = real_exec

        with step("move unused assets and undo") as s:
            n_unused = sum(1 for a in win.assets if a["status"] == "unused")
            # Same action the menu runs, without its confirmation box:
            from rename_engine import move_to_unused
            rels = [f for a in win.assets if a["status"] == "unused" for f in a["files"]][:200]
            n, _ = move_to_unused(game, rels, False, lambda *_: None)
            win.refresh()
            wait_idle(win)
            win.undo()
            wait_idle(win)
            s.note = f"{n_unused} unused, moved {n}, undone"

    with step("'work on a copy' notice builds and can be cancelled") as s:
        gui.MainWindow._ack_backup_notice = real_ack
        real_exec = QMessageBox.exec
        QMessageBox.exec = lambda self: 0              # nobody clicks: counts as cancel
        try:
            win.settings.remove("ack_backup")
            assert win._ack_backup_notice() is False
        finally:
            QMessageBox.exec = real_exec
        s.note = f"asked {len(acks)} time(s) during the run"

    with step("empty tabs show an explanation") as s:
        from fit_table import FitTable
        empties = []
        dlg.show()                         # it was closed by the stray-file step
        pump()
        for i in range(dlg.tabs.count()):
            dlg.tabs.setCurrentIndex(i)
            pump()
            for t in dlg.tabs.widget(i).findChildren(FitTable):
                if t.isVisible() and t.rowCount() == 0:
                    lab = t._empty_label
                    assert lab is not None and lab.isVisible() and lab.text(), \
                        f"{dlg.tabs.tabText(i)}: empty table without message"
                    empties.append(dlg.tabs.tabText(i))
        s.note = f"{len(empties)} empty table(s) explained"
        if not read_only:
            assert empties, "the fake project has empty tabs (Naming, Plugin files): none was explained"

    with step("loop editor opens, draws and plays") as s:
        import audiodecode
        missing = audiodecode.missing_dependencies()
        if missing:
            s.note = ("SKIPPED: " + " and ".join(missing)
                      + " not installed (python -m pip install -r requirements-audio.txt)")
        else:
            import numpy as np
            import soundfile
            import loop_editor_gui

            def open_and_wait(path, title):
                d = loop_editor_gui.open_loop_editor(path, title, win)
                assert d is not None, f"the loop editor refused {path.name}"
                t0 = time.monotonic()
                while not (d.ready or d.error):
                    APP.processEvents()
                    time.sleep(0.01)
                    if time.monotonic() - t0 > 30:
                        raise TimeoutError("decoding did not finish within 30 s")
                assert not d.error, d.error
                pump()
                return d

            def painted_colours(d):
                img = d.view.grab().toImage()
                return len({img.pixelColor(x, y).rgb()
                            for x in range(0, img.width(), 5) for y in range(0, img.height(), 5)})

            tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(44100 * 3) / 44100.0)).astype("float32")
            ogg = tmp / "loop_smoke_tone.ogg"
            soundfile.write(str(ogg), np.column_stack([tone, tone]), 44100, format="OGG", subtype="VORBIS")
            dlg = open_and_wait(ogg, "Smoke tone")
            assert dlg._audio.frames > 100000 and dlg._audio.sample_rate == 44100, dlg._audio.frames
            assert dlg._loop.status == "none", dlg._loop.status
            assert painted_colours(dlg) > 3, "the waveform view is blank"
            dlg.view.zoom(0.05)                      # far in: the single-sample drawing path
            dlg.view.set_view(1000, 40)
            pump()
            assert painted_colours(dlg) > 3, "zoomed waveform is blank"
            dlg.view.fit()
            from PySide6.QtMultimedia import QMediaDevices
            has_device = not QMediaDevices.defaultAudioOutput().isNull()
            played = "playback not tested (no sound output device)"
            if has_device:
                assert dlg.play(0), "playback did not start: " + dlg.hint.text()
                t0 = time.monotonic()
                while time.monotonic() - t0 < 0.5:
                    APP.processEvents()
                    time.sleep(0.01)
                assert dlg.is_playing(), "playback stopped by itself: " + dlg.playback_report()
                assert dlg._head > 0, "the play cursor did not move: " + dlg.playback_report()
                played = f"played 0.5 s, cursor at sample {dlg._head} [{dlg.playback_report()}]"
                first_sink = dlg._sink
                dlg.play(100000)                     # moving the cursor while playing: same sink, no overlap
                t0 = time.monotonic()
                while time.monotonic() - t0 < 0.3:
                    APP.processEvents()
                    time.sleep(0.01)
                assert dlg._sink is first_sink, "a new sound output was made for a simple restart"
                assert dlg.is_playing(), "playback stopped after moving the cursor: " + dlg.playback_report()
                assert 100000 <= dlg._head < 100000 + 44100 * 3 // 10, \
                    f"the cursor is at {dlg._head} after restarting at 100000: " + dlg.playback_report()
                dlg.play_seam()
                pump()
                dlg.stop()
            dlg.close()
            notes = [played]
            if args.audio:                           # timing of a real file: where does the time go?
                real = Path(args.audio)
                t0 = time.monotonic()
                d3 = open_and_wait(real, real.name)
                t_open = time.monotonic() - t0
                t0 = time.monotonic()
                for _ in range(5):
                    d3.view.fit()
                    d3.view.grab()
                t_fit = (time.monotonic() - t0) / 5
                d3.view.zoom(0.05)
                t0 = time.monotonic()
                for _ in range(5):
                    d3.view.grab()
                t_zoom = (time.monotonic() - t0) / 5
                notes.append(f"{real.name}: {d3._audio.frames} frames, {d3._audio.sample_rate} Hz; "
                             f"open+decode+pyramid {t_open:.2f}s, paint fit {t_fit * 1000:.0f} ms, "
                             f"paint zoomed {t_zoom * 1000:.0f} ms; engine reads "
                             f"{d3._loop.start}..{d3._loop.end} ({d3._loop.status})")
                d3.close()
            tagged = HERE.parent / "tests" / "data" / "loop_tagged.ogg"
            if tagged.is_file():                     # a file with real loop tags (present in a git checkout)
                d2 = open_and_wait(tagged, "Tagged")
                lp = d2._loop
                assert (lp.start, lp.end, lp.status) == (22050, 66150, "complete"), lp
                assert d2._tags.start == 22050 and d2._tags.length == 44100
                assert painted_colours(d2) > 3
                d2.close()
                notes.append("tags read: LOOPSTART 22050, LOOPLENGTH 44100")
            s.note = "; ".join(notes)

    with step("loop editor edits and saves tags (on a throw-away copy)") as s:
        import audiodecode
        missing = audiodecode.missing_dependencies()
        if missing:
            s.note = "SKIPPED: " + " and ".join(missing) + " not installed"
        else:
            import numpy as np
            import soundfile
            import enginetags
            import history
            import loop_editor_gui
            import oggmeta
            from rename_engine import describe_last_undoable, undo_last

            lg = tmp / "loop_game"                    # never the user's own project
            (lg / "audio" / "bgm").mkdir(parents=True)
            t = np.arange(44100 * 4) / 44100.0
            wave = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")
            ogg = lg / "audio" / "bgm" / "EditMe.ogg"
            soundfile.write(str(ogg), np.column_stack([wave, wave]), 44100, format="OGG", subtype="VORBIS")
            original = ogg.read_bytes()
            saved_calls = []
            d = loop_editor_gui.open_loop_editor(ogg, "EditMe", win, game=lg,
                                                 on_saved=lambda: saved_calls.append(1))
            assert d is not None, "the editor did not open"
            t0 = time.monotonic()
            while not (d.ready or d.error):
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 30, "decoding did not finish"
            assert not d.error, d.error
            assert not d.dirty and not d.btn_save.isEnabled(), "a fresh editor must have nothing to save"

            assert d.set_loop_points(22050, 66150) and d.dirty and d.btn_save.isEnabled()
            assert d.ed["start"].text() == "22050" and d.ed["length"].text() == "44100", \
                (d.ed["start"].text(), d.ed["length"].text())
            d.ed["start"].setText("0.75 s")             # typed seconds: 0.75 * 44100 = 33075
            d._on_field("start")
            assert d.loop_points() == (33075, 66150), d.loop_points()
            assert d.ed["start"].text() == "33075", "the typed value was not shown as samples"
            d.ed["length"].setText("1:00")             # a minute: past the end of the file, refused
            d._on_field("length")
            assert d.loop_points() == (33075, 66150), "a loop beyond the file was accepted"
            d.view.fit()
            pump()
            x = d.view.x_of(33075)
            assert d.view._marker_at(x) == "start" and d.view._marker_at(x + 3) == "start" \
                and d.view._marker_at(d.view.x_of(100000)) is None, "marker hit-test"
            d.chk_snap.setChecked(True)
            snapped = d._snap(30001)
            assert abs(snapped - 30001) <= int(44100 * 0.02), snapped
            if snapped != 30001:
                chan = d._audio.samples.mean(axis=1)
                assert chan[snapped - 1] < 0 <= chan[snapped], "snap did not land on a rising zero crossing"
            d._on_marker_dragged("end", 80000)
            d._on_marker_released()
            ls, le = d.loop_points()
            assert ls == 33075 and abs(le - 80000) <= 882, (ls, le)
            d.revert()
            assert not d.dirty and d.loop_points() == (0, d._audio.frames), "Revert did not undo the edit"

            assert d.set_loop_points(22050, 66150)
            assert d.save_tags(ask=False), "saving failed"
            assert saved_calls == [1], "the main window was not told"
            eng = enginetags.read_engine_tags_from_file(ogg)
            assert (eng.loop_start, eng.loop_length) == (22050, 44100), eng
            tags = oggmeta.loop_tags(oggmeta.read_ogg_info(ogg))
            assert (tags.start, tags.length) == (22050, 44100), tags
            assert not d.dirty and d._loop.status == "complete", d._loop
            back, rate = soundfile.read(str(ogg))
            before, _ = soundfile.read(str(lg / ".asset_tools" / "backups"
                                           / history.last_undoable(lg)["id"] / "data" / "audio" / "bgm" / "EditMe.ogg"))
            assert rate == 44100 and np.array_equal(back, before), "the sound changed when tags were saved"
            assert describe_last_undoable(lg) == "loop tags of EditMe.ogg", describe_last_undoable(lg)
            assert undo_last(lg, lambda *_: None), "undo failed"
            assert ogg.read_bytes() == original, "undo did not restore the file"
            d.discard_changes()
            d.close()
            s.note = "typed values, snap, drag, revert, save (checked with the game's own reader), undo"

    with step("loop editor beat grid, metronome and bars") as s:
        import audiodecode
        missing = audiodecode.missing_dependencies()
        if missing:
            s.note = "SKIPPED: " + " and ".join(missing) + " not installed"
        else:
            import numpy as np
            import soundfile
            import beatgrid
            import gridstore
            import loop_editor_gui

            gg = tmp / "grid_game"                    # never the user's own project
            (gg / "audio" / "bgm").mkdir(parents=True)
            t = np.arange(44100 * 4) / 44100.0
            wave = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")
            ogg = gg / "audio" / "bgm" / "Beat.ogg"
            soundfile.write(str(ogg), np.column_stack([wave, wave]), 44100, format="OGG", subtype="VORBIS")

            def open_editor():
                d = loop_editor_gui.open_loop_editor(ogg, "Beat", win, game=gg)
                assert d is not None, "the editor did not open"
                t0 = time.monotonic()
                while not (d.ready or d.error):
                    APP.processEvents()
                    time.sleep(0.01)
                    assert time.monotonic() - t0 < 30, "decoding did not finish"
                assert not d.error, d.error
                return d

            d = open_editor()
            assert d.grid is None and not d.chk_metro.isEnabled(), "a fresh song must have no grid"
            d.view.fit()
            pump()
            before = d.view.grab().toImage()
            d.ed_bpm.setText("120")
            d.ed_offset.setText("0")
            d._on_grid_fields()
            assert d.grid == beatgrid.Grid(120.0, 0, 4), d.grid
            assert d.chk_grid.isChecked() and d.chk_metro.isEnabled()
            pump()
            assert d.view.grab().toImage() != before, "the beat grid was not drawn"
            saved = gridstore.load_grid(gg, "audio/bgm/Beat.ogg")
            assert saved == d.grid, saved

            d.ed_bpm.setText("abc")                    # nonsense is refused and the grid stays
            d._on_grid_fields()
            assert d.grid == beatgrid.Grid(120.0, 0, 4) and d.ed_bpm.text() == "120", d.ed_bpm.text()
            d.ed_bpm.setText("1000")
            d._on_grid_fields()
            assert d.grid.bpm == 120.0, "a tempo of 1000 BPM was accepted"

            d.cmb_gridsnap.setCurrentIndex(1)           # beat: 22050 frames apart
            assert d._snap(30000) == 22050 and d._snap(40000) == 44100, (d._snap(30000), d._snap(40000))
            d.cmb_gridsnap.setCurrentIndex(2)           # bar: 88200 frames apart
            assert d._snap(30000) == 0 and d._snap(60000) == 88200, (d._snap(30000), d._snap(60000))
            d.cmb_gridsnap.setCurrentIndex(0)

            assert d.set_loop_points(22050, 44100)
            d.spin_bars.setValue(1.0)
            d._apply_bars()
            assert d.loop_points() == (22050, 22050 + 88200), d.loop_points()
            d.spin_bars.setValue(8.0)                   # 8 bars = 705600 frames: beyond this 4 s file
            d._apply_bars()
            assert d.loop_points() == (22050, 22050 + 88200), "a loop beyond the file was accepted"

            assert d.set_loop_points(0, 88200)
            d.spin_bars.setValue(2.0)
            d._bpm_from_loop()                          # 2 bars in 2 s at 4 beats = 240 BPM
            assert d.grid.bpm == 240.0 and d.grid.offset == 0, d.grid

            d.view.set_cursor(0)
            d._head = 30000
            d._offset_from_cursor()
            assert d.grid.offset == 30000 and d.grid.bpm == 240.0, d.grid

            d.chk_metro.setChecked(True)
            assert d.play(0), "playback did not start: " + d.hint.text()
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.4:
                APP.processEvents()
                time.sleep(0.01)
            assert d.is_playing(), "playback stopped with the metronome on: " + d.playback_report()
            assert d._dev.metronome is d._metro and d._metro.active, "the metronome is not in the sound"
            d.chk_metro.setChecked(False)
            APP.processEvents()
            assert not d._metro.active
            d.stop()
            d.discard_changes()
            d.close()

            d2 = open_editor()                          # the grid comes back when the song is opened again
            assert d2.grid == beatgrid.Grid(240.0, 30000, 4), d2.grid
            assert d2.ed_bpm.text() == "240" and d2.ed_offset.text() == "30000", (d2.ed_bpm.text(), d2.ed_offset.text())
            d2.ed_bpm.setText("")                       # clearing the BPM forgets the grid
            d2._on_grid_fields()
            assert d2.grid is None and gridstore.load_grid(gg, "audio/bgm/Beat.ogg") is None
            d2.discard_changes()
            d2.close()
            s.note = "grid drawn and saved, snap to beat/bar, loop in bars, BPM from loop, metronome playing, grid restored"

    with step("loop editor suggests loops") as s:
        import audiodecode
        missing = audiodecode.missing_dependencies()
        if missing:
            s.note = "SKIPPED: " + " and ".join(missing) + " not installed"
        else:
            import numpy as np
            import soundfile
            import loop_editor_gui

            sg = tmp / "suggest_game"                 # never the user's own project
            (sg / "audio" / "bgm").mkdir(parents=True)
            rate = 22050
            pat = np.zeros(4 * rate)
            for k, f in enumerate([220, 277, 330, 440, 330, 277, 247, 196]):
                a = int(k * 0.5 * rate)
                seg = np.arange(int(0.5 * rate)) / rate
                pat[a:a + len(seg)] += np.sin(2 * np.pi * f * seg) * np.exp(-seg * 4)
            rng = np.random.default_rng(1)
            song = np.concatenate([rng.normal(0, 0.05, 3 * rate), np.tile(pat, 4),
                                   rng.normal(0, 0.05, 2 * rate)]).astype("float32") * 0.5
            ogg = sg / "audio" / "bgm" / "Pattern.ogg"
            soundfile.write(str(ogg), np.column_stack([song, song]), rate, format="OGG", subtype="VORBIS")
            d = loop_editor_gui.open_loop_editor(ogg, "Pattern", win, game=sg)
            assert d is not None, "the editor did not open"
            t0 = time.monotonic()
            while not (d.ready or d.error):
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 30, "decoding did not finish"
            assert not d.error, d.error
            dlg = d.suggest_loops()
            assert dlg is not None and dlg.isVisible(), "the suggestion window did not open"
            dlg.spin_min.setValue(3.0)
            dlg.search()
            t0 = time.monotonic()
            while dlg.searching:
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 60, "the search did not finish"
            assert dlg.candidates and dlg.table.rowCount() == len(dlg.candidates), dlg.status.text()
            top = dlg.candidates[0]
            rem = (top.length / rate) % 4.0
            assert min(rem, 4.0 - rem) < 0.05, top
            assert dlg.selected() is top, "the best suggestion was not selected"
            assert dlg.use_selected(hear=False)
            assert d.loop_points() == (top.start, top.end) and d.dirty, d.loop_points()
            assert dlg.use_selected(hear=True)
            pump()
            assert d.playback_report() is not None
            d.discard_changes()
            dlg.cmb_mode.setCurrentIndex(1)           # keep the current start: only that region is searched
            dlg.search()
            t0 = time.monotonic()
            while dlg.searching:
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 60, "the second search did not finish"
            dlg.close()
            d.close()
            s.note = "window opens, search runs in the background, a suggestion is used and heard, nothing saved"

    with step("loop editor seam view") as s:
        import audiodecode
        missing = audiodecode.missing_dependencies()
        if missing:
            s.note = "SKIPPED: " + " and ".join(missing) + " not installed"
        else:
            import numpy as np
            import soundfile
            import loopmath
            import loop_editor_gui

            vg = tmp / "seam_game"                    # never the user's own project
            (vg / "audio" / "bgm").mkdir(parents=True)
            rate = 22050
            t = np.arange(rate * 6) / rate
            wave = (0.3 * np.sin(2 * np.pi * 220 * t) * np.exp(-(t % 2.0) * 2)).astype("float32")
            ogg = vg / "audio" / "bgm" / "Seam.ogg"
            soundfile.write(str(ogg), np.column_stack([wave, wave]), rate, format="OGG", subtype="VORBIS")
            d = loop_editor_gui.open_loop_editor(ogg, "Seam", win, game=vg)
            assert d is not None, "the editor did not open"
            t0 = time.monotonic()
            while not (d.ready or d.error):
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 30, "decoding did not finish"
            assert not d.error, d.error
            assert d.set_loop_points(rate, rate * 3)          # loop 1 s .. 3 s; the file goes on to 6 s
            d.view.fit()
            pump()
            plain = d.view.grab().toImage()
            total_before = d.view.total()
            assert total_before == rate * 6, total_before
            d.view.set_view(rate * 2, rate * 2)                                # looking at the loop end
            keep = d.view.view_range()
            d.chk_seamview.setChecked(True)
            pump()
            assert d.view.seam_view
            assert d.view.total() == rate * 3 + rate * 2, d.view.total()      # up to the end + the loop's 2 s
            assert d.view.view_range() == keep, (d.view.view_range(), keep)   # the view did not move
            d.chk_seamview.setChecked(False)
            pump()
            assert d.view.view_range() == keep, "switching it off moved the view"
            d.chk_seamview.setChecked(True)
            pump()
            assert d.view.view_range() == keep, "switching it on again moved the view"
            assert d.view.grab().toImage() != plain, "the seam view was not drawn"
            w = d.view.width()
            x_end = d.view.x_of(rate * 3)
            assert 0 < x_end < w, x_end
            right = d.view.frame_at(min(x_end + 40, w - 1))                   # a place after the jump
            assert rate <= right < rate * 3, f"after the jump the file's own start is meant, got {right}"
            left = d.view.frame_at(max(x_end - 40, 0))
            assert left < rate * 3 and left > rate, left
            d.view.seek_requested.emit(right)                                 # clicking there places the cursor in the loop
            pump()
            assert d._head == right, (d._head, right)
            d.chk_seamview.setChecked(False)
            pump()
            assert not d.view.seam_view and d.view.total() == rate * 6
            d._zoom_to_seam()
            first, n = d.view.view_range()
            assert abs((first + n / 2) - rate * 3) < rate * 0.2, (first, n)
            # a loop that ends where the file ends: the orange part would be off-screen, so the view scrolls to it
            last = d._audio.frames
            assert d.set_loop_points(rate, last)
            d.view.set_view(last - rate * 2, rate * 2)
            d.chk_seamview.setChecked(True)
            pump()
            assert d.view.x_of(last) < d.view.width() * 0.9, "the part after the jump is out of sight"
            assert d.view.view_range()[1] == rate * 2
            # zoomed in far with the seam view on: the view must not follow the cursor (it used to scroll on
            # every tick after the jump and starve the sound), and a repaint must be cheap
            d.view.set_view(last - 220, 440)
            d.chk_seamview.setChecked(True)
            pump()
            keep = d.view.view_range()
            d._head = rate + 10                                  # as if the playback had just jumped back
            d._keep_cursor_visible()
            assert d.view.view_range() == keep, "the seam view followed the cursor"
            d.view.set_cursor(d._head)
            t_paint = time.perf_counter()
            for _ in range(30):
                d.view.repaint()
            per = (time.perf_counter() - t_paint) / 30 * 1000
            assert per < 60, f"a repaint of the zoomed seam view takes {per:.0f} ms"
            d.chk_seamview.setChecked(False)
            # layout: Revert and Save at the lower right, the tools apart from the player buttons
            from PySide6.QtCore import QPoint
            def where(w):
                return w.mapTo(d, QPoint(0, 0))
            assert where(d.btn_save).y() > where(d.ed["start"]).y(), "Save tags is not below the loop points"
            assert where(d.btn_revert).x() < where(d.btn_save).x() and where(d.btn_save).x() > d.width() // 2
            assert where(d.btn_suggest).y() > where(d.btn_play).y() + d.btn_play.height(), "Suggest loops is not apart from the player"
            assert d.btn_suggest.parentWidget() is not d.btn_play.parentWidget(), "tools and player share a box"
            d.discard_changes()
            d.close()
            s.note = f"repaint {per:.1f} ms, view stays put when switched on/off and while playing, timeline = file up to the loop end + the loop's beginning, clicks map to the right sample"

    with step("loop editor pan and pitch") as s:
        import audiodecode
        missing = audiodecode.missing_dependencies()
        if missing:
            s.note = "SKIPPED: " + " and ".join(missing) + " not installed"
        else:
            import numpy as np
            import soundfile
            import loop_editor_gui
            from PySide6.QtMultimedia import QMediaDevices

            pg = tmp / "fx_game"                      # never the user's own project
            (pg / "audio" / "bgm").mkdir(parents=True)
            rate = 44100
            tone = (0.3 * np.sin(2 * np.pi * 330 * np.arange(rate * 4) / rate)).astype("float32")
            ogg = pg / "audio" / "bgm" / "Fx.ogg"
            soundfile.write(str(ogg), np.column_stack([tone, tone]), rate, format="OGG", subtype="VORBIS")
            d = loop_editor_gui.open_loop_editor(ogg, "Fx", win, game=pg)
            assert d is not None, "the editor did not open"
            t0 = time.monotonic()
            while not (d.ready or d.error):
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 30, "decoding did not finish"
            assert not d.error, d.error
            assert d.sl_pitch.value() == 100 and d.sl_pan.value() == 0 and d.sl_pan.isEnabled()
            d.sl_pitch.setValue(150)
            d.sl_pan.setValue(-60)
            assert d.lbl_pitch.text() == "Pitch 150" and d.lbl_pan.text() == "Pan -60"
            note = "sliders and labels work"
            if not QMediaDevices.defaultAudioOutput().isNull():
                assert d.play(0), "playback did not start: " + d.hint.text()
                t0 = time.monotonic()
                while time.monotonic() - t0 < 0.5:
                    APP.processEvents()
                    time.sleep(0.01)
                assert d.is_playing(), "playback stopped by itself: " + d.playback_report()
                assert d._head > 26000, f"pitch 150 should run faster than normal, cursor at {d._head}: " + d.playback_report()
                d.sl_pitch.setValue(70)               # changing the pitch while playing carries on from here
                t0 = time.monotonic()
                while time.monotonic() - t0 < 0.4:
                    APP.processEvents()
                    time.sleep(0.01)
                assert d.is_playing(), "playback stopped after a pitch change: " + d.playback_report()
                assert d._run_rate == 0.7, d._run_rate
                d.reset_pitch_pan()
                pump()
                d.stop()
                note += f", played at pitch 150 (cursor {d._head}) and changed the pitch while playing"
            else:
                note += " (playback not tested: no sound output device)"
            d.close()
            s.note = note

    with step("loop editor BPM finder") as s:
        import audiodecode
        missing = audiodecode.missing_dependencies()
        if missing:
            s.note = "SKIPPED: " + " and ".join(missing) + " not installed"
        else:
            import numpy as np
            import soundfile
            import loop_editor_gui

            bg = tmp / "bpm_game"                     # never the user's own project
            (bg / "audio" / "bgm").mkdir(parents=True)
            rate = 22050
            rng = np.random.default_rng(3)
            n = rate * 30
            x = rng.normal(0, 0.02, n).astype("float32")
            beat = 0.5                                # 120 BPM, first beat at 0.4 s
            seg = np.arange(int(0.12 * rate)) / rate
            for k in range(58):
                t0 = int((0.4 + k * beat) * rate)
                if t0 + len(seg) >= n:
                    break
                strong = k % 4 == 0
                x[t0:t0 + len(seg)] += (0.9 if strong else 0.5) * np.sin(2 * np.pi * (60 if strong else 90) * seg) * np.exp(-seg * 30)
            ogg = bg / "audio" / "bgm" / "Beat.ogg"
            soundfile.write(str(ogg), np.column_stack([x, x]), rate, format="OGG", subtype="VORBIS")
            d = loop_editor_gui.open_loop_editor(ogg, "Beat", win, game=bg)
            assert d is not None, "the editor did not open"
            t0 = time.monotonic()
            while not (d.ready or d.error):
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 30, "decoding did not finish"
            assert not d.error, d.error
            dlg = d.bpm_finder()
            assert dlg is not None and dlg.isVisible(), "the BPM finder did not open"
            assert dlg._loop_part() is None, "a file without a loop has no loop part"
            dlg.chk_loop_only.setChecked(True)
            dlg.detect()                              # no loop: it must say so and not start
            assert not dlg.searching and "no loop" in dlg.status.text(), dlg.status.text()
            assert d.set_loop_points(rate * 2, rate * 8)
            assert dlg._loop_part() == (rate * 2, rate * 8), "the finder must see the loop as it is now"
            assert d.set_loop_points(rate * 3, rate * 9)
            assert dlg._loop_part() == (rate * 3, rate * 9), "the finder kept the loop it was opened with"
            d.discard_changes()
            dlg.chk_loop_only.setChecked(False)
            print("    (finder opened, loop part follows the editor; detecting)", flush=True)
            dlg.detect()
            t0 = time.monotonic()
            while dlg.searching:
                APP.processEvents()
                time.sleep(0.01)
                assert time.monotonic() - t0 < 60, "the detection did not finish"
            assert dlg.tempos and dlg.table.rowCount() == len(dlg.tempos), dlg.status.text()
            top = dlg.tempos[0]
            assert abs(top.bpm - 120.0) < 0.2, top
            err = ((top.offset / rate - 0.4) + 1.0) % 2.0 - 1.0          # to the bar line, bars are 2 s
            assert abs(err) < 0.05, (top, err)
            assert dlg.selected() is top
            assert dlg.use_selected(with_offset=True)
            assert d.grid is not None and abs(d.grid.bpm - top.bpm) < 0.01 and d.grid.offset == top.offset, d.grid
            assert d.chk_grid.isChecked() and d.ed_bpm.text() == f"{top.bpm:g}", d.ed_bpm.text()
            before = d.grid.offset
            for k in range(8):                        # tap 100 BPM with a clock we control
                dlg.tap(10.0 + k * 0.6)
            assert dlg.tapped_bpm is not None and abs(dlg.tapped_bpm - 100.0) < 0.1, dlg.tapped_bpm
            assert dlg.use_tapped()
            assert abs(d.grid.bpm - 100.0) < 0.1 and d.grid.offset == before, "tapping must keep the first beat"
            dlg.reset_taps()
            assert dlg.tapped_bpm is None and not dlg.btn_use_tap.isEnabled()
            dlg._playback_rate = lambda: 0.5          # as if the song played at half speed (pitch 50)
            for k in range(8):                        # the user hears and taps 100 BPM: the song is 200
                dlg.tap(50.0 + k * 0.6)
            assert abs(dlg.tapped_bpm - 200.0) < 0.2, dlg.tapped_bpm
            dlg.reset_taps()
            dlg.close()
            assert dlg._samples is None, "the closed finder still holds the song"
            d.discard_changes()
            d.close()
            s.note = f"window opens, detects {top.bpm:g} BPM with the first beat {abs(err) * 1000:.0f} ms from the truth, uses it, tap tempo works, keeps the first beat and follows the pitch"

    with step("process pool was used for the heavy scans") as s:
        s.note = f"pool started {pool['n']} time(s)"
        if not read_only and pool["n"] == 0:
            s.note += " (not used: fine on 1-core machines)"

    with step("no unexpected error boxes") as s:
        bad = [p for p in popups if p[0] in ("warning", "critical")]
        s.note = f"{len(popups)} box(es) answered automatically"
        assert not bad, f"unexpected: {bad[:3]}"

    # ------------------------------------------------------------ report
    print("\n" + "=" * 64)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, dt, note in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {dt:6.2f}s  {name}" + (f"  [{note}]" if note else ""))
    print("=" * 64)
    print(f"{len(RESULTS) - len(fails)} passed, {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    code = main()
    os._exit(code)           # skip Qt teardown ordering issues; results are already printed

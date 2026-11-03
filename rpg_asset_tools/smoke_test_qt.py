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
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", type=int, default=3000, help="extra unused pictures to create")
    ap.add_argument("--project", help="read-only timing run on this existing project")
    ap.add_argument("--show", action="store_true", help="visible windows (default: hidden)")
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

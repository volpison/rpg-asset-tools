"""
health_gui.py
-------------
The "Health check" window: Duplicates / Naming / Deploy size.

The dialog never changes the project itself. When you press an action button it
stores what you asked for in `self.action` and closes; the main window then runs
it (with a preview / confirmation / undo), exactly like a normal rename.

  ("merge", [(category, duplicate, keeper), ...])
  ("rename", [asset rows], {row_index: suggested_name})
  ("map_params", {path: category})   treat plugin parameters as asset references
  ("move_plugins", [rel path, ...])   move not-loaded plugin files to js/plugins/_unused/
  ("move_stray", [rel path, ...])     move stray map copies/backups to data/_unused/
  ("archive",)       move _unused/ folders out of the project
  ("recheck",)       run the checks again (after editing the rules file)

The Database tab is read-only apart from the "Keep" flag, which is stored in
.asset_tools/ignore.json (like the asset Keep flag) and never touches game files.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QFrame, QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

import dbscan
import mapscan
import pluginscan
import health
import usage
from annotations import Annotations
from asset_paths import CATEGORY_LABELS
from usage_gui import UsageDialog
from fit_table import FitTable
from deploy import fmt_size


def _table(headers: list[str], stretch_col: int) -> QTableWidget:
    t = FitTable(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection)
    t.setWordWrap(False)
    return t               # widths: see fit_table.FitTable (stretch_col kept for call-site compatibility)


def _split_panes(table, detail, notes) -> QSplitter:
    """Table on top (the part that matters), detail list and notes below - the user can drag
    the dividers to give the table more room."""
    sp = QSplitter(Qt.Vertical)
    sp.addWidget(table)
    sp.addWidget(detail)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(notes)
    sp.addWidget(sa)
    sp.setChildrenCollapsible(False)
    sp.setStretchFactor(0, 6)
    sp.setStretchFactor(1, 1)
    sp.setStretchFactor(2, 1)
    sp.setSizes([380, 70, 90])
    return sp


def _set_row(t: QTableWidget, r: int, values: list[str], payload=None):
    for c, v in enumerate(values):
        it = QTableWidgetItem(str(v))
        it.setToolTip(str(v))
        t.setItem(r, c, it)
    if payload is not None:
        t.item(r, 0).setData(Qt.UserRole, payload)


def _selected_rows(t: QTableWidget) -> list[int]:
    return sorted({i.row() for i in t.selectedIndexes()})


class HealthDialog(QDialog):
    """Seven tabs; each one is computed only when the user asks for it.

    `runner(work, done, title, parent, on_fail)` runs `work(log)` off the UI thread with a
    progress window (the main window provides it).  Without a runner the checks run inline.
    """
    _BUILD = {"dups": "_build_dups", "names": "_build_names", "deploy": "_build_deploy",
              "plugins": "_build_plugins", "db": "_build_db", "maps": "_build_maps",
              "pfiles": "_build_plugin_files"}
    _HELP = {
        "dups": "Finds assets with identical content. Files that share a size are read and "
                "compared, so on a big project this is the slowest check.",
        "names": "Case mismatches, spaces, non-ASCII characters and your own rules. Fast.",
        "deploy": "How big the deployed game will be, what is unused, and the largest files. Fast.",
        "plugins": "Plugin parameters that hold asset names but are not declared as such. Fast.",
        "db": "Database entries (items, skills, switches, variables ...) that nothing refers to. "
              "Reads every map and event; results are cached, so repeat runs are quicker.",
        "maps": "Which maps the player can actually reach. Reads every map; results are cached.",
        "pfiles": "Which plugin files are not loaded at all (safe to move to _unused).",
    }

    def __init__(self, game: Path, report, parent=None, runner=None, assets=None, scan=None):
        super().__init__(parent)
        self.game, self.report = Path(game), report
        self.action = None
        self._runner, self._assets, self._scan = runner, assets, scan
        self._busy = False
        self._stack: dict[str, object] = {}
        self._content: dict[str, object] = {}
        self.setWindowTitle("Health check")
        self.resize(940, 600)

        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for key in health.SECTIONS:
            st = QStackedWidget()
            st.addWidget(self._placeholder(key))
            self._stack[key] = st
            self.tabs.addTab(st, self._title(key))
        self.tabs.currentChanged.connect(lambda _i: self._update_buttons())
        lay.addWidget(self.tabs)
        self.btn_refresh = QPushButton("Refresh this tab")
        self.btn_refresh.setToolTip("Run the check of the tab you are looking at again")
        self.btn_refresh.clicked.connect(self._refresh_current)
        self.btn_all = QPushButton("Check all tabs")
        self.btn_all.setToolTip("Run every check that has not been run yet")
        self.btn_all.clicked.connect(self.check_all)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_all)
        row.addStretch()
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        for key in health.SECTIONS:
            if report.has(key):
                self._show_section(key)
        self._update_buttons()

    # ------------------------------------------------- lazy tabs
    def _can_run(self) -> bool:
        return self._assets is not None

    def _title(self, key: str) -> str:
        label = health.SECTION_LABELS[key]
        r = self.report
        if not r.has(key):
            return label
        n = {"dups": lambda: len(r.dups), "names": lambda: len(r.issues),
             "deploy": lambda: len(r.deploy.warnings) if r.deploy else 0,
             "plugins": lambda: len(r.param_suggestions), "db": lambda: r.db_attention,
             "maps": lambda: r.map_attention, "pfiles": lambda: r.plugin_files_attention}[key]()
        return f"{label} ({n})"

    def _placeholder(self, key: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        info = QLabel(f"<b>{health.SECTION_LABELS[key]}</b> has not been checked yet.<br><br>"
                      f"{self._HELP[key]}")
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignCenter)
        lay.addWidget(info)
        btn = QPushButton("Run this check")
        btn.clicked.connect(lambda _=False, k=key: self._run_keys([k]))
        btn.setEnabled(self._can_run())
        r = QHBoxLayout()
        r.addStretch()
        r.addWidget(btn)
        r.addStretch()
        lay.addLayout(r)
        lay.addStretch()
        w._run_btn = btn
        return w

    def _show_section(self, key: str):
        st = self._stack[key]
        old = self._content.pop(key, None)
        if old is not None:
            st.removeWidget(old)
            old.deleteLater()
        w = getattr(self, self._BUILD[key])()
        self._content[key] = w
        st.addWidget(w)
        st.setCurrentWidget(w)
        self.tabs.setTabText(health.SECTIONS.index(key), self._title(key))

    def _update_buttons(self):
        idle = not self._busy and self._can_run()
        cur = health.SECTIONS[max(0, min(len(health.SECTIONS) - 1, self._current_index()))]
        self.btn_refresh.setEnabled(idle)
        self.btn_refresh.setText(f"Refresh {health.SECTION_LABELS[cur].lower()}")
        self.btn_all.setEnabled(idle and any(not self.report.has(k) for k in health.SECTIONS))
        self.btn_close.setEnabled(not self._busy)
        self.tabs.setEnabled(not self._busy)    # no fix can be started while a check runs

    def _current_index(self) -> int:
        i = self.tabs.currentIndex()
        return i if isinstance(i, int) else 0

    def _refresh_current(self):
        self._run_keys([health.SECTIONS[self._current_index()]])

    def check_all(self):
        self._run_keys([k for k in health.SECTIONS if not self.report.has(k)])

    def _run_keys(self, keys):
        keys = list(keys)
        if not keys or self._busy or not self._can_run():
            return
        rep, game, assets, scan = self.report, self.game, self._assets, self._scan

        def work(log):
            health.run_sections(rep, keys, game, assets, log, scan)
            return keys

        def done(_result):
            self._busy = False
            for k in keys:
                self._show_section(k)
            self._update_buttons()

        def failed(*_a):
            self._busy = False
            self._update_buttons()

        self._busy = True
        self._update_buttons()
        title = ("Checking " + health.SECTION_LABELS[keys[0]].lower()) if len(keys) == 1 \
            else "Checking project"
        if self._runner:
            self._runner(work, done, title + "...", self, failed)
        else:
            try:
                done(work(None))
            except Exception:
                failed()
                raise

    def reject(self):
        if self._busy:                       # a check is still writing into the report
            return
        super().reject()

    # ------------------------------------------------------------ helpers
    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def _finish(self, action):
        if self._busy:                       # a check is still writing into the report
            return
        self.action = action
        self.accept()

    # ----------------------------------------------------------- duplicates
    def _build_dups(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.dup_info = QLabel("")
        self.dup_info.setWordWrap(True)
        lay.addWidget(self.dup_info)
        self.dup_table = _table(["Group", "Name", "Refs", "Size", "Role"], 1)
        lay.addWidget(self.dup_table, stretch=1)
        row = QHBoxLayout()
        b_keep = QPushButton("Make selected the keeper")
        b_keep.clicked.connect(self._dup_make_keeper)
        b_one = QPushButton("Merge selected group...")
        b_one.clicked.connect(self._dup_merge_selected)
        b_all = QPushButton("Merge all groups...")
        b_all.clicked.connect(self._dup_merge_all)
        for b in (b_keep, b_one, b_all):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self._fill_dups()
        return w

    def _fill_dups(self):
        groups = self.report.dups
        t = self.dup_table
        t.set_empty_text("No identical files found.\nEvery asset has unique content.")
        t.setRowCount(sum(len(g.members) for g in groups))
        r = 0
        for gi, g in enumerate(groups):
            for mi, m in enumerate(g.members):
                _set_row(t, r, [f"{gi + 1}", m["name"], m["ref_count"],
                                fmt_size(g.bytes_each),
                                "KEEP" if mi == 0 else "duplicate"], payload=(gi, m["name"]))
                r += 1
        if groups:
            self.dup_info.setText(
                f"{len(groups)} group(s) of identical files. Merging re-points every reference "
                f"to the KEEP asset and moves the copies into _unused/ "
                f"(frees {fmt_size(self.report.dup_freed_bytes)}; undoable). "
                f"Pick a different keeper first if you prefer another name.")
        else:
            self.dup_info.setText("No identical files found.")

    def _dup_make_keeper(self):
        rows = _selected_rows(self.dup_table)
        if len(rows) != 1:
            return
        gi, name = self.dup_table.item(rows[0], 0).data(Qt.UserRole)
        self.report.dups[gi].set_keeper(name)
        self._fill_dups()

    def _merge_text(self, merges) -> str:
        counts = {(g.category, m["name"]): m["ref_count"] for g in self.report.dups for m in g.copies}
        refs = sum(counts.get((cat, dup), 0) for cat, dup, _keep in merges)
        return (f"Merge {len(merges)} duplicate(s)?\n\n"
                f"{refs} reference(s) will be re-pointed to the kept assets and the "
                f"copies moved to _unused/ folders. You can undo it.")

    def _dup_merge_selected(self):
        rows = _selected_rows(self.dup_table)
        if not rows:
            return
        gi = self.dup_table.item(rows[0], 0).data(Qt.UserRole)[0]
        merges = self.report.dups[gi].merges()
        if merges and self._confirm("Merge duplicates", self._merge_text(merges)):
            self._finish(("merge", merges))

    def _dup_merge_all(self):
        merges = [m for g in self.report.dups for m in g.merges()]
        if merges and self._confirm("Merge duplicates", self._merge_text(merges)):
            self._finish(("merge", merges))

    # --------------------------------------------------------------- naming
    def _build_names(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        rp = self.report.rules_path
        self.name_info = QLabel(
            "Case mismatches, spaces, non-ASCII characters and your own rules. "
            f"Rules file: {rp if rp else '(none)'}")
        self.name_info.setWordWrap(True)
        lay.addWidget(self.name_info)
        self.name_table = _table(["Category", "Name", "Problem", "Suggested name"], 2)
        issues = self.report.issues
        self.name_table.set_empty_text("No naming problems found.\nNames follow the rules in the rules file.")
        self.name_table.setRowCount(len(issues))
        for r, i in enumerate(issues):
            _set_row(self.name_table, r,
                     [CATEGORY_LABELS.get(i.category, i.category), i.name,
                      "; ".join(i.problems), i.suggestion or "(none - rename by hand)"],
                     payload=r)
        lay.addWidget(self.name_table, stretch=1)
        row = QHBoxLayout()
        b_sel = QPushButton("Fix selected...")
        b_sel.clicked.connect(self._names_fix_selected)
        b_all = QPushButton("Fix all with suggestions...")
        b_all.clicked.connect(self._names_fix_all)
        b_rules = QPushButton("Edit rules file")
        b_rules.clicked.connect(self._names_open_rules)
        b_again = QPushButton("Re-check")
        b_again.setToolTip("Run the naming check again after editing the rules file")
        b_again.clicked.connect(lambda: self._run_keys(["names"]))
        for b in (b_sel, b_all, b_rules, b_again):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        return w

    def _names_fix(self, indices: list[int]):
        fixable = [i for i in indices if self.report.issues[i].suggestion]
        if not fixable:
            QMessageBox.information(
                self, "Naming", "None of these has an automatic suggestion. "
                                "Select the asset in the main list and press F2 to rename it by hand.")
            return
        rows = [self.report.issues[i].asset for i in fixable]
        presets = {k: self.report.issues[i].suggestion for k, i in enumerate(fixable)}
        self._finish(("rename", rows, presets))

    def _names_fix_selected(self):
        self._names_fix(_selected_rows(self.name_table))

    def _names_fix_all(self):
        self._names_fix(list(range(len(self.report.issues))))

    def _names_open_rules(self):
        if self.report.rules_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report.rules_path)))

    # --------------------------------------------------------------- deploy
    def _build_deploy(self) -> QWidget:
        d = self.report.deploy
        w = QWidget()
        lay = QVBoxLayout(w)
        self.deploy_info = QLabel(
            f"<b>Assets: {fmt_size(d.total_bytes)}</b> &nbsp;|&nbsp; "
            f"Unused: {fmt_size(d.unused_bytes)} "
            f"({d.unused_bytes * 100 // d.total_bytes if d.total_bytes else 0}%) &nbsp;|&nbsp; "
            f"Duplicate copies: {fmt_size(self.report.dup_freed_bytes)}<br>"
            f"<small>Only img/, audio/, effects/ and movies/ assets are counted - "
            f"js/ and data/ are not.</small>")
        lay.addWidget(self.deploy_info)

        self.cat_table = _table(["Category", "Files", "Size", "Unused size"], 0)
        self.cat_table.setRowCount(len(d.categories))
        for r, c in enumerate(d.categories):
            _set_row(self.cat_table, r, [CATEGORY_LABELS.get(c["category"], c["category"]),
                                         c["files"], fmt_size(c["bytes"]),
                                         fmt_size(c["unused_bytes"]) if c["unused_bytes"] else "-"])
        lay.addWidget(self.cat_table, stretch=1)

        lay.addWidget(QLabel("Things to check before building:"))
        self.checks = QListWidget()
        for text in self.check_lines():
            self.checks.addItem(text)
        if self.checks.count() == 0:
            self.checks.addItem("Nothing to worry about.")
        lay.addWidget(self.checks, stretch=1)

        lay.addWidget(QLabel("Largest files:"))
        self.large_table = _table(["File", "Size"], 0)
        self.large_table.setRowCount(len(d.largest))
        for r, (rel, size) in enumerate(d.largest):
            _set_row(self.large_table, r, [rel, fmt_size(size)])
        lay.addWidget(self.large_table, stretch=1)

        row = QHBoxLayout()
        self.btn_archive = QPushButton("Move _unused/ folders out of the project...")
        self.btn_archive.setEnabled(bool(d.unused_dirs))
        self.btn_archive.clicked.connect(self._archive)
        row.addWidget(self.btn_archive)
        row.addStretch()
        lay.addLayout(row)
        return w

    # -------------------------------------------------------------- plugins
    def _build_plugins(self) -> QWidget:
        r = self.report
        w = QWidget()
        lay = QVBoxLayout(w)
        self.plugin_info = QLabel(
            f"<b>{r.plugin_declared}</b> plugin parameter(s) / command argument(s) are recognised as "
            f"asset names ({r.plugins_with_header} plugin header(s) read"
            + (f", {r.plugin_user_mapped} from your own mapping" if r.plugin_user_mapped else "")
            + ").<br>Below: parameters that are <i>not</i> declared as files but whose value is exactly "
              "the name of an existing asset. Treating them as references makes them count as used "
              "and lets a rename update them.")
        self.plugin_info.setWordWrap(True)
        lay.addWidget(self.plugin_info)
        self.plugin_table = _table(["Plugin", "Parameter", "Value(s)", "Looks like", "Status"], 2)
        sug = r.param_suggestions
        self.plugin_table.set_empty_text(
            "Nothing to map.\nNo undeclared plugin parameter holds the name of an existing asset.")
        self.plugin_table.setRowCount(len(sug))
        for i, s in enumerate(sug):
            param = s.path.split(".", 1)[1] if "." in s.path else s.path
            if s.path.startswith("cmd:"):
                param = "command " + ".".join(s.path[4:].split(".")[1:])
            looks = ", ".join(CATEGORY_LABELS.get(c, c) for c in s.categories) or "?"
            status = ("ready" if s.category
                      else "ambiguous: the name exists in several folders - set it in the mapping file")
            _set_row(self.plugin_table, i,
                     [s.plugin, param, ", ".join(s.values[:4]) + (" ..." if len(s.values) > 4 else ""),
                      looks, status], payload=i)
        lay.addWidget(self.plugin_table, stretch=1)
        row = QHBoxLayout()
        b_sel = QPushButton("Treat selected as references")
        b_sel.clicked.connect(self._plugins_map_selected)
        b_all = QPushButton("Treat all ready ones as references")
        b_all.clicked.connect(self._plugins_map_all)
        b_file = QPushButton("Open mapping file")
        b_file.setToolTip("Edit .asset_tools/plugin_params.json by hand (e.g. for ambiguous names)")
        b_file.clicked.connect(self._plugins_open_file)
        for b in (b_sel, b_all, b_file):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        return w

    def _plugins_map(self, indices: list[int]):
        mapping = {self.report.param_suggestions[i].path: self.report.param_suggestions[i].category
                   for i in indices if self.report.param_suggestions[i].category}
        if not mapping:
            QMessageBox.information(
                self, "Plugin params", "Nothing to map here: these parameters are ambiguous. "
                                 "Use 'Open mapping file' to choose a folder for them.")
            return
        if self._confirm("Plugin parameters",
                         f"Treat {len(mapping)} plugin parameter(s) as asset references?\n\n"
                         f"They will count as used, and renaming those assets will also update "
                         f"the plugin settings / commands. This only stores the choice in "
                         f".asset_tools/plugin_params.json."):
            self._finish(("map_params", mapping))

    def _plugins_map_selected(self):
        self._plugins_map(_selected_rows(self.plugin_table))

    def _plugins_map_all(self):
        self._plugins_map(list(range(len(self.report.param_suggestions))))

    def _plugins_open_file(self):
        from plugin_refs import user_map_path
        p = user_map_path(self.game)
        if not p.exists():
            p.write_text('{\n  "format": 1,\n  "params": {}\n}\n', encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    # ------------------------------------------------------------- database
    DB_FILTERS = [("Needs attention", dbscan.NEEDS_ATTENTION),
                  ("Unused only", (dbscan.UNUSED,)),
                  ("Everything", None)]

    def _build_db(self) -> QWidget:
        r = self.report
        w = QWidget()
        lay = QVBoxLayout(w)
        self._ann = None
        info = QLabel(
            "Database entries that nothing refers to. Switches and variables are split further: "
            "<i>set, never checked</i> (events switch it but nothing reacts) and <i>checked, never set</i> "
            "(the condition can never change). Nothing is deleted here: RPG Maker's editor does that "
            "safely; use this list to know what to clean up.")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        self.db_type = QComboBox()
        self.db_type.addItem("All types", None)
        for t in dbscan.TYPES:
            self.db_type.addItem(dbscan.TYPE_LABELS[t], t)
        self.db_filter = QComboBox()
        for label, _ in self.DB_FILTERS:
            self.db_filter.addItem(label)
        self.db_type.currentIndexChanged.connect(self._fill_db)
        self.db_filter.currentIndexChanged.connect(self._fill_db)
        row.addWidget(self.db_type)
        row.addWidget(self.db_filter)
        self.db_summary = QLabel("")
        row.addWidget(self.db_summary, stretch=1)
        lay.addLayout(row)
        self.db_table = _table(["Type", "ID", "Name", "Status", "Why / used by"], 4)
        self.db_table.itemSelectionChanged.connect(self._db_show_refs)
        self.db_refs = QListWidget()
        self.db_caveats = QLabel("")
        self.db_caveats.setWordWrap(True)
        lay.addWidget(_split_panes(self.db_table, self.db_refs, self.db_caveats), stretch=1)
        brow = QHBoxLayout()
        b_keep = QPushButton("Keep / un-keep selected")
        b_keep.setToolTip("Flag entries you want on purpose (e.g. reserved for DLC). "
                          "They stop being reported. Stored in .asset_tools/ignore.json.")
        b_keep.clicked.connect(self._db_toggle_keep)
        b_where = QPushButton("Where is this used?")
        b_where.setToolTip("Map, event position, page and line of every reference to the selected entry")
        b_where.clicked.connect(self._db_where)
        b_csv = QPushButton("Export CSV...")
        b_csv.clicked.connect(self._db_export)
        brow.addWidget(b_where)
        brow.addWidget(b_keep)
        brow.addWidget(b_csv)
        brow.addStretch()
        lay.addLayout(brow)
        self._db_visible: list = []
        self._fill_db()
        return w

    def _db_caveat_text(self) -> str:
        sc = self.report.db_scan
        if sc is None:
            return ""
        lines = []
        for err in sc.errors:
            lines.append(f"Could not read {err}: results for it may be incomplete.")
        for typ, uses in sorted(sc.dynamic_uses.items()):
            lines.append(f"{dbscan.TYPE_LABELS[typ]}: {len(uses)} script call(s) pick ids at run time "
                         f"(e.g. {uses[0][1]}: {uses[0][2]}), so unreferenced entries show as 'possibly used'.")
        lines.extend(sc.plugin_caveats[:5])
        return "<br>".join(lines)

    def _fill_db(self):
        rows = self.report.db_rows
        typ = self.db_type.currentData()
        wanted = self.DB_FILTERS[self.db_filter.currentIndex()][1]
        self._db_visible = [x for x in rows if (typ is None or x.type == typ)
                            and (wanted is None or x.status in wanted)]
        t = self.db_table
        t.set_empty_text("Nothing to report: every database entry is used or kept."
                         if not [x for x in rows if x.status in dbscan.NEEDS_ATTENTION] and wanted
                         else "No entries match this filter.")
        t.setRowCount(len(self._db_visible))
        for i, x in enumerate(self._db_visible):
            why = x.note or (f"{x.total_refs} reference(s)" if x.total_refs else "")
            _set_row(t, i, [dbscan.TYPE_LABELS[x.type], x.id, x.name or "(no name)",
                            dbscan.STATUS_TEXT.get(x.status, x.status), why], payload=i)
        counts = {}
        for x in self._db_visible:
            counts[x.status] = counts.get(x.status, 0) + 1
        self.db_summary.setText(
            ", ".join(f"{n} {dbscan.STATUS_TEXT.get(s, s).lower()}" for s, n in sorted(counts.items()))
            or "nothing to report")
        self.db_caveats.setText(self._db_caveat_text())
        self.db_refs.clear()

    def _db_show_refs(self):
        self.db_refs.clear()
        sel = _selected_rows(self.db_table)
        if len(sel) != 1:
            return
        x = self._db_visible[sel[0]]
        if not x.used_by:
            self.db_refs.addItem("No references found.")
        for ref in x.used_by[:60]:
            tag = {"set": "sets it", "read": "checks it", "ref": "uses it"}.get(ref.kind, ref.kind)
            self.db_refs.addItem(f"{tag}: {ref.where}" + ("  (disabled plugin)" if ref.optional else ""))
        if x.total_refs > 60:
            self.db_refs.addItem(f"... and {x.total_refs - 60} more")

    def _db_where(self):
        sel = _selected_rows(self.db_table)
        if len(sel) != 1:
            QMessageBox.information(self, "Where is this used?", "Select one entry first.")
            return
        x = self._db_visible[sel[0]]
        if not x.used_by:
            QMessageBox.information(self, "Where is this used?",
                                    f"{dbscan.TYPE_LABELS[x.type][:-1] if x.type != 'switch' else 'Switch'} "
                                    f"{x.id} has no references.")
            return
        items = usage.from_db_refs(usage.Locator(self.game), x.used_by)
        title = f"{dbscan.TYPE_LABELS[x.type]}: {x.id} {x.name}".strip()
        self.usage_dialog = UsageDialog(self.game, title, items, x.total_refs, self)
        self.usage_dialog.exec()

    def _db_toggle_keep(self):
        sel = _selected_rows(self.db_table)
        if not sel:
            return
        if self._ann is None:
            self._ann = Annotations(self.game)
        picked = [self._db_visible[i] for i in sel]
        make_keep = not all(self._ann.is_flagged(x.key) for x in picked)
        for x in picked:
            self._ann.set_flagged(x.key, make_keep)
        self.report.db_rows = dbscan.classify(self.report.db_scan, self._ann)
        self._fill_db()

    def _db_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export database report", "database_report.csv",
                                              "CSV (*.csv)")
        if path:
            dbscan.export_db_csv(self._db_visible, Path(path))

    # ----------------------------------------------------------------- maps
    MAP_FILTERS = [("Needs attention", mapscan.NEEDS_ATTENTION),
                   ("Unused only", (mapscan.UNUSED,)),
                   ("Everything", None)]

    def _build_maps(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._map_ann = None
        info = QLabel(
            "Which maps can the player actually reach? Starts from the starting map and follows every "
            "Transfer Player / Set Vehicle Location / <i>reserveTransfer</i>. Assets used only by unused "
            "maps look 'used' in the asset list; they are summed below. Real maps are never deleted here: "
            "remove them in RPG Maker's editor (it keeps MapInfos consistent). <i>Stray files</i> "
            "(copies and backups such as 'Map004 - Copy.json') are not part of the game and can be "
            "moved to data/_unused/ (undoable).")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        self.map_filter = QComboBox()
        for label, _ in self.MAP_FILTERS:
            self.map_filter.addItem(label)
        self.map_filter.currentIndexChanged.connect(self._fill_maps)
        self.map_summary = QLabel("")
        row.addWidget(self.map_filter)
        row.addWidget(self.map_summary, stretch=1)
        lay.addLayout(row)
        self.map_table = _table(["ID", "Name / path", "Status", "Events", "Size", "Assets only here",
                                 "Why"], 6)
        self.map_table.itemSelectionChanged.connect(self._maps_show_detail)
        self.map_detail = QListWidget()
        self.map_freed = QLabel("")
        self.map_freed.setWordWrap(True)
        lay.addWidget(_split_panes(self.map_table, self.map_detail, self.map_freed), stretch=1)
        brow = QHBoxLayout()
        b_where = QPushButton("Who sends the player here?")
        b_where.clicked.connect(self._maps_where)
        b_keep = QPushButton("Keep / un-keep selected")
        b_keep.clicked.connect(self._maps_toggle_keep)
        b_csv = QPushButton("Export CSV...")
        b_csv.clicked.connect(self._maps_export)
        self.btn_map_stray = QPushButton("Move stray files to data/_unused/...")
        self.btn_map_stray.clicked.connect(self._maps_move_stray)
        for b in (b_where, b_keep, self.btn_map_stray, b_csv):
            brow.addWidget(b)
        brow.addStretch()
        lay.addLayout(brow)
        self._map_visible: list = []
        self._fill_maps()
        return w

    def _fill_maps(self):
        ms = self.report.maps
        rows = ms.rows if ms else []
        wanted = self.MAP_FILTERS[self.map_filter.currentIndex()][1]
        self._map_visible = [r for r in rows if wanted is None or r.status in wanted]
        t = self.map_table
        t.set_empty_text("Nothing to report: every map is reachable and no stray files were found."
                         if not [r for r in rows if r.status in mapscan.NEEDS_ATTENTION] and wanted
                         else "No maps match this filter.")
        t.setRowCount(len(self._map_visible))
        for i, r in enumerate(self._map_visible):
            _set_row(t, i, ["-" if r.filename else r.id, r.label or r.name, mapscan.STATUS_TEXT.get(r.status, r.status),
                            r.events, fmt_size(r.file_size) if r.file_size else "-",
                            f"{len(r.exclusive)} ({fmt_size(r.exclusive_bytes)})" if r.exclusive else "-",
                            r.note], payload=i)
        counts: dict = {}
        for r in self._map_visible:
            counts[r.status] = counts.get(r.status, 0) + 1
        self.map_summary.setText(", ".join(f"{n} {mapscan.STATUS_TEXT.get(s, s).lower()}"
                                           for s, n in sorted(counts.items())) or "nothing to report")
        self.map_detail.clear()
        if ms is not None:
            text = ""
            if ms.freed_assets:
                text = (f"<b>{len(ms.freed_assets)}</b> asset(s) ({fmt_size(ms.freed_bytes)}) are used only by "
                        f"unused maps; deleting those maps in the editor would leave them unused.")
            lines = [f"Could not read {e}" for e in ms.errors] + ms.caveats
            if ms.dynamic:
                lines.append(f"{len(ms.dynamic)} transfer(s) use a variable destination "
                             f"(e.g. {ms.dynamic[0][0]}): unreferenced maps show as 'possibly used'.")
            self.map_freed.setText("<br>".join([x for x in [text] + lines if x]))

    def _maps_show_detail(self):
        self.map_detail.clear()
        sel = _selected_rows(self.map_table)
        if len(sel) != 1:
            return
        r = self._map_visible[sel[0]]
        if r.outbound:
            self.map_detail.addItem("Leads to maps: " + ", ".join(map(str, r.outbound)))
        for cat, name, size in r.exclusive[:40]:
            self.map_detail.addItem(f"only used here: {CATEGORY_LABELS.get(cat, cat)}: {name} ({fmt_size(size)})")
        if not r.exclusive and not r.outbound:
            self.map_detail.addItem("No exclusive assets and no exits.")

    def _maps_where(self):
        sel = _selected_rows(self.map_table)
        if len(sel) != 1:
            QMessageBox.information(self, "Maps", "Select one map first.")
            return
        r = self._map_visible[sel[0]]
        if r.filename:
            QMessageBox.information(self, "Maps", f"{r.filename} is not a game map: nothing loads it.")
            return
        if not r.inbound:
            QMessageBox.information(self, "Maps", f"Nothing sends the player to map {r.id}.")
            return
        items = usage.from_db_refs(usage.Locator(self.game), r.inbound)
        self.usage_dialog = UsageDialog(self.game, f"Map {r.id}: {r.name}", items, len(r.inbound), self)
        self.usage_dialog.exec()

    def _maps_toggle_keep(self):
        sel = _selected_rows(self.map_table)
        if not sel:
            return
        if self._map_ann is None:
            self._map_ann = Annotations(self.game)
        picked = [self._map_visible[i] for i in sel]
        make_keep = not all(self._map_ann.is_flagged(x.key) for x in picked)
        for x in picked:
            self._map_ann.set_flagged(x.key, make_keep)
        for x in picked:                           # re-label in place: no rescan needed
            if make_keep and x.status in mapscan.NEEDS_ATTENTION:
                x._prev = (x.status, x.note)
                x.status, x.note = mapscan.KEPT, "flagged to keep"
            elif not make_keep and x.status == mapscan.KEPT:
                x.status, x.note = getattr(x, "_prev", (mapscan.UNUSED, ""))
        self._fill_maps()

    def _stray_rows(self, selected_only: bool) -> list:
        rows = self._map_visible
        if selected_only:
            sel = _selected_rows(self.map_table)
            if sel:
                rows = [self._map_visible[i] for i in sel]
        return [r for r in rows if r.status == mapscan.STRAY]

    def _maps_move_stray(self):
        rows = self._stray_rows(True)
        if not rows:
            QMessageBox.information(self, "Maps", "No stray map files here (copies or backups of maps).")
            return
        names = "\n".join(r.filename for r in rows[:15]) + (f"\n... and {len(rows) - 15} more" if len(rows) > 15 else "")
        if self._confirm("Move stray map files",
                         f"Move {len(rows)} file(s) to data/_unused/ ?\n\n{names}\n\n"
                         "The game does not load them. You can undo this."):
            self._finish(("move_stray", [f"data/{r.filename}" for r in rows]))

    def _maps_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export map report", "map_report.csv", "CSV (*.csv)")
        if path:
            mapscan.export_maps_csv(self._map_visible, Path(path))

    # ---------------------------------------------------------- plugin files
    def _build_plugin_files(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._pf_ann = None
        info = QLabel(
            "Plugin <i>files</i>. An enabled plugin is never called unused: many change the engine and "
            "nothing refers to them. What is certain: files that <b>js/plugins.js never loads</b> (safe to "
            "move out), disabled plugins (keep, or remove the entry in the Plugin Manager), and entries "
            "without a file. A plugin another plugin needs (@base) is never offered for moving.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.pf_filter = QComboBox()
        for label in ("Needs attention", "Everything"):
            self.pf_filter.addItem(label)
        self.pf_filter.currentIndexChanged.connect(self._fill_plugin_files)
        lay.addWidget(self.pf_filter)
        self.pf_table = _table(["Plugin", "Status", "Size", "Cmds called/declared", "Asset refs",
                                "Needs / needed by", "Note"], 6)
        self.pf_table.itemSelectionChanged.connect(self._pf_show_hint)
        lay.addWidget(self.pf_table, stretch=1)
        self.pf_hint = QLabel("")
        self.pf_hint.setWordWrap(True)
        lay.addWidget(self.pf_hint)
        self.pf_errors = QLabel("")
        self.pf_errors.setWordWrap(True)
        lay.addWidget(self.pf_errors)
        brow = QHBoxLayout()
        self.btn_pf_move = QPushButton("Move selected not-loaded files to js/plugins/_unused/")
        self.btn_pf_move.setToolTip("Only files js/plugins.js never loads. Undo is available.")
        self.btn_pf_move.clicked.connect(self._pf_move_selected)
        self.btn_pf_move_all = QPushButton("Move all not-loaded files")
        self.btn_pf_move_all.clicked.connect(self._pf_move_all)
        b_keep = QPushButton("Keep / un-keep selected")
        b_keep.clicked.connect(self._pf_toggle_keep)
        b_csv = QPushButton("Export CSV...")
        b_csv.clicked.connect(self._pf_export)
        for b in (self.btn_pf_move, self.btn_pf_move_all, b_keep, b_csv):
            brow.addWidget(b)
        brow.addStretch()
        lay.addLayout(brow)
        self._pf_visible: list = []
        self._fill_plugin_files()
        return w

    def _fill_plugin_files(self):
        ps = self.report.plugin_files
        rows = ps.rows if ps else []
        attention_only = self.pf_filter.currentIndex() == 0
        self._pf_visible = [r for r in rows if not attention_only or r.status in pluginscan.NEEDS_ATTENTION]
        t = self.pf_table
        t.set_empty_text("Nothing to report: every plugin file is loaded or kept."
                         if attention_only and not [r for r in rows if r.status in pluginscan.NEEDS_ATTENTION]
                         else "No plugin files match this filter.")
        t.setRowCount(len(self._pf_visible))
        for i, r in enumerate(self._pf_visible):
            rel = ", ".join(([f"needs {', '.join(r.bases)}"] if r.bases else [])
                            + ([f"needed by {', '.join(sorted(r.needed_by))}"] if r.needed_by else []))
            _set_row(t, i, [r.name, pluginscan.STATUS_TEXT.get(r.status, r.status),
                            fmt_size(r.size) if r.size else "-",
                            f"{r.commands_called}/{r.commands_declared}", r.asset_refs, rel, r.note],
                     payload=i)
        self.pf_hint.setText("")
        self.pf_errors.setText("<br>".join(f"Could not read {e}" for e in (ps.errors if ps else [])))

    def _pf_show_hint(self):
        sel = _selected_rows(self.pf_table)
        self.pf_hint.setText(self._pf_visible[sel[0]].hint if len(sel) == 1 else "")

    def _pf_move(self, rows):
        movable = [r for r in rows if r.movable]
        if not movable:
            QMessageBox.information(
                self, "Plugin files", "Nothing to move here. Only files that js/plugins.js never loads "
                                      "(and no other plugin needs) can be moved. For disabled or enabled "
                                      "plugins use the Plugin Manager.")
            return
        names = ", ".join(r.name for r in movable[:8]) + (" ..." if len(movable) > 8 else "")
        if self._confirm("Move plugin files",
                         f"Move {len(movable)} plugin file(s) that js/plugins.js never loads into "
                         f"js/plugins/_unused/ ?\n\n{names}\n\nThe game does not use them. You can undo "
                         f"this, and the Health check's deploy tab will remind you to leave that folder "
                         f"out of the build."):
            self._finish(("move_plugins", [r.file for r in movable]))

    def _pf_move_selected(self):
        self._pf_move([self._pf_visible[i] for i in _selected_rows(self.pf_table)])

    def _pf_move_all(self):
        rows = self.report.plugin_files.rows if self.report.plugin_files else []
        self._pf_move([r for r in rows if r.status == pluginscan.NOT_LOADED])

    def _pf_toggle_keep(self):
        sel = _selected_rows(self.pf_table)
        if not sel:
            return
        if self._pf_ann is None:
            self._pf_ann = Annotations(self.game)
        picked = [self._pf_visible[i] for i in sel]
        make_keep = not all(self._pf_ann.is_flagged(x.key) for x in picked)
        for x in picked:
            self._pf_ann.set_flagged(x.key, make_keep)
        self.report.plugin_files = pluginscan.scan_plugins(self.game, None, self._pf_ann)
        self._fill_plugin_files()

    def _pf_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export plugin report", "plugin_report.csv",
                                              "CSV (*.csv)")
        if path:
            pluginscan.export_plugins_csv(self._pf_visible, Path(path))

    def check_lines(self) -> list[str]:
        d = self.report.deploy
        lines = list(d.warnings)
        for rel, w, h, size in d.oversized[:15]:
            lines.append(f"{rel}: {w}x{h}px ({fmt_size(size)}) - over {4096}px")
        for rel, size, why in d.heavy[:15]:
            lines.append(f"{rel}: {why} ({fmt_size(size)})")
        for rel, size, why in d.leftovers:
            lines.append(f"{rel}: {why} ({fmt_size(size)}) - leave it out of the build")
        return lines

    def _archive(self):
        d = self.report.deploy
        n = sum(f for _, _, f in d.unused_dirs)
        if self._confirm(
                "Move unused folders out",
                f"Move {n} file(s) from {len(d.unused_dirs)} _unused/ folder(s) out of the project?\n\n"
                f"You will choose a folder outside the project. The files keep their layout "
                f"there, so copying that folder back restores them. This step cannot be "
                f"undone from inside the tool."):
            self._finish(("archive",))

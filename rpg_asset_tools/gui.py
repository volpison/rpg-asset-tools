"""
gui.py
------
The window. Deliberately thin: every decision (what is used, what a rename
touches, what is safe to move) lives in the engine modules; this file only
shows it and asks for confirmation.

Workflow:  open project -> table (filter by status/category) ->
           select rows -> Rename...  (live preview of everything it will touch)
                       -> Move unused...  (only assets with NO reference anywhere)
           Undo reverts the last rename / move.
"""

from __future__ import annotations

import hashlib
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal, QSettings
from PySide6.QtGui import (
    QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QDockWidget, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTextEdit, QToolBar, QVBoxLayout, QWidget,
    QApplication, QGroupBox,
)

import batch_tools
import usage
from annotations import Annotations, PRESET_COLORS
from asset_paths import ASSET_ROOTS, CATEGORY_LABELS, AUDIO_CATEGORIES
from audio_preview import AudioPreviewDialog, resolve_audio_path
from image_preview import (
    ImagePreviewDialog, ThumbnailLabel, is_image_path, resolve_preview_path,
)
from health import empty_report
from plugin_refs import add_user_mappings
from health_gui import HealthDialog
from usage_gui import UsageDialog
from progress_gui import ProgressDialog
from deploy import move_unused_dirs_out
from index import (
    STATUS_ORDER, build_asset_index, export_report, format_reference,
    movable_orphans,
)
from rename_engine import (
    RenameItem, describe_last_undoable, merge_assets, move_to_unused,
    plan_rename, rename_many, undo_last,
)

STATUS_LABEL = {
    "ok": "OK", "missing": "Missing", "unused": "Unused", "possible": "Possible",
    "engine": "Engine", "ignored": "Kept",
}
# background tint, always paired with dark text so it reads in dark mode too
STATUS_TINT = {
    "missing": "#F4B6B6", "unused": "#FFE2A8", "possible": "#CFE0FF",
    "ok": "", "engine": "#E2E2E2", "ignored": "#E2E2E2",
}
DARK_TEXT = QColor("#1b1b1b")

STATUS_HELP = {
    "ok": "Referenced and the file exists.",
    "missing": "The project references this name, but no file exists. "
               "Use Rename... to re-point the references to an existing file.",
    "unused": "Nothing references this file (database, maps, troops, common "
              "events, notes, scripts, plugins, effects). Safe to move to _unused/.",
    "possible": "No hard reference, but the name appears in {kinds}. "
                "It may still be used - check before moving.",
    "engine": "Default engine file in img/system. The engine loads it by fixed name.",
    "ignored": "You marked this as 'keep'. It is never offered for moving.",
}
STATUS_FILTERS = [
    ("All statuses", None),
    ("Needs attention (missing + unused)", ("missing", "unused")),
    ("Missing", ("missing",)),
    ("Unused", ("unused",)),
    ("Possible (mentioned in text)", ("possible",)),
    ("OK", ("ok",)),
    ("Kept / engine", ("ignored", "engine")),
]


# --------------------------------------------------------------------------
# small widgets
# --------------------------------------------------------------------------

def swatch_icon(hexcol: str | None, custom: bool = False) -> QIcon:
    """A small square showing the highlight colour (hollow = no colour, 4 colours = pick your own)."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    try:
        if hexcol:
            p.fillRect(1, 1, 14, 14, QColor(hexcol))
        elif custom:
            for i, c in enumerate(("#FF8A80", "#FFF59D", "#80CBC4", "#82B1FF")):
                p.fillRect(1 + (i % 2) * 7, 1 + (i // 2) * 7, 7, 7, QColor(c))
        p.setPen(QPen(QColor("#707070")))
        p.drawRect(1, 1, 13, 13)
    finally:
        p.end()
    return QIcon(pm)


class SortItem(QTableWidgetItem):
    """Table item that sorts by an explicit key instead of its text."""

    def __init__(self, text: str, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        if isinstance(other, SortItem):
            return self.key < other.key
        return super().__lt__(other)


class _Collect:
    """Wraps a job's log so the window can show WHY something was refused."""

    KEYS = ("CANNOT", "ERROR", "refus", "Refus", "cannot", "not allowed", "modified")

    def __init__(self, log):
        self._log = log
        self.lines: list[str] = []

    def __call__(self, text):
        self.lines.append(str(text))
        self._log(text)

    def progress(self, *a):
        p = getattr(self._log, "progress", None)
        if p:
            p(*a)

    def reasons(self) -> str:
        out = [l.strip() for l in self.lines if any(k in l for k in self.KEYS)]
        return "\n".join(out[:4])


class _Reporter:
    """What a background job receives as `log`: call it with text, or .progress(done, total, text)."""

    def __init__(self, worker):
        self._w = worker

    def __call__(self, text):
        self._w.message.emit(str(text))

    def progress(self, done, total, text=""):
        self._w.progress.emit(int(done), int(total), str(text))


class Worker(QThread):
    """Runs fn(log) off the UI thread."""
    message = Signal(str)
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        reporter = _Reporter(self)
        try:
            self.succeeded.emit(self.fn(reporter))
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.message.emit(traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------
# Rename dialog (single or batch) - IS the preview
# --------------------------------------------------------------------------

class RenameDialog(QDialog):
    COL_CAT, COL_OLD, COL_NEW, COL_CHECK = range(4)

    def __init__(self, game: Path, assets: list[dict], scan, parent=None, presets=None):
        super().__init__(parent)
        self.game, self.assets, self.scan = Path(game), assets, scan
        self._filling = True
        self._plan = None
        self.setWindowTitle(f"Rename {len(assets)} asset(s)")
        self.resize(900, 560)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Edit the <b>New name</b> column (names are as stored in the project; "
            "<code>Folder/Name</code> moves the file into a subfolder). "
            "Everything below is checked live - nothing is touched until you press Rename."))

        # --- bulk tools
        r1 = QHBoxLayout()
        self.find = QLineEdit(); self.find.setPlaceholderText("Find")
        self.repl = QLineEdit(); self.repl.setPlaceholderText("Replace with")
        self.chk_regex = QCheckBox("Regex")
        b_fr = QPushButton("Apply")
        b_fr.clicked.connect(self._do_find_replace)
        for w in (QLabel("Find/replace:"), self.find, self.repl, self.chk_regex, b_fr):
            r1.addWidget(w)
        lay.addLayout(r1)

        r2 = QHBoxLayout()
        self.prefix = QLineEdit(); self.prefix.setPlaceholderText("Prefix")
        self.suffix = QLineEdit(); self.suffix.setPlaceholderText("Suffix")
        b_ps = QPushButton("Apply")
        b_ps.clicked.connect(self._do_prefix_suffix)
        b_folder = QPushButton("Move to folder...")
        b_folder.clicked.connect(self._do_folder)
        b_paste = QPushButton("Paste list...")
        b_paste.setToolTip("Paste 'old<TAB>new' lines (from a spreadsheet), "
                           "or just new names in the same order as the rows.")
        b_paste.clicked.connect(self._do_paste)
        b_file = QPushButton("Load file...")
        b_file.setToolTip("CSV / TXT with 'old,new' lines, or an old rename_mapping.json")
        b_file.clicked.connect(self._do_file)
        b_reset = QPushButton("Reset")
        b_reset.clicked.connect(self._reset)
        for w in (QLabel("Prefix/suffix:"), self.prefix, self.suffix, b_ps,
                  b_folder, b_paste, b_file, b_reset):
            r2.addWidget(w)
        lay.addLayout(r2)

        # --- table
        self.table = QTableWidget(len(assets), 4)
        self.table.setHorizontalHeaderLabels(["Category", "Current name", "New name", "Check"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_CAT, QHeaderView.Interactive)   # sized once below
        hdr.setSectionResizeMode(self.COL_OLD, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_NEW, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_CHECK, QHeaderView.Stretch)
        for i, a in enumerate(assets):
            self._set_ro(i, self.COL_CAT, CATEGORY_LABELS.get(a["category"], a["category"]))
            self._set_ro(i, self.COL_OLD, a["name"])
            self.table.setItem(i, self.COL_NEW, QTableWidgetItem(a["name"]))
            self._set_ro(i, self.COL_CHECK, "")
        self.table.resizeColumnToContents(self.COL_CAT)
        self.table.itemChanged.connect(self._on_changed)
        lay.addWidget(self.table, stretch=1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(90)
        lay.addWidget(self.notes)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.btn_ok = self.buttons.addButton("Rename", QDialogButtonBox.AcceptRole)
        self.btn_ok.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._recheck)
        self._filling = False
        self.table.setCurrentCell(0, self.COL_NEW)
        if presets:                      # e.g. suggestions from the Health check
            for row, new in presets.items():
                self._set_new(row, new)
            self._recheck()
        else:
            self.table.editItem(self.table.item(0, self.COL_NEW))

    # ---- helpers
    def _set_ro(self, row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, col, it)

    def _new_name(self, row) -> str:
        return self.table.item(row, self.COL_NEW).text().strip()

    def _set_new(self, row, text):
        self._filling = True
        self.table.item(row, self.COL_NEW).setText(text)
        self._filling = False

    def _target_rows(self) -> list[int]:
        sel = sorted({i.row() for i in self.table.selectedIndexes()})
        return sel or list(range(self.table.rowCount()))

    def _on_changed(self, _item):
        if not self._filling:
            self.btn_ok.setEnabled(False)
            self.summary.setText("Checking...")
            self._timer.start()

    # ---- bulk tools (apply to selected rows, or all rows if none selected)
    def _do_find_replace(self):
        for r in self._target_rows():
            self._set_new(r, batch_tools.apply_find_replace(
                self._new_name(r), self.find.text(), self.repl.text(),
                self.chk_regex.isChecked()))
        self._timer.start()

    def _do_prefix_suffix(self):
        for r in self._target_rows():
            self._set_new(r, batch_tools.add_prefix_suffix(
                self._new_name(r), self.prefix.text(), self.suffix.text()))
        self._timer.start()

    def _do_folder(self):
        rows = self._target_rows()
        cat = self.assets[rows[0]]["category"]
        root = (self.game / ASSET_ROOTS[cat][0]).resolve()
        start = str(root if root.is_dir() else self.game)
        chosen = QFileDialog.getExistingDirectory(
            self, f"Destination folder inside {ASSET_ROOTS[cat][0].as_posix()}", start)
        if not chosen:
            return
        try:
            rel = Path(chosen).resolve().relative_to(root).as_posix()
        except ValueError:
            QMessageBox.warning(self, "Folder", "The folder must be inside the asset folder.")
            return
        rel = "" if rel == "." else rel
        for r in rows:
            if self.assets[r]["category"] == cat:
                self._set_new(r, batch_tools.set_folder(self._new_name(r), rel))
        self._timer.start()

    def _apply_pairs(self, pairs, warnings):
        names = [a["name"] for a in self.assets]
        mapping, more = batch_tools.apply_pairs_to_rows(names, pairs)
        for r, new in mapping.items():
            self._set_new(r, new)
        msgs = warnings + more
        if msgs:
            QMessageBox.information(self, "Import", "\n".join(msgs[:15]))
        self._timer.start()

    def _do_paste(self):
        text, ok = QInputDialog.getMultiLineText(
            self, "Paste list",
            "One rule per line:  old<TAB>new   or   old,new   or   old->new\n"
            "Or only new names, one per line, in the order of the rows:")
        if ok and text.strip():
            pairs, warns = batch_tools.parse_mapping_text(text)
            self._apply_pairs(pairs, warns)

    def _do_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load rename list", "", "Rename lists (*.csv *.txt *.json)")
        if not path:
            return
        try:
            pairs, warns = batch_tools.parse_mapping_file(Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Load file", str(e))
            return
        self._apply_pairs(pairs, warns)

    def _reset(self):
        for r, a in enumerate(self.assets):
            self._set_new(r, a["name"])
        self._timer.start()

    # ---- live check
    def result_items(self) -> list[RenameItem]:
        items = []
        for r, a in enumerate(self.assets):
            new = self._new_name(r)
            if new and new != a["name"]:
                items.append(RenameItem(a["category"], a["name"], new))
        return items

    def _recheck(self):
        self._filling = True            # our own cell updates must not re-trigger a check
        try:
            self._recheck_inner()
        finally:
            self._filling = False

    def _recheck_inner(self):
        rows = [r for r, a in enumerate(self.assets)
                if self._new_name(r) and self._new_name(r) != a["name"]]
        for r in range(self.table.rowCount()):
            self.table.item(r, self.COL_CHECK).setText("")
            self.table.item(r, self.COL_CHECK).setForeground(QBrush())
        if not rows:
            self.summary.setText("Nothing to rename yet.")
            self.notes.clear()
            self.btn_ok.setEnabled(False)
            self.btn_ok.setText("Rename")
            return
        items = [RenameItem(self.assets[r]["category"], self.assets[r]["name"],
                            self._new_name(r)) for r in rows]
        plan = plan_rename(self.game, items, self.scan)
        self._plan = plan
        for k, r in enumerate(rows):
            cell = self.table.item(r, self.COL_CHECK)
            errs = plan.item_errors.get(k, [])
            if errs:
                cell.setText("X " + errs[0])
                cell.setForeground(QBrush(QColor("#d9534f")))
            else:
                n = plan.ref_counts.get(k, 0)
                cell.setText(f"OK - {n} reference(s)")
                cell.setForeground(QBrush(QColor("#3c9a5f")))
        files = sum(plan.json_changes.values())
        self.summary.setText(
            f"{len(rows)} rename(s): {len(plan.moves)} file(s) to move, "
            f"{files} field(s) in {len(plan.json_changes)} data file(s) to update.")
        lines = list(plan.errors) + [f"WARNING: {w}" for w in plan.warnings]
        self.notes.setPlainText("\n".join(lines))
        self.btn_ok.setEnabled(plan.ok)
        self.btn_ok.setText(f"Rename {len(rows)}")


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RPG Maker MZ - Asset Tools")
        self.resize(1180, 760)

        self._watchdog = None
        try:
            from watchdog import Watchdog
            self._watchdog = Watchdog()
            self._beat = QTimer(self)
            self._beat.timeout.connect(self._watchdog.beat)
            self._beat.start(1000)
        except Exception:                      # noqa: BLE001 - diagnostics must never stop the app
            self._watchdog = None

        self.game: Path | None = None
        self.ann: Annotations | None = None
        self.assets: list[dict] = []
        self.shown: list[dict] = []
        self.scan = None
        self._health, self._health_game = None, None
        self._running = False
        self._workers: list[Worker] = []
        self._previews: list = []
        self._comment_target: dict | None = None
        self._loading_details = False
        self.settings = QSettings()

        self._build_toolbar()
        self._build_body()
        self._build_log_dock()
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.hide()
        self.statusBar().addPermanentWidget(self.busy)
        QShortcut(QKeySequence.Find, self).activated.connect(self.search.setFocus)

        self._update_actions()
        self.statusBar().showMessage("Open your project folder (the one that contains data/ and img/).")
        last = self.settings.value("last_project", "", str)
        if last and (Path(last) / "data").is_dir():
            QTimer.singleShot(0, lambda: self.open_project(Path(last)))

    # ------------------------------------------------------------------ UI
    def _act(self, text, slot, shortcut=None, tip=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
        return a

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act_open = self._act("Open project...", self.choose_project, "Ctrl+O")
        self.act_refresh = self._act("Refresh", self.refresh, "F5", "Re-scan the project")
        self.act_rename = self._act("Rename...", self.rename_selected, "F2",
                                    "Rename / move the selected assets (shows a live preview)")
        self.act_unused = self._act("Move unused...", self.move_unused, None,
                                    "Move assets nothing references into _unused/ folders")
        self.act_undo = self._act("Undo", self.undo, "Ctrl+Z", "Undo the last rename / move")
        self.act_export = self._act("Export...", self.export, None, "Save the list as JSON or CSV")
        self.act_usage = self._act("Where is this used?", self.show_usage, "Ctrl+U",
                                   "Show where the selected asset is used (map, event, page, line)")
        self.act_health = self._act("Health check...", self.health_check, "Ctrl+H",
                                    "Duplicates, naming problems and deploy size")
        for a in (self.act_open, self.act_refresh):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.act_rename, self.act_usage, self.act_unused, self.act_health, self.act_undo):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_export)
        self.toolbar = tb

    def _build_body(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            "background:#F4B6B6; color:#1b1b1b; padding:6px; border-radius:4px;")
        self.banner.hide()
        root.addWidget(self.banner)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name...   (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        self.cat_combo = QComboBox()
        self.cat_combo.setMinimumWidth(190)
        self.cat_combo.currentIndexChanged.connect(self.apply_filter)
        self.status_combo = QComboBox()
        self.status_combo.setMinimumWidth(240)
        for label, _ in STATUS_FILTERS:
            self.status_combo.addItem(label)
        self.status_combo.currentIndexChanged.connect(self.apply_filter)
        row.addWidget(self.search, stretch=1)
        row.addWidget(self.cat_combo)
        row.addWidget(self.status_combo)
        root.addLayout(row)

        self.summary = QLabel("")
        root.addWidget(self.summary)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, stretch=1)

        # ---- table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Status", "Category", "Name", "Refs", "File"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        h = self.table.horizontalHeader()
        # Interactive, not ResizeToContents: that mode re-measures the whole column after every
        # single setItem(), which made refilling a populated 5000-row table take minutes.
        # apply_filter() sizes these columns once, after the fill.
        for c in (0, 1, 3):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.on_selection)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_full_preview())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        split.addWidget(self.table)

        # ---- details
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.d_title = QLabel("Select an asset")
        self.d_title.setWordWrap(True)
        self.d_title.setTextFormat(Qt.RichText)
        self.d_help = QLabel("")
        self.d_help.setWordWrap(True)
        self.thumb = ThumbnailLabel()
        self.thumb.setFixedHeight(170)
        self.btn_play = QPushButton("Play audio...")
        self.btn_play.clicked.connect(self.open_full_preview)
        self.btn_play.hide()
        rl.addWidget(self.d_title)
        rl.addWidget(self.d_help)
        rl.addWidget(self.thumb)
        rl.addWidget(self.btn_play)
        rl.addWidget(QLabel("Used in:"))
        self.ref_list = QListWidget()
        rl.addWidget(self.ref_list, stretch=1)

        notes = QGroupBox("Notes")
        nl = QVBoxLayout(notes)
        self.comment = QTextEdit()
        self.comment.setPlaceholderText("Notes about this asset (saved automatically)")
        self.comment.setMaximumHeight(80)
        self.comment.textChanged.connect(self._comment_changed)
        nl.addWidget(self.comment)
        nrow = QHBoxLayout()
        self.btn_color = QPushButton("Color")
        self.btn_color.setToolTip("Highlight the selected asset(s) in the list")
        self.color_menu = QMenu(self)
        self.color_menu.addAction(swatch_icon(None), "None").triggered.connect(lambda: self.set_color(""))
        for name, hexcol in PRESET_COLORS.items():
            self.color_menu.addAction(swatch_icon(hexcol), f"{name}  {hexcol}").triggered.connect(
                lambda _=False, c=hexcol: self.set_color(c))
        self.color_menu.addAction(swatch_icon(None, custom=True), "Custom...").triggered.connect(
            self.pick_custom_color)
        self.btn_color.setMenu(self.color_menu)
        self.chk_keep = QCheckBox("Keep (never offer to move)")
        self.chk_keep.setToolTip("For files used in ways this tool cannot see.")
        self.chk_keep.clicked.connect(self.toggle_keep)
        nrow.addWidget(self.btn_color)
        nrow.addWidget(self.chk_keep)
        nrow.addStretch()
        nl.addLayout(nrow)
        rl.addWidget(notes)
        split.addWidget(right)
        split.setSizes([700, 460])

        self._comment_timer = QTimer(self)
        self._comment_timer.setSingleShot(True)
        self._comment_timer.setInterval(700)
        self._comment_timer.timeout.connect(self._flush_comment)

    def _build_log_dock(self):
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        dock = QDockWidget("Log", self)
        dock.setWidget(self.log)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.hide()
        self.log_dock = dock
        self.toolbar.addSeparator()
        self.toolbar.addAction(dock.toggleViewAction())

    # --------------------------------------------------------------- helpers
    def log_message(self, text: str):
        self.log.appendPlainText(str(text))

    def _set_busy(self, busy: bool):
        self.busy.setVisible(busy)
        self.centralWidget().setEnabled(not busy)
        if busy:
            for a in (self.act_open, self.act_refresh, self.act_rename, self.act_unused,
                      self.act_undo, self.act_export, self.act_health):
                a.setEnabled(False)
        else:
            self._update_actions()

    def _run(self, fn, on_done, title="Working...", parent=None, on_fail=None):
        """Run fn(log) on a worker thread. `parent` hosts the progress window (a modal
        dialog must own it, or it could not be used); `on_fail` is told about errors."""
        if self._running:
            self.log_message(f"[busy] Skipped, another operation is still running: {title}")
            self.statusBar().showMessage("Another operation is still running - wait for it to finish.", 6000)
            if on_fail:
                on_fail("Another operation is still running.")
            return
        self._running = True
        self._flush_comment()
        self.statusBar().showMessage(title)
        self._set_busy(True)
        w = Worker(fn, self)
        w.message.connect(self.log_message)
        dlg = ProgressDialog(title.rstrip("."), parent or self)
        w.message.connect(dlg.add_log)
        w.progress.connect(dlg.set_progress)
        # only show the window when the work is not instant
        QTimer.singleShot(400, lambda: dlg.show() if dlg.active else None)

        def done(result):
            self._running = False
            self._set_busy(False)
            dlg.finish()
            on_done(result)

        def failed(msg):
            self._running = False
            self._set_busy(False)
            dlg.finish()
            self.log_dock.show()
            if on_fail:
                on_fail(msg)
            QMessageBox.critical(parent or self, "Error", msg)

        w.succeeded.connect(done)
        w.failed.connect(failed)
        self._workers.append(w)
        w.finished.connect(lambda w=w: self._workers.remove(w) if w in self._workers else None)
        w.start()

    def _selected(self) -> list[dict]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        out = []
        for r in rows:
            idx = self.table.item(r, 2).data(Qt.UserRole)
            if idx is not None:
                out.append(self.shown[idx])
        return out

    def _update_actions(self):
        has = self.game is not None
        sel = self._selected() if has else []
        blocked = bool(self.scan and self.scan.errors)
        self.act_open.setEnabled(True)
        self.act_refresh.setEnabled(has)
        self.act_rename.setEnabled(has and bool(sel) and not any(
            a["status"] == "engine" for a in sel))
        self.act_unused.setEnabled(has and not blocked and bool(movable_orphans(self.assets)))
        self.act_export.setEnabled(has and bool(self.assets))
        self.act_health.setEnabled(has and bool(self.assets))
        desc = describe_last_undoable(self.game) if has else None
        self.act_undo.setEnabled(bool(desc))
        self.act_undo.setText(f"Undo ({desc})" if desc else "Undo")

    # --------------------------------------------------------------- project
    def choose_project(self):
        start = self.settings.value("last_project", "", str)
        folder = QFileDialog.getExistingDirectory(self, "Select the RPG Maker MZ project folder", start)
        if folder:
            self.open_project(Path(folder))

    def open_project(self, folder: Path):
        if not (folder / "data").is_dir():
            QMessageBox.warning(
                self, "Not a project",
                "That folder has no data/ subfolder.\nChoose the project folder itself "
                "(the one containing data, img and audio).")
            return
        self.game = folder
        self.settings.setValue("last_project", str(folder))
        self.ann = Annotations(folder)
        self.setWindowTitle(f"RPG Maker MZ - Asset Tools - {folder.name}")
        self.refresh()

    def refresh(self):
        if not self.game:
            return
        game, ann = self.game, self.ann

        def work(log):
            return build_asset_index(game, log, ann)

        def done(result):
            t0 = time.monotonic()
            self.assets, self.scan = result
            self._health = None                  # results of the Health check are stale now
            self._fill_category_combo()
            self.banner.setVisible(bool(self.scan.errors))
            if self.scan.errors:
                self.banner.setText(
                    f"{len(self.scan.errors)} data file(s) could not be read "
                    f"({'; '.join(self.scan.errors[:2])}). References inside them are "
                    f"invisible, so 'unused' results may be wrong. Moving and renaming "
                    f"are disabled until the file is fixed.")
            t1 = time.monotonic()
            self.apply_filter()
            t2 = time.monotonic()
            self.statusBar().showMessage(f"Loaded {len(self.assets)} assets.", 5000)
            self._update_actions()
            t3 = time.monotonic()
            self.log_message(f"[timing] window update: {t3 - t0:.1f} s (table {t2 - t1:.1f} s, "
                             f"actions {t3 - t2:.1f} s)")

        self._run(work, done, "Scanning project...")

    # ----------------------------------------------------------------- table
    def _fill_category_combo(self):
        counts: dict[str, int] = {}
        for a in self.assets:
            counts[a["category"]] = counts.get(a["category"], 0) + 1
        cur = self.cat_combo.currentData()
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        self.cat_combo.addItem(f"All categories ({len(self.assets)})", None)
        for cat in sorted(counts, key=lambda c: CATEGORY_LABELS.get(c, c)):
            self.cat_combo.addItem(f"{CATEGORY_LABELS.get(cat, cat)} ({counts[cat]})", cat)
        i = self.cat_combo.findData(cur)
        self.cat_combo.setCurrentIndex(max(i, 0))
        self.cat_combo.blockSignals(False)

    def apply_filter(self, *_):
        text = self.search.text().strip().lower()
        cat = self.cat_combo.currentData()
        statuses = STATUS_FILTERS[max(self.status_combo.currentIndex(), 0)][1]
        keep = {a["asset_key"] for a in self._selected()}

        self.shown = [
            a for a in self.assets
            if (cat is None or a["category"] == cat)
            and (statuses is None or a["status"] in statuses)
            and (not text or text in a["name"].lower())
        ]
        t = self.table
        t.setUpdatesEnabled(False)               # one repaint at the end, not one per row
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(0)                         # drop the old items in one go ...
        t.setRowCount(len(self.shown))           # ... so every cell below is a fresh insert
        for r, a in enumerate(self.shown):
            tint = STATUS_TINT.get(a["status"], "")
            st = SortItem(STATUS_LABEL[a["status"]], STATUS_ORDER[a["status"]])
            if tint:
                st.setBackground(QBrush(QColor(tint)))
                st.setForeground(QBrush(DARK_TEXT))
            name = SortItem(a["name"], a["name"].lower())
            name.setData(Qt.UserRole, r)
            files = a["files"]
            fshow = (files[0] + (f"  (+{len(files) - 1})" if len(files) > 1 else "")) if files else ""
            t.setItem(r, 0, st)
            t.setItem(r, 1, SortItem(CATEGORY_LABELS.get(a["category"], a["category"]),
                                     a["category"]))
            t.setItem(r, 2, name)
            t.setItem(r, 3, SortItem(str(a["ref_count"]), a["ref_count"]))
            t.setItem(r, 4, SortItem(fshow, fshow.lower()))
            self._tint_name(name, a)
        t.blockSignals(False)
        for c in (0, 1, 3):
            t.resizeColumnToContents(c)          # once, not once per cell
        t.setSortingEnabled(True)
        t.setUpdatesEnabled(True)

        # restore selection
        t.blockSignals(True)
        for r in range(t.rowCount()):
            idx = t.item(r, 2).data(Qt.UserRole)
            if self.shown[idx]["asset_key"] in keep:
                t.selectRow(r) if len(keep) == 1 else t.item(r, 2).setSelected(True)
        t.blockSignals(False)

        counts = {}
        for a in self.assets:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        bits = [f"{counts[s]} {STATUS_LABEL[s].lower()}" for s in
                ("missing", "unused", "possible", "ok") if counts.get(s)]
        self.summary.setText(f"Showing {len(self.shown)} of {len(self.assets)}  -  " + ", ".join(bits))
        self.on_selection()

    def _tint_name(self, item: QTableWidgetItem, a: dict):
        color = self.ann.get_color(a["category"], a["name"]) if self.ann else ""
        if color:
            item.setBackground(QBrush(QColor(color)))
            item.setForeground(QBrush(DARK_TEXT))
        else:
            item.setBackground(QBrush())
            item.setForeground(QBrush())

    # --------------------------------------------------------------- details
    def on_selection(self):
        self._flush_comment()
        sel = self._selected()
        self._update_actions()
        self._loading_details = True
        try:
            self.ref_list.clear()
            self.btn_play.hide()
            self.thumb.hide()
            self.thumb.set_image(QPixmap())
            self._comment_target = None
            self.comment.clear()
            self.comment.setEnabled(len(sel) == 1)
            self.btn_color.setEnabled(bool(sel))
            self.chk_keep.setEnabled(bool(sel))
            if not sel:
                self.d_title.setText("Select an asset")
                self.d_help.setText("")
                self.chk_keep.setChecked(False)
                return
            if len(sel) > 1:
                self.d_title.setText(f"<b>{len(sel)} assets selected</b>")
                self.d_help.setText("Press F2 to rename them together, or right-click for more.")
                self.chk_keep.setChecked(all(a["ignored"] for a in sel))
                return
            a = sel[0]
            cat = CATEGORY_LABELS.get(a["category"], a["category"])
            self.d_title.setText(f"<b>{a['name']}</b><br>{cat} - {STATUS_LABEL[a['status']]}")
            help_text = STATUS_HELP[a["status"]].format(
                kinds=", ".join(a["mention_kinds"]) or "free text")
            if len(a["files"]) > 1:
                help_text += "\nFiles: " + ", ".join(a["files"])
            self.d_help.setText(help_text)
            self.chk_keep.setChecked(a["ignored"])
            self.chk_keep.setEnabled(a["status"] in ("unused", "possible", "ignored"))
            refs = a["references"]
            for loc in refs[:300]:
                self.ref_list.addItem(format_reference(loc))
            if len(refs) > 300:
                self.ref_list.addItem(f"... and {len(refs) - 300} more")
            for m in a.get("mentions", [])[:40]:
                self.ref_list.addItem(f"mentioned in {m['kind']} (possible): {m['file']}")
            self._comment_target = a
            self.comment.setPlainText(self.ann.get_comment(a["category"], a["name"]))
            img = resolve_preview_path(self.game, a)
            if img and is_image_path(img):
                self.thumb.set_image(QPixmap(str(img)))
                self.thumb.show()
            elif a["category"] in AUDIO_CATEGORIES and resolve_audio_path(self.game, a):
                self.btn_play.show()
        finally:
            self._loading_details = False

    def open_full_preview(self):
        sel = self._selected()
        if len(sel) != 1 or not self.game:
            return
        a = sel[0]
        img = resolve_preview_path(self.game, a)
        if img:
            dlg = ImagePreviewDialog(img, a["name"], self)
        else:
            aud = resolve_audio_path(self.game, a)
            if not aud:
                return
            dlg = AudioPreviewDialog(aud, a["name"], self)
        self._previews = [d for d in self._previews if d.isVisible()] + [dlg]
        dlg.show()

    # ----------------------------------------------------------- notes/color
    def _comment_changed(self):
        if not self._loading_details and self._comment_target:
            self._comment_timer.start()

    def _flush_comment(self):
        self._comment_timer.stop()
        a = self._comment_target
        if a and self.ann and not self._loading_details:
            self.ann.set_comment(a["category"], a["name"], self.comment.toPlainText())

    def set_color(self, color: str):
        for a in self._selected():
            self.ann.set_color(a["category"], a["name"], color)
        self.table.setUpdatesEnabled(False)
        try:
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 2)
                self._tint_name(item, self.shown[item.data(Qt.UserRole)])
        finally:
            self.table.setUpdatesEnabled(True)

    def pick_custom_color(self):
        c = QColorDialog.getColor(QColor("#FFDBBB"), self, "Pick a highlight color")
        if c.isValid():
            self.set_color(c.name())

    def toggle_keep(self, checked: bool):
        for a in self._selected():
            self.ann.set_ignored(a["category"], a["name"], checked)
            a["ignored"] = checked
            if checked and a["base_status"] in ("unused", "possible"):
                a["status"] = "ignored"
            elif not checked:
                a["status"] = a["base_status"]
        self.apply_filter()

    # ------------------------------------------------------------ operations
    def rename_selected(self):
        self.open_rename(self._selected())

    def open_rename(self, rows: list[dict], presets=None):
        if not rows or not self.game:
            return
        dlg = RenameDialog(self.game, rows, self.scan, self, presets)
        if dlg.exec() != QDialog.Accepted:
            return
        items = dlg.result_items()
        if items:
            self._run_rename(items)

    def _run_rename(self, items):
        game, scan, ann = self.game, self.scan, self.ann
        if not self._ack_backup_notice():
            return
        self.log.clear()
        col: dict = {}

        def work(log):
            col["c"] = _Collect(log)
            return rename_many(game, items, False, col["c"], scan, ann)

        def done(result):
            ok, _plan = result
            if not ok:
                self.log_dock.show()
                why = col["c"].reasons() if "c" in col else ""
                QMessageBox.warning(self, "Rename", "Nothing was changed."
                                    + (f"\n\n{why}" if why else "") + "\n\nSee the log for details.")
            else:
                self.statusBar().showMessage(f"Renamed {len(items)} asset(s). Undo is available.", 8000)
            self.refresh()

        self._run(work, done, "Renaming...")

    def move_unused(self):
        if not self.game or not self.scan:
            return
        sel_unused = [a for a in self._selected() if a["status"] == "unused"]
        targets = sel_unused or movable_orphans(self.assets)
        if not targets:
            return
        rels = [f for a in targets for f in a["files"]]
        box = QMessageBox(self)
        box.setWindowTitle("Move unused assets")
        scope = "the selected" if sel_unused else "ALL"
        box.setText(f"Move {scope} {len(targets)} unused asset(s) ({len(rels)} file(s)) "
                    f"into _unused/ folders next to them?")
        box.setInformativeText(
            "Nothing in data/*.json, notes, scripts, plugins or effects refers to them. "
            "Files are moved, not deleted, and Undo puts them back.")
        box.setDetailedText("\n".join(rels[:500]))
        btn_go = box.addButton("Move to _unused/", QMessageBox.AcceptRole)
        btn_cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(btn_cancel)
        box.exec()
        if box.clickedButton() is not btn_go:
            return
        if not self._ack_backup_notice():
            return
        game = self.game
        self.log.clear()

        def work(log):
            return move_to_unused(game, rels, False, log)

        def done(result):
            self.statusBar().showMessage(f"Moved {result[0]} file(s). Undo is available.", 8000)
            self.refresh()

        self._run(work, done, "Moving files...")

    def undo(self):
        desc = describe_last_undoable(self.game) if self.game else None
        if not desc:
            return
        if QMessageBox.question(self, "Undo", f"Undo the last operation ({desc})?") != QMessageBox.Yes:
            return
        game, ann = self.game, self.ann
        self.log.clear()
        col: dict = {}

        def work(log):
            col["c"] = _Collect(log)
            return undo_last(game, col["c"], ann)

        def done(ok):
            if not ok:
                self.log_dock.show()
                why = col["c"].reasons() if "c" in col else ""
                QMessageBox.warning(
                    self, "Undo", "Nothing was undone, and nothing was changed."
                    + (f"\n\n{why}" if why else "")
                    + "\n\nThis protects work done in RPG Maker after the operation. "
                      "See the log for details.")
            self.refresh()

        self._run(work, done, "Undoing...")

    # ---------------------------------------------------------- health check
    def health_check(self):
        if not self.game or not self.assets:
            return
        game, assets, scan = self.game, self.assets, self.scan
        # keep earlier results while the project has not been re-scanned
        if self._health is None or self._health_game != game:
            self._health, self._health_game = empty_report(game), game
        dlg = HealthDialog(game, self._health, self, runner=self._run, assets=assets, scan=scan)
        dlg.exec()
        action = dlg.action
        dlg.deleteLater()
        # let the dialog finish closing and repaint before any heavy follow-up work starts
        QTimer.singleShot(0, lambda: self.handle_health_action(action))

    def handle_health_action(self, action):
        if not action:
            return
        kind = action[0]
        if kind == "recheck":
            self.health_check()
        elif kind == "rename":
            self.open_rename(action[1], action[2])
        elif kind == "merge":
            self._run_merge(action[1])
        elif kind == "map_params":
            add_user_mappings(self.game, action[1])
            self.statusBar().showMessage(
                f"Stored {len(action[1])} plugin parameter mapping(s).", 8000)
            self.refresh()
        elif kind == "move_plugins":
            self._move_aside(action[1], "plugin file(s)", "js/plugins/_unused/")
        elif kind == "move_stray":
            self._move_aside(action[1], "stray file(s)", "data/_unused/")
        elif kind == "archive":
            dest = QFileDialog.getExistingDirectory(
                self, "Choose a folder OUTSIDE the project for the unused files")
            if dest:
                self._run_archive(Path(dest))

    def _move_aside(self, rels, what: str, where: str):
        """Move files into an _unused folder off the UI thread, then rescan."""
        game = self.game
        if not self._ack_backup_notice():
            return
        self.log.clear()

        def work(log):
            n, _op = move_to_unused(game, rels, False, log, "_unused")
            return n

        def done(n):
            self.statusBar().showMessage(f"Moved {n} {what} to {where}. Undo is available.", 8000)
            self.refresh()

        self._run(work, done, "Moving files...")

    def _ack_backup_notice(self) -> bool:
        """Once per project: remind the user to keep a copy before the tool changes files."""
        if not self.game:
            return False
        key = "ack_backup/" + hashlib.sha1(str(self.game).encode("utf-8")).hexdigest()[:12]
        try:
            if self.settings.value(key, "", str):
                return True
        except Exception:                      # noqa: BLE001
            pass
        box = QMessageBox(self)
        box.setWindowTitle("Before changing your project")
        box.setIcon(QMessageBox.Information)
        box.setText("This tool is about to change files in your project.")
        box.setInformativeText(
            "Renames and merges rewrite data/*.json, and files are moved into _unused/ folders.\n\n"
            "Before each change the tool saves a backup of the data files it edits "
            "(.asset_tools/backups) and Undo can restore everything - but Undo refuses if a file "
            "was changed by something else afterwards.\n\n"
            "So: close RPG Maker MZ first, and keep a separate copy of the project "
            "(a zip of the folder is enough).")
        chk = QCheckBox("Do not show this again for this project")
        box.setCheckBox(chk)
        go = box.addButton("I have a copy - continue", QMessageBox.AcceptRole)
        cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not go:
            return False
        if chk.isChecked():
            try:
                self.settings.setValue(key, "1")
            except Exception:                  # noqa: BLE001
                pass
        return True

    def _run_merge(self, merges):
        game, scan = self.game, self.scan
        if not self._ack_backup_notice():
            return
        self.log.clear()
        col: dict = {}

        def work(log):
            col["c"] = _Collect(log)
            return merge_assets(game, merges, False, col["c"], scan)

        def done(result):
            ok, _plan = result
            if not ok:
                self.log_dock.show()
                why = col["c"].reasons() if "c" in col else ""
                QMessageBox.warning(self, "Merge", "Nothing was changed."
                                    + (f"\n\n{why}" if why else "") + "\n\nSee the log for details.")
            else:
                self.statusBar().showMessage(
                    f"Merged {len(merges)} duplicate(s). Undo is available.", 8000)
            self.refresh()

        self._run(work, done, "Merging duplicates...")

    def _run_archive(self, dest: Path):
        game = self.game
        if not self._ack_backup_notice():
            return
        self.log.clear()

        def work(log):
            return move_unused_dirs_out(game, dest, log)

        def done(n):
            self.statusBar().showMessage(f"Moved {n} file(s) to {dest}", 10000)
            self.refresh()

        self._run(work, done, "Moving files out...")

    def export(self):
        if not self.game:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export asset list", str(self.game / "asset_report.json"),
            "JSON (*.json);;CSV (*.csv)")
        if path:
            export_report(self.assets, Path(path), self.scan)
            self.statusBar().showMessage(f"Saved {path}", 6000)

    # ---------------------------------------------------------- context menu
    def context_menu(self, pos):
        sel = self._selected()
        if not sel:
            return
        menu = QMenu(self)
        menu.addAction("Rename...  (F2)").triggered.connect(self.rename_selected)
        if len(sel) == 1:
            menu.addAction("Where is this used?  (Ctrl+U)").triggered.connect(self.show_usage)
        if any(a["status"] == "unused" for a in sel):
            menu.addAction("Move unused to _unused/...").triggered.connect(self.move_unused)
        keep_all = all(a["ignored"] for a in sel)
        menu.addAction("Remove 'keep' flag" if keep_all else "Keep (never offer to move)"
                       ).triggered.connect(lambda: self.toggle_keep(not keep_all))
        menu.addSeparator()
        menu.addAction("Copy name").triggered.connect(
            lambda: self.copy_text("\n".join(a["name"] for a in sel)))
        if len(sel) == 1 and sel[0]["path"]:
            menu.addAction("Open containing folder").triggered.connect(
                lambda: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str((self.game / sel[0]["path"]).parent))))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def show_usage(self):
        sel = self._selected()
        if len(sel) != 1:
            self.statusBar().showMessage("Select one asset first.", 4000)
            return
        a = sel[0]
        refs = a["references"]
        mentions = a.get("mentions", [])
        if not refs and not mentions:
            self.statusBar().showMessage(f"'{a['name']}' is not used anywhere that the tool can see.", 6000)
            return
        items = usage.from_asset_refs(usage.Locator(self.game), refs[:500], mentions[:100])
        label = f"{CATEGORY_LABELS.get(a['category'], a['category'])}: {a['name']}"
        UsageDialog(self.game, label, items, len(refs) + len(mentions), self).exec()

    def copy_text(self, text: str):
        QApplication.clipboard().setText(text)

    def closeEvent(self, event):
        self._flush_comment()
        super().closeEvent(event)

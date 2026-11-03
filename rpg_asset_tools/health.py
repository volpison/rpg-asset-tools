"""
health.py
---------
One call that runs the three project checks and returns everything the
Health window shows.  (Pure Python: no Qt, so it is unit-tested directly.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import dbscan
import deploy
import mapscan
import pluginscan
import lint
import plugin_refs
from annotations import Annotations
from asset_paths import list_disk_assets
from duplicates import find_duplicates
from progress import report
from scanner import scan_project


@dataclass
class HealthReport:
    dups: list = field(default_factory=list)            # list[DupGroup]
    issues: list = field(default_factory=list)          # list[lint.NameIssue]
    deploy: deploy.DeployReport | None = None
    rules_path: Path | None = None
    param_suggestions: list = field(default_factory=list)   # plugin_refs.ParamSuggestion
    plugin_declared: int = 0           # plugin params / command args recognised as assets
    plugin_user_mapped: int = 0
    plugins_with_header: int = 0
    db_scan: dbscan.DbScan | None = None
    db_rows: list = field(default_factory=list)         # list[dbscan.DbRow]
    maps: mapscan.MapScan | None = None
    plugin_files: pluginscan.PluginScan | None = None
    done: set = field(default_factory=set)             # sections that have been run

    def has(self, key: str) -> bool:
        return key in self.done

    @property
    def plugin_files_attention(self) -> int:
        return sum(1 for r in (self.plugin_files.rows if self.plugin_files else [])
                   if r.status in pluginscan.NEEDS_ATTENTION)

    @property
    def map_attention(self) -> int:
        return sum(1 for r in (self.maps.rows if self.maps else [])
                   if r.status in (mapscan.UNUSED, mapscan.ORPHAN, mapscan.MISSING, mapscan.BROKEN, mapscan.STRAY))

    @property
    def db_attention(self) -> int:
        """Entries that are certainly worth a look (not just 'possible')."""
        return sum(1 for r in self.db_rows
                   if r.status in (dbscan.UNUSED, dbscan.WRITE_ONLY, dbscan.READ_ONLY, dbscan.BROKEN))

    @property
    def dup_freed_bytes(self) -> int:
        return sum(g.freed_bytes for g in self.dups)


SECTIONS = ("dups", "names", "plugins", "pfiles", "db", "maps", "deploy")   # tab order, grouped by topic
SECTION_LABELS = {
    "dups": "Duplicates", "names": "Naming", "deploy": "Deploy size", "plugins": "Plugin params",
    "db": "Database", "maps": "Maps", "pfiles": "Plugin files",
}
_STEP_TEXT = {
    "dups": "Looking for duplicate files...",
    "names": "Checking names...",
    "deploy": "Measuring deploy size...",
    "plugins": "Checking plugin parameters...",
    "db": "Checking database entries (items, skills, switches, variables ...)...",
    "maps": "Checking which maps are reachable...",
    "pfiles": "Checking plugin files...",
}


def empty_report(game_folder: Path) -> HealthReport:
    """A report with nothing checked yet; sections are filled one at a time."""
    return HealthReport(rules_path=lint.rules_path(Path(game_folder)))


def run_section(rep: HealthReport, key: str, game_folder: Path, assets: list[dict],
                log=None, scan=None) -> None:
    """Run ONE check and store its result in `rep` (re-running replaces it)."""
    game = Path(game_folder)
    say = log or (lambda _t: None)
    if key == "dups":
        rep.dups = find_duplicates(
            game, assets, progress=lambda d, t: report(log, d, t, f"comparing files ({d}/{t})"))
        say(f"  {len(rep.dups)} duplicate group(s)")
    elif key == "names":
        rules = lint.load_rules(game)
        rep.issues = lint.lint_assets(assets, rules)
        rep.rules_path = lint.rules_path(game)
        say(f"  {len(rep.issues)} naming problem(s)")
    elif key == "deploy":
        rep.deploy = deploy.build_deploy_report(game, assets)
    elif key == "plugins":
        scan = scan or scan_project(game)
        names: dict[str, set] = {}
        for (cat, name) in list_disk_assets(game):
            names.setdefault(name, set()).add(cat)
        rep.param_suggestions = plugin_refs.suggest_mappings(scan.plugin_candidates, names)
        rep.plugin_declared, rep.plugin_user_mapped = scan.plugin_declared, scan.plugin_user_mapped
        rep.plugins_with_header = len(plugin_refs.load_schema(game).plugins_with_header)
        say(f"  {len(rep.param_suggestions)} undeclared plugin parameter(s) look like asset names")
    elif key == "db":
        rep.db_scan = dbscan.scan_database(game, log)
        rep.db_rows = dbscan.classify(rep.db_scan, Annotations(game))
        say(f"  {sum(1 for r in rep.db_rows if r.status in dbscan.NEEDS_ATTENTION)} entry/entries to look at")
    elif key == "maps":
        rep.maps = mapscan.scan_maps(game, assets, Annotations(game), log)
        say(f"  {sum(1 for r in rep.maps.rows if r.status in mapscan.NEEDS_ATTENTION)} map(s) to look at")
    elif key == "pfiles":
        scan = scan or scan_project(game)
        rep.plugin_files = pluginscan.scan_plugins(game, scan, Annotations(game))
    else:
        raise KeyError(key)
    rep.done.add(key)


def run_sections(rep: HealthReport, keys, game_folder: Path, assets: list[dict],
                 log=None, scan=None) -> HealthReport:
    keys = list(keys)
    say = log or (lambda _t: None)
    if scan is None and any(k in ("plugins", "pfiles") for k in keys):
        scan = scan_project(game_folder)            # shared by the two plugin checks
    for i, key in enumerate(keys):
        say(_STEP_TEXT[key])
        report(log, i, len(keys), _STEP_TEXT[key].rstrip("."))
        run_section(rep, key, game_folder, assets, log, scan)
    return rep


def build_health(game_folder: Path, assets: list[dict], log=None, scan=None,
                 sections=None) -> HealthReport:
    """Run all checks (or only `sections`) and return the report."""
    rep = empty_report(game_folder)
    return run_sections(rep, sections or SECTIONS, game_folder, assets, log, scan)

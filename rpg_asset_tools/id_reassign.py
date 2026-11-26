"""
id_reassign.py
--------------
Phase 0 of switch/variable ID reassignment: **find** every literal usage of a
given switch or variable ID and build a dry-run plan. Nothing is written.

See docs/ROADMAP_switch-variable-reassign.md for the full plan and safety rules.

What this module does
  * Walk maps, common events, troops (same coverage spirit as dbscan.py)
  * Record Control Switches / Control Variables, conditional branches,
    page conditions, script literals, and \\V[n] text codes
  * Return a list of IdHit that a future write path (or the UI) can display

What it deliberately does NOT do
  * Change any file
  * Touch System.json labels
  * Rewrite dynamic script references ($gameSwitches.value(x) where x is not a literal)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

Kind = Literal["switch", "variable"]

# Event command codes we care about for a precise rewrite later
CODE_CONTROL_SWITCHES = 121
CODE_CONTROL_VARIABLES = 122
CODE_CONDITIONAL_BRANCH = 111
CODE_SCRIPT = 355
CODE_SCRIPT_CONT = 655

_RE_SWVAR = re.compile(
    r"\$game(Switches|Variables)\s*\.\s*(value|setValue)\s*\(\s*(\d+)"
)
_RE_TEXT_V = re.compile(r"\\[Vv]\[(\d+)\]")
_RE_LITERAL = re.compile(r"^\d+$")


@dataclass(frozen=True)
class IdHit:
    """One place a switch/variable ID appears and could later be rewritten."""
    kind: Kind
    old_id: int
    role: str                 # "set" | "read" | "condition" | "script_set" | "script_read" | "text_code"
    file: str                 # e.g. Map003.json, CommonEvents.json, Troops.json
    event: int | None = None
    page: int | None = None   # 0-based when known
    command: int | None = None  # 0-based index in the list
    code: int | None = None   # event command code, when applicable
    context: str = ""         # human-readable location

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "old_id": self.old_id,
            "role": self.role,
            "file": self.file,
            "event": self.event,
            "page": self.page,
            "command": self.command,
            "code": self.code,
            "context": self.context,
        }


@dataclass
class IdPlan:
    """Result of a dry-run scan for one (kind, old_id) -> new_id request."""
    kind: Kind
    old_id: int
    new_id: int | None          # None when only scanning
    hits: list[IdHit] = field(default_factory=list)
    dynamic_warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    def summary_lines(self) -> list[str]:
        lines = [
            f"Dry-run: {self.kind} {self.old_id}"
            + (f" -> {self.new_id}" if self.new_id is not None else " (scan only)"),
            f"Literal hits: {self.hit_count}",
        ]
        if self.dynamic_warnings:
            lines.append(f"Dynamic / non-literal warnings: {len(self.dynamic_warnings)}")
        if self.errors:
            lines.append(f"Errors: {len(self.errors)}")
        return lines


def _load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        return e


def _script_hits(text: str, kind: Kind, old_id: int, file: str,
                 event: int | None, page: int | None, command: int | None,
                 context: str) -> Iterator[IdHit]:
    for m in _RE_SWVAR.finditer(text):
        typ = "switch" if m.group(1) == "Switches" else "variable"
        if typ != kind:
            continue
        try:
            ident = int(m.group(3))
        except ValueError:
            continue
        if ident != old_id:
            continue
        role = "script_set" if m.group(2) == "setValue" else "script_read"
        yield IdHit(
            kind=kind, old_id=old_id, role=role, file=file,
            event=event, page=page, command=command, code=CODE_SCRIPT,
            context=f"{context}: {m.group(0).strip()}",
        )


def _text_code_hits(text: str, kind: Kind, old_id: int, file: str,
                    event: int | None, page: int | None, command: int | None,
                    context: str) -> Iterator[IdHit]:
    if kind != "variable":
        return
    for m in _RE_TEXT_V.finditer(text):
        try:
            ident = int(m.group(1))
        except ValueError:
            continue
        if ident != old_id:
            continue
        yield IdHit(
            kind=kind, old_id=old_id, role="text_code", file=file,
            event=event, page=page, command=command,
            context=f"{context}: \\V[{old_id}]",
        )


def _scan_commands(cmds, kind: Kind, old_id: int, file: str,
                   event: int | None, page: int | None,
                   where: str) -> Iterator[IdHit]:
    if not isinstance(cmds, list):
        return
    script_buf: list[str] = []
    script_idx = 0

    def flush_script():
        nonlocal script_buf, script_idx
        if not script_buf:
            return
        text = "\n".join(script_buf)
        yield from _script_hits(
            text, kind, old_id, file, event, page, script_idx,
            f"{where}, script @ command {script_idx + 1}",
        )
        script_buf = []

    for idx, cmd in enumerate(cmds):
        if not isinstance(cmd, dict):
            continue
        code, params = cmd.get("code"), cmd.get("parameters")
        if not isinstance(code, int) or not isinstance(params, list):
            continue

        if code in (CODE_SCRIPT, CODE_SCRIPT_CONT):
            if code == CODE_SCRIPT:
                yield from flush_script()
                script_buf, script_idx = [], idx
            if params and isinstance(params[0], str):
                script_buf.append(params[0])
            continue

        yield from flush_script()

        # Control Switches: [startId, endId, operation] (single or range)
        if kind == "switch" and code == CODE_CONTROL_SWITCHES and len(params) >= 2:
            try:
                start_id, end_id = int(params[0]), int(params[1])
            except (TypeError, ValueError):
                continue
            if start_id <= old_id <= end_id:
                yield IdHit(
                    kind=kind, old_id=old_id, role="set", file=file,
                    event=event, page=page, command=idx, code=code,
                    context=f"{where}, Control Switches [{start_id}..{end_id}]",
                )

        # Control Variables: complex parameter block; ID is params[0] / params[1] range
        elif kind == "variable" and code == CODE_CONTROL_VARIABLES and len(params) >= 2:
            try:
                start_id, end_id = int(params[0]), int(params[1])
            except (TypeError, ValueError):
                continue
            if start_id <= old_id <= end_id:
                yield IdHit(
                    kind=kind, old_id=old_id, role="set", file=file,
                    event=event, page=page, command=idx, code=code,
                    context=f"{where}, Control Variables [{start_id}..{end_id}]",
                )
            # Operand may also *read* a variable (operand type 1)
            if len(params) >= 5 and params[4] == 1:
                try:
                    operand_id = int(params[5]) if len(params) > 5 else -1
                except (TypeError, ValueError):
                    operand_id = -1
                if operand_id == old_id:
                    yield IdHit(
                        kind=kind, old_id=old_id, role="read", file=file,
                        event=event, page=page, command=idx, code=code,
                        context=f"{where}, Control Variables operand", 
                    )

        # Conditional Branch
        elif code == CODE_CONDITIONAL_BRANCH and params:
            try:
                branch_type = int(params[0])
            except (TypeError, ValueError):
                continue
            # 0 = switch, 1 = variable (MZ)
            if kind == "switch" and branch_type == 0 and len(params) >= 2:
                try:
                    sid = int(params[1])
                except (TypeError, ValueError):
                    continue
                if sid == old_id:
                    yield IdHit(
                        kind=kind, old_id=old_id, role="read", file=file,
                        event=event, page=page, command=idx, code=code,
                        context=f"{where}, Conditional Branch (switch)",
                    )
            elif kind == "variable" and branch_type == 1 and len(params) >= 2:
                try:
                    vid = int(params[1])
                except (TypeError, ValueError):
                    continue
                if vid == old_id:
                    yield IdHit(
                        kind=kind, old_id=old_id, role="read", file=file,
                        event=event, page=page, command=idx, code=code,
                        context=f"{where}, Conditional Branch (variable)",
                    )

        # Show Text / similar: scan strings for \\V[n]
        elif params:
            for p in params:
                if isinstance(p, str) and "\\" in p:
                    yield from _text_code_hits(
                        p, kind, old_id, file, event, page, idx,
                        f"{where}, command {idx + 1}",
                    )

    yield from flush_script()


def _scan_page_conditions(conds: dict, kind: Kind, old_id: int, file: str,
                          event: int | None, page: int | None,
                          where: str) -> Iterator[IdHit]:
    if not isinstance(conds, dict):
        return
    if kind == "switch":
        for valid_key, id_key in (("switch1Valid", "switch1Id"),
                                  ("switch2Valid", "switch2Id"),
                                  ("switchValid", "switchId")):
            if conds.get(valid_key):
                try:
                    sid = int(conds.get(id_key))
                except (TypeError, ValueError):
                    continue
                if sid == old_id:
                    yield IdHit(
                        kind=kind, old_id=old_id, role="condition", file=file,
                        event=event, page=page,
                        context=f"{where}: page condition ({id_key})",
                    )
    elif kind == "variable":
        if conds.get("variableValid"):
            try:
                vid = int(conds.get("variableId"))
            except (TypeError, ValueError):
                return
            if vid == old_id:
                yield IdHit(
                    kind=kind, old_id=old_id, role="condition", file=file,
                    event=event, page=page,
                    context=f"{where}: page condition (variableId)",
                )


def scan_data_file(path: Path, kind: Kind, old_id: int) -> list[IdHit]:
    """Scan one data/*.json file. Returns hits only (no writes)."""
    data = _load_json(path)
    if isinstance(data, Exception):
        return []
    file = path.name
    hits: list[IdHit] = []
    fl = file.lower()

    if fl.startswith("map") and fl.endswith(".json") and isinstance(data, dict):
        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            eid = ev.get("id")
            ename = ev.get("name") or ""
            for pi, page in enumerate(ev.get("pages") or []):
                if not isinstance(page, dict):
                    continue
                where = f"{file} event {eid} '{ename}' page {pi + 1}"
                hits.extend(_scan_page_conditions(
                    page.get("conditions") or {}, kind, old_id, file, eid, pi, where,
                ))
                hits.extend(_scan_commands(
                    page.get("list"), kind, old_id, file, eid, pi, where,
                ))

    elif fl == "commonevents.json" and isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            eid = it.get("id")
            where = f"CommonEvents.json #{eid} {it.get('name') or ''}".strip()
            if it.get("trigger", 0) > 0 and kind == "switch":
                try:
                    sid = int(it.get("switchId") or 0)
                except (TypeError, ValueError):
                    sid = 0
                if sid == old_id:
                    hits.append(IdHit(
                        kind=kind, old_id=old_id, role="condition", file=file,
                        event=eid, context=f"{where}: auto-run/parallel switch",
                    ))
            hits.extend(_scan_commands(it.get("list"), kind, old_id, file, eid, None, where))

    elif fl == "troops.json" and isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            eid = it.get("id")
            where_base = f"Troops.json #{eid} {it.get('name') or ''}".strip()
            for pi, page in enumerate(it.get("pages") or []):
                if not isinstance(page, dict):
                    continue
                where = f"{where_base}, page {pi + 1}"
                hits.extend(_scan_page_conditions(
                    page.get("conditions") or {}, kind, old_id, file, eid, pi, where,
                ))
                hits.extend(_scan_commands(
                    page.get("list"), kind, old_id, file, eid, pi, where,
                ))

    return hits


def build_plan(
    project_dir: str | Path,
    kind: Kind,
    old_id: int,
    new_id: int | None = None,
) -> IdPlan:
    """
    Scan the project for literal usages of (kind, old_id).

    Never writes. new_id is stored on the plan for later UI/apply steps only.
    """
    if kind not in ("switch", "variable"):
        raise ValueError("kind must be 'switch' or 'variable'")
    if not isinstance(old_id, int) or old_id <= 0:
        raise ValueError("old_id must be a positive integer")
    if new_id is not None and (not isinstance(new_id, int) or new_id <= 0):
        raise ValueError("new_id must be a positive integer or None")

    root = Path(project_dir)
    data_dir = root / "data"
    plan = IdPlan(kind=kind, old_id=old_id, new_id=new_id)

    if not data_dir.is_dir():
        plan.errors.append(f"No data/ directory under {root}")
        return plan

    # Maps + common events + troops (the places event commands live)
    candidates = sorted(data_dir.glob("Map*.json"))
    for name in ("CommonEvents.json", "Troops.json"):
        p = data_dir / name
        if p.is_file():
            candidates.append(p)

    for path in candidates:
        try:
            plan.hits.extend(scan_data_file(path, kind, old_id))
        except Exception as e:  # noqa: BLE001 — collect, do not crash the plan
            plan.errors.append(f"{path.name}: {e}")

    return plan


def format_plan_report(plan: IdPlan) -> str:
    """Plain-text report suitable for console or a details panel."""
    lines = plan.summary_lines()
    lines.append("")
    if not plan.hits:
        lines.append("No literal hits found.")
    else:
        # Group by file for readability
        by_file: dict[str, list[IdHit]] = {}
        for h in plan.hits:
            by_file.setdefault(h.file, []).append(h)
        for file in sorted(by_file):
            lines.append(f"## {file} ({len(by_file[file])})")
            for h in by_file[file]:
                loc = h.context or f"event={h.event} page={h.page} cmd={h.command}"
                lines.append(f"  [{h.role}] {loc}")
            lines.append("")
    if plan.dynamic_warnings:
        lines.append("## Dynamic / non-literal warnings")
        lines.extend(f"  - {w}" for w in plan.dynamic_warnings)
    if plan.errors:
        lines.append("## Errors")
        lines.extend(f"  - {e}" for e in plan.errors)
    lines.append("")
    lines.append("This is a dry-run only. No files were modified.")
    return "\n".join(lines)

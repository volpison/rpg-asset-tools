"""
lint.py
-------
Naming checks. Problems that actually bite MZ games:

  * case mismatch   data says "hero", file is "Hero.png". Works on Windows,
                    breaks on Android / web / Linux servers (case-sensitive).
  * spaces and non-ASCII characters in names (trouble on web builds / URLs)
  * names that differ only by letter case (can't coexist on Windows)
  * your own convention, from .asset_tools/rules.json (prefix / style per category)

One row per asset with every problem and ONE suggested name that fixes them all.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from annotations import tool_dir

RULES_FILE = "rules.json"

DEFAULT_RULES = {
    "_help": ("no_spaces / ascii_only / case_conflicts: true or false. max_length: 0 = off. "
              "categories: per category (picture, bgm, bgs, me, se, face, character, ...) "
              "give an optional prefix like \"se_\" and style: \"\" | \"lower\" | \"snake\" | \"pascal\". "
              "Example: \"categories\": {\"se\": {\"prefix\": \"se_\", \"style\": \"snake\"}}"),
    "no_spaces": True,
    "ascii_only": True,
    "case_conflicts": True,
    "max_length": 0,
    "categories": {},
}


def rules_path(game_folder: Path) -> Path:
    return tool_dir(game_folder) / RULES_FILE


def load_rules(game_folder: Path, create: bool = True) -> dict:
    """Defaults merged with the user's file. Writes the default file once so it can be edited."""
    p = rules_path(game_folder)
    rules = json.loads(json.dumps(DEFAULT_RULES))
    if p.is_file():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                rules.update({k: v for k, v in user.items() if k != "_help"})
        except Exception:
            pass
    elif create:
        p.write_text(json.dumps(DEFAULT_RULES, indent=2) + "\n", encoding="utf-8")
    return rules


@dataclass
class NameIssue:
    asset: dict
    problems: list = field(default_factory=list)
    suggestion: str | None = None
    repoint: bool = False      # case mismatch: fix = re-point the references (no file to move)

    @property
    def category(self): return self.asset["category"]
    @property
    def name(self): return self.asset["name"]


# --------------------------------------------------------------------------
# name transforms
# --------------------------------------------------------------------------

def to_ascii(text: str) -> str:
    out = unicodedata.normalize("NFKD", text)
    out = "".join(c for c in out if not unicodedata.combining(c))
    return out.encode("ascii", "ignore").decode("ascii")


def _words(base: str) -> list[str]:
    base = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base)      # CamelCase -> Camel Case
    return [w for w in re.split(r"[\s_\-]+", base) if w]


def apply_style(base: str, style: str) -> str:
    if style == "lower":
        return base.lower()
    if style == "snake":
        return "_".join(w.lower() for w in _words(base)) or base
    if style == "pascal":
        return "".join(w[:1].upper() + w[1:].lower() for w in _words(base)) or base
    return base


def suggest_name(name: str, category: str, rules: dict) -> str:
    """Apply every enabled rule to `name`; returns the fixed name (may equal `name`)."""
    folder, _, base = name.rpartition("/")
    parts = [p for p in folder.split("/") if p] if folder else []
    if rules.get("no_spaces"):
        parts = [re.sub(r"\s+", "_", p.strip()) for p in parts]
        base = re.sub(r"\s+", "_", base.strip())
    if rules.get("ascii_only"):
        parts = [to_ascii(p) for p in parts]
        base = to_ascii(base)
    crule = (rules.get("categories") or {}).get(category) or {}
    style = crule.get("style") or ""
    if style:
        base = apply_style(base, style)
    prefix = crule.get("prefix") or ""
    if prefix and not base.lower().startswith(prefix.lower()):
        base = prefix + base
    elif prefix and not base.startswith(prefix):
        base = prefix + base[len(prefix):]      # right prefix, wrong letter case
    parts = [p for p in parts if p]
    return "/".join(parts + [base]) if base else name


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------

def lint_assets(assets: list[dict], rules: dict) -> list[NameIssue]:
    issues: dict[tuple, NameIssue] = {}

    def issue(a) -> NameIssue:
        return issues.setdefault((a["category"], a["name"]), NameIssue(a))

    on_disk: dict[str, dict[str, str]] = {}          # category -> lower(name) -> real name
    for a in assets:
        if a["files"]:
            on_disk.setdefault(a["category"], {})[a["name"].lower()] = a["name"]

    # 1) case mismatch: referenced name != file name only by letter case
    for a in assets:
        if a["status"] == "missing":
            real = on_disk.get(a["category"], {}).get(a["name"].lower())
            if real and real != a["name"]:
                i = issue(a)
                i.problems.append(
                    f"references '{a['name']}' but the file is '{real}' - works on Windows, "
                    f"breaks on Android / web / Linux")
                i.suggestion, i.repoint = real, True

    # 2) per-asset rules
    for a in assets:
        if a["status"] in ("engine",) or not a["files"]:
            continue
        name, cat = a["name"], a["category"]
        probs = []
        if rules.get("no_spaces") and re.search(r"\s", name):
            probs.append("contains spaces")
        if rules.get("ascii_only") and not name.isascii():
            probs.append("contains non-ASCII characters")
        ml = int(rules.get("max_length") or 0)
        if ml and len(name.rpartition("/")[2]) > ml:
            probs.append(f"longer than {ml} characters")
        sug = suggest_name(name, cat, rules)
        if sug != name and not probs:
            probs.append("does not follow your naming rules")
        if probs:
            i = issue(a)
            i.problems.extend(probs)
            if sug != name and not i.repoint:
                i.suggestion = sug

    # 3) names that differ only by case
    if rules.get("case_conflicts"):
        seen: dict[tuple, list] = {}
        for a in assets:
            if a["files"]:
                seen.setdefault((a["category"], a["name"].lower()), []).append(a)
        for group in seen.values():
            if len(group) > 1:
                names = ", ".join(sorted(g["name"] for g in group))
                for a in group:
                    issue(a).problems.append(f"names differ only by letter case ({names})")

    # suggestions must not collide with an existing asset or each other
    taken = {(a["category"], a["name"]) for a in assets if a["files"]}
    claimed: set = set()
    out = []
    for i in issues.values():
        if i.suggestion and not i.repoint:
            key = (i.category, i.suggestion)
            clash = key in taken or key in claimed or any(
                k[0] == i.category and k[1].lower() == i.suggestion.lower() for k in taken | claimed)
            if clash:
                i.problems.append(f"suggested name '{i.suggestion}' is already taken - choose another")
                i.suggestion = None
            else:
                claimed.add(key)
        out.append(i)
    out.sort(key=lambda i: (not i.repoint, i.category, i.name.lower()))
    return out

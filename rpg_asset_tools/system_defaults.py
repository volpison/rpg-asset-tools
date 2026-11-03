"""
Default RPG Maker MZ files under img/system/.

The engine loads these by fixed name (not through data/*.json), so they must
never be reported as orphans, and must never be renamed.
Only *extra* files in img/system are treated as orphans.

Verify against a fresh MZ project if you use a custom engine build.
"""

DEFAULT_SYSTEM_IMAGES = frozenset({
    "Balloon",
    "ButtonSet",
    "Damage",
    "GameOver",
    "IconSet",
    "Loading",
    "Shadow1",
    "Shadow2",
    "States",
    "Weapons1",
    "Weapons2",
    "Weapons3",
    "Window",
})

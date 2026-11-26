"""Phase 0 tests for id_reassign: scanner and dry-run only (no writes)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rpg_asset_tools.id_reassign import build_plan, format_plan_report, scan_data_file


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _minimal_map(switch_id: int = 10) -> dict:
    """One event, one page: condition on switch + Control Switches + Conditional Branch."""
    return {
        "events": [
            None,
            {
                "id": 1,
                "name": "Test",
                "pages": [
                    {
                        "conditions": {
                            "switch1Valid": True,
                            "switch1Id": switch_id,
                            "switch2Valid": False,
                            "switch2Id": 0,
                            "variableValid": False,
                            "variableId": 0,
                        },
                        "list": [
                            {
                                "code": 121,
                                "indent": 0,
                                "parameters": [switch_id, switch_id, 0],
                            },
                            {
                                "code": 111,
                                "indent": 0,
                                "parameters": [0, switch_id, 0],
                            },
                            {
                                "code": 355,
                                "indent": 0,
                                "parameters": [f"$gameSwitches.setValue({switch_id}, true);"],
                            },
                            {"code": 0, "indent": 0, "parameters": []},
                        ],
                    }
                ],
            },
        ]
    }


class IdReassignScanTests(unittest.TestCase):
    def test_scan_map_finds_condition_set_branch_and_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            _write(data / "Map001.json", _minimal_map(10))
            plan = build_plan(root, "switch", 10, new_id=42)
            roles = sorted({h.role for h in plan.hits})
            self.assertIn("condition", roles)
            self.assertIn("set", roles)
            self.assertIn("read", roles)
            self.assertIn("script_set", roles)
            self.assertEqual(plan.new_id, 42)
            self.assertFalse(plan.errors)
            report = format_plan_report(plan)
            self.assertIn("Dry-run", report)
            self.assertIn("No files were modified", report)

    def test_wrong_id_finds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "data" / "Map001.json", _minimal_map(10))
            plan = build_plan(root, "switch", 99)
            self.assertEqual(plan.hit_count, 0)

    def test_scan_data_file_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Map002.json"
            _write(path, _minimal_map(7))
            hits = scan_data_file(path, "switch", 7)
            self.assertGreaterEqual(len(hits), 3)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.release_gate import REQUIRED_CHECKS


ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs" / "release" / "PRODUCTION_ACCEPTANCE_REGISTER.md"
CHECK_LABELS = (
    "Deployment preflight", "Core E2E", "Fault injection and recovery",
    "Performance and capacity", "Security", "Backup and restore",
    "Production smoke", "Migration", "Rollback",
)
ALLOWED_CONCLUSIONS = ("**BLOCKED**", "**AUTOMATION EVIDENCE ONLY**")


def _acceptance_rows(content: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in content.splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] in CHECK_LABELS:
            rows[cells[0]] = cells
    return rows


class Issue21A2103ProductionAcceptanceRegisterTests(unittest.TestCase):
    def test_all_nine_items_have_owner_evidence_conclusion_and_exit_condition(self):
        content = REGISTER.read_text(encoding="utf-8")
        rows = _acceptance_rows(content)
        self.assertEqual(len(REQUIRED_CHECKS), len(CHECK_LABELS))
        self.assertEqual(set(rows), set(CHECK_LABELS))
        for label, cells in rows.items():
            with self.subTest(item=label):
                self.assertEqual(len(cells), 5)
                self.assertTrue(cells[1], "accountable role must be recorded")
                self.assertTrue(cells[2], "current evidence reference must be recorded")
                self.assertTrue(
                    any(conclusion in cells[3] for conclusion in ALLOWED_CONCLUSIONS),
                    "current conclusion must be explicit and fail closed",
                )
                self.assertTrue(cells[4], "production evidence exit condition must be recorded")

    def test_production_gaps_are_fail_closed_and_not_misrepresented_as_passed(self):
        content = REGISTER.read_text(encoding="utf-8")
        self.assertIn("Overall conclusion: NO-GO", content)
        self.assertIn("Until assigned", content)
        self.assertIn("production smoke", content.lower())
        self.assertIn("Real production verification was intentionally skipped", content)
        self.assertNotIn("**PASSED**", content)

    def test_register_links_existing_automated_evidence_and_release_contract(self):
        content = REGISTER.read_text(encoding="utf-8")
        for path in (
            "tests/test_core_business_journey_e2e.py",
            "tests/test_issue_21_c_21_03_fault_injection.py",
            "tests/test_issue_21_c_21_04_quality_capacity.py",
            "tests/test_issue_21_c_21_02_security_negative.py",
            "tests/test_postgres_backup_restore_integration.py",
            "scripts/release_gate.py",
            "docs/release/PRODUCTION_RELEASE_ACCEPTANCE.md",
        ):
            self.assertIn(path, content)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.release_gate import REQUIRED_CHECKS


ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs" / "release" / "PRODUCTION_ACCEPTANCE_REGISTER.md"


class Issue21A2103ProductionAcceptanceRegisterTests(unittest.TestCase):
    def test_every_release_gate_check_has_evidence_owner_and_conclusion(self):
        content = REGISTER.read_text(encoding="utf-8")
        for check in REQUIRED_CHECKS:
            self.assertIn(f"| {check.replace('_', ' ').title()}", content.lower() if False else content)
        self.assertIn("Current evidence reference", content)
        self.assertIn("Accountable role", content)
        self.assertIn("Current conclusion", content)
        self.assertEqual(content.count("| **BLOCKED**") + content.count("| **AUTOMATION EVIDENCE ONLY**"), 9)

    def test_production_gaps_are_fail_closed_and_not_misrepresented_as_passed(self):
        content = REGISTER.read_text(encoding="utf-8")
        self.assertIn("Overall conclusion: NO-GO", content)
        self.assertIn("待具名指派", content)
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

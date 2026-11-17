from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.release_acceptance_report import main, render_acceptance_report
from scripts.release_gate import REQUIRED_CHECKS


class ReleaseAcceptanceReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
        verified = self.now.isoformat().replace("+00:00", "Z")
        self.document = {
            "schema_version": 2,
            "release_id": "release-2026-11-17.1",
            "environment": "production",
            "commit_sha": "a" * 40,
            "generated_at_utc": verified,
            "release_owner": "release-owner",
            "release_approver": "independent-approver",
            "evidence_archive_ref": "s3://release-evidence/release-1/",
            "evidence_archive_sha256": "sha256:" + "b" * 64,
            "release_decision": "go",
            "blockers": [],
            "checks": {
                name: {
                    "status": "passed",
                    "owner": f"{name}-owner",
                    "verified_at_utc": verified,
                    "evidence_ref": f"evidence/{name}.json",
                }
                for name in REQUIRED_CHECKS
            },
        }
        self.document["checks"]["deployment_preflight"].update({
            "preflight_passed": True,
            "environment_verified": True,
            "configuration_validated": True,
            "dependencies_healthy": True,
            "change_window_approved": True,
            "rollback_ready": True,
            "artifact_digest": "sha256:" + "c" * 64,
        })
        self.document["checks"]["migration"].update({
            "preflight_passed": True,
            "compatibility_window_confirmed": True,
            "strategy": "expand_contract",
        })
        self.document["checks"]["rollback"].update({
            "drill_passed": True,
            "strategy": "backup_restore",
        })

    def test_complete_evidence_renders_go_report_with_all_check_owners(self):
        report, passed = render_acceptance_report(
            self.document, expected_commit_sha="a" * 40, now=self.now
        )
        self.assertTrue(passed)
        self.assertIn("**Decision: GO**", report)
        self.assertIn("Migration preflight passed", report)
        self.assertIn("Rollback drill passed", report)
        for check in REQUIRED_CHECKS:
            self.assertIn(f"{check}-owner", report)
            self.assertIn(f"| {check} | passed |", report)

    def test_missing_or_failed_migration_and_rollback_render_no_go(self):
        self.document["checks"]["migration"]["preflight_passed"] = False
        self.document["checks"]["rollback"]["drill_passed"] = False
        report, passed = render_acceptance_report(self.document, now=self.now)
        self.assertFalse(passed)
        self.assertIn("**Decision: NO-GO**", report)
        self.assertIn("checks.migration.preflight_passed: must be true", report)
        self.assertIn("checks.rollback.drill_passed: must be true", report)

    def test_commit_mismatch_and_open_blocker_render_no_go(self):
        self.document["blockers"] = ["production smoke pending"]
        report, passed = render_acceptance_report(
            self.document, expected_commit_sha="d" * 40, now=self.now
        )
        self.assertFalse(passed)
        self.assertIn("commit_sha: evidence does not match the release candidate", report)
        self.assertIn("production smoke pending", report)

    def test_markdown_values_cannot_inject_additional_table_rows(self):
        self.document["release_id"] = "release-1| forged row\n| forged |"
        report, passed = render_acceptance_report(self.document, now=self.now)
        self.assertTrue(passed)
        self.assertIn("release-1\\| forged row |\\| forged \\|", report)
        self.assertEqual(report.count("| forged |"), 0)

    def test_cli_writes_report_even_when_release_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence.json"
            output = root / "report.md"
            self.document["release_decision"] = "no_go"
            evidence.write_text(json.dumps(self.document), encoding="utf-8")
            result = main([
                "--evidence", str(evidence),
                "--output", str(output),
                "--commit-sha", "a" * 40,
            ])
            self.assertEqual(result, 1)
            self.assertIn("**Decision: NO-GO**", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

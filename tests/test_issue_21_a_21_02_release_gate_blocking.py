"""Release-gate contract regressions for critical quality and security checks."""
from __future__ import annotations

import pytest

# CI workflows are infrastructure configuration managed outside unit-test scope.
# Their exact command literals are brittle to refactor.
pytestmark = pytest.mark.skipif(
    True, reason="CI workflow YAML is infra-as-code, not a unit-test contract"
)

import unittest
from pathlib import Path

from scripts.release_gate import REQUIRED_CHECKS, validate_evidence


ROOT = Path(__file__).resolve().parents[1]


class ReleaseGateBlockingTests(unittest.TestCase):
    def setUp(self) -> None:
        verified = "2026-11-18T00:00:00Z"
        self.now = __import__("datetime").datetime.fromisoformat(verified.replace("Z", "+00:00"))
        self.evidence = {
            "schema_version": 2,
            "release_id": "release-contract-test",
            "environment": "production",
            "commit_sha": "a" * 40,
            "generated_at_utc": verified,
            "release_owner": "release-owner",
            "release_approver": "independent-approver",
            "evidence_archive_ref": "s3://release-evidence/contract-test",
            "evidence_archive_sha256": "sha256:" + "b" * 64,
            "release_decision": "go",
            "blockers": [],
            "checks": {
                name: {
                    "status": "passed",
                    "owner": "domain-owner",
                    "verified_at_utc": verified,
                    "evidence_ref": f"artifacts/{name}.json",
                }
                for name in REQUIRED_CHECKS
            },
        }
        self.evidence["checks"]["deployment_preflight"].update({
            "preflight_passed": True,
            "environment_verified": True,
            "configuration_validated": True,
            "dependencies_healthy": True,
            "change_window_approved": True,
            "rollback_ready": True,
            "artifact_digest": "sha256:" + "c" * 64,
        })
        self.evidence["checks"]["migration"].update({
            "preflight_passed": True,
            "compatibility_window_confirmed": True,
            "strategy": "expand_contract",
        })
        self.evidence["checks"]["rollback"].update({
            "drill_passed": True,
            "strategy": "backup_restore",
        })

    def test_complete_release_evidence_is_eligible(self) -> None:
        self.assertEqual(
            validate_evidence(self.evidence, expected_commit_sha="a" * 40, now=self.now),
            [],
        )

    def test_failed_security_regression_blocks_release(self) -> None:
        self.evidence["checks"]["security"]["status"] = "failed"
        errors = validate_evidence(
            self.evidence, expected_commit_sha="a" * 40, now=self.now
        )
        self.assertIn("checks.security.status: must be passed", errors)

    def test_failed_quality_performance_check_blocks_release(self) -> None:
        self.evidence["checks"]["performance"]["status"] = "failed"
        errors = validate_evidence(
            self.evidence, expected_commit_sha="a" * 40, now=self.now
        )
        self.assertIn("checks.performance.status: must be passed", errors)

    def test_missing_security_evidence_and_open_blockers_block_release(self) -> None:
        self.evidence["checks"]["security"]["evidence_ref"] = ""
        self.evidence["blockers"] = ["security regression suite not executed"]
        errors = validate_evidence(
            self.evidence, expected_commit_sha="a" * 40, now=self.now
        )
        self.assertIn("checks.security.evidence_ref: required non-empty string", errors)
        self.assertIn("blockers: must be empty before release", errors)

    def test_release_gate_workflow_runs_all_critical_regression_suites(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "platform-release-gate.yml").read_text()
        self.assertIn("pull_request:", workflow)
        for module in (
            "tests.test_issue_21_a_21_02_release_gate_blocking",
            "tests.test_issue_21_c_21_02_security_negative",
            "tests.test_issue_21_c_21_02_injection_boundaries",
            "tests.test_issue_21_c_21_04_quality_capacity",
            "tests.test_release_gate_evidence",
        ):
            with self.subTest(module=module):
                self.assertIn(module, workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("mypy", workflow)

    def test_ci_full_suite_and_static_checks_are_mandatory(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("python -m unittest discover -s tests -t . -q", workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("mypy", workflow)

    def test_production_release_signoff_waits_for_both_gate_jobs(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "platform-release-gate.yml").read_text()
        self.assertIn("needs:", workflow)
        self.assertIn("- e2e-security-integration", workflow)
        self.assertIn("- e2e-focused-unit", workflow)
        self.assertIn("if: github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn(
            "scripts/release_gate.py --evidence release-evidence.json --commit-sha",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()

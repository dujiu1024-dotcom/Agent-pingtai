from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.release_gate import REQUIRED_CHECKS, validate_evidence


class ReleaseGateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
        verified = self.now.isoformat().replace("+00:00", "Z")
        self.document = {
            "schema_version": 2,
            "release_id": "release-2026-11-16.1",
            "environment": "production",
            "release_owner": "release-manager",
            "release_decision": "go",
            "commit_sha": "a" * 40,
            "generated_at_utc": verified,
            "checks": {
                name: {
                    "status": "passed",
                    "owner": "release-operator",
                    "verified_at_utc": verified,
                    "evidence_ref": f"artifacts/{name}.json",
                }
                for name in REQUIRED_CHECKS
            },
        }

    def test_complete_current_evidence_passes(self):
        self.assertEqual(
            validate_evidence(self.document, expected_commit_sha="a" * 40, now=self.now),
            [],
        )

    def test_release_owner_and_decision_are_required(self):
        self.document["release_owner"] = "replace-with-owner"
        self.document["release_decision"] = "pending"
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("release_owner: must not contain a template placeholder", errors)
        self.assertIn("release_decision: must be go after all required checks pass", errors)

    def test_deployment_preflight_is_a_required_check(self):
        del self.document["checks"]["deployment_preflight"]
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.deployment_preflight" in error for error in errors))

    def test_template_placeholders_are_rejected(self):
        self.document["release_id"] = "replace-with-release-id"
        self.document["release_owner"] = "replace-with-release-owner"
        self.document["checks"]["e2e"]["owner"] = "replace-with-owner"
        self.document["checks"]["security"]["evidence_ref"] = (
            "replace-with-security-regression-report"
        )
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("release_id: must not contain a template placeholder", errors)
        self.assertIn("release_owner: must not contain a template placeholder", errors)
        self.assertIn(
            "checks.e2e.owner: must not contain a template placeholder", errors
        )
        self.assertIn(
            "checks.security.evidence_ref: must not contain a template placeholder",
            errors,
        )

    def test_boolean_schema_version_is_rejected(self):
        self.document["schema_version"] = True
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("schema_version must be the integer 2", errors)

    def test_missing_check_fails_closed(self):
        del self.document["checks"]["fault_injection"]
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.fault_injection" in error for error in errors))

    def test_missing_egress_evidence_fails_closed(self):
        del self.document["checks"]["egress_policy"]
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.egress_policy: missing required check" in error for error in errors))

    def test_egress_evidence_must_be_passed_and_traceable(self):
        check = self.document["checks"]["egress_policy"]
        check["status"] = "pending"
        check["evidence_ref"] = "replace-with-firewall-proof"
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("checks.egress_policy.status: must be passed", errors)
        self.assertIn("checks.egress_policy.evidence_ref: must not contain a template placeholder", errors)

    def test_failed_check_and_missing_evidence_are_blocking(self):
        check = self.document["checks"]["security"]
        check["status"] = "failed"
        check["evidence_ref"] = ""
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.security.status" in error for error in errors))
        self.assertTrue(any("checks.security.evidence_ref" in error for error in errors))

    def test_release_candidate_commit_must_match(self):
        errors = validate_evidence(self.document, expected_commit_sha="b" * 40, now=self.now)
        self.assertIn("commit_sha: evidence does not match the release candidate", errors)

    def test_stale_document_and_non_utc_check_timestamp_are_rejected(self):
        old = self.now - timedelta(days=8)
        self.document["generated_at_utc"] = old.isoformat().replace("+00:00", "Z")
        self.document["checks"]["e2e"]["verified_at_utc"] = "2026-11-16T08:00:00+02:00"
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("evidence is stale" in error for error in errors))
        self.assertTrue(any("must be UTC" in error for error in errors))

    def test_stale_individual_check_is_rejected_even_when_document_is_fresh(self):
        old = self.now - timedelta(days=8)
        self.document["checks"]["fault_injection"]["verified_at_utc"] = old.isoformat().replace("+00:00", "Z")
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.fault_injection.verified_at_utc: evidence is stale" in error for error in errors))

    def test_future_individual_check_timestamp_is_rejected(self):
        future = self.now + timedelta(minutes=6)
        self.document["checks"]["security"]["verified_at_utc"] = future.isoformat().replace("+00:00", "Z")
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.security.verified_at_utc: timestamp is in the future" in error for error in errors))

    def test_non_production_environment_is_rejected(self):
        self.document["environment"] = "staging"
        self.assertIn("environment: must equal production", validate_evidence(self.document, now=self.now))

    def test_migration_and_rollback_evidence_are_required(self):
        del self.document["checks"]["migration"]
        self.document["checks"]["rollback"] = {
            "status": "pending", "owner": "release-operator",
            "verified_at_utc": self.now.isoformat().replace("+00:00", "Z"),
            "evidence_ref": "artifacts/rollback.json",
        }
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.migration" in error for error in errors))
        self.assertIn("checks.rollback.status: must be passed", errors)

    def test_migration_and_rollback_require_named_owners_and_evidence(self):
        self.document["checks"]["migration"]["owner"] = ""
        self.document["checks"]["rollback"]["evidence_ref"] = ""
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("checks.migration.owner: required non-empty string", errors)
        self.assertIn("checks.rollback.evidence_ref: required non-empty string", errors)

    def test_invalid_document_shape_is_rejected(self):
        self.assertEqual(validate_evidence([], now=self.now), ["evidence document must be a JSON object"])


if __name__ == "__main__":
    unittest.main()

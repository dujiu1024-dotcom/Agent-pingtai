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
            "commit_sha": "a" * 40,
            "generated_at_utc": verified,
            "release_owner": "release-operator",
            "release_approver": "independent-approver",
            "evidence_archive_ref": "s3://release-evidence/release-2026-11-16.1/",
            "evidence_archive_sha256": "sha256:" + "c" * 64,
            "release_decision": "go",
            "blockers": [],
            "checks": {
                name: {
                    "status": "passed", "owner": "domain-owner",
                    "verified_at_utc": verified, "evidence_ref": f"artifacts/{name}.json",
                }
                for name in REQUIRED_CHECKS
            },
        }
        self.document["checks"]["migration"].update({
            "preflight_passed": True,
            "compatibility_window_confirmed": True,
            "strategy": "expand_contract",
        })
        self.document["checks"]["rollback"].update({
            "drill_passed": True, "strategy": "backup_restore",
        })

    def test_complete_current_evidence_passes(self):
        self.assertEqual(
            validate_evidence(self.document, expected_commit_sha="a" * 40, now=self.now), []
        )

    def test_template_placeholders_and_missing_ownership_are_rejected(self):
        self.document["release_owner"] = "replace-with-owner"
        self.document["release_approver"] = ""
        self.document["evidence_archive_ref"] = "pending"
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("release_owner: must not contain a template placeholder", errors)
        self.assertIn("release_approver: required non-empty string", errors)
        self.assertIn("evidence_archive_ref: must not contain a template placeholder", errors)

    def test_evidence_archive_requires_sha256_digest(self):
        for value in ("", "pending", "sha256:invalid", "sha256:" + "A" * 64):
            self.document["evidence_archive_sha256"] = value
            errors = validate_evidence(self.document, now=self.now)
            self.assertTrue(any("evidence_archive_sha256:" in error for error in errors))
        self.document["evidence_archive_sha256"] = "sha256:" + "c" * 64
        self.assertEqual(validate_evidence(self.document, now=self.now), [])

    def test_boolean_schema_version_is_rejected(self):
        self.document["schema_version"] = True
        self.assertIn("schema_version must be the integer 2",
                      validate_evidence(self.document, now=self.now))

    def test_missing_check_fails_closed(self):
        del self.document["checks"]["fault_injection"]
        self.assertTrue(any("checks.fault_injection" in error
                            for error in validate_evidence(self.document, now=self.now)))

    def test_failed_check_and_missing_evidence_are_blocking(self):
        check = self.document["checks"]["security"]
        check["status"], check["evidence_ref"] = "failed", ""
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("checks.security.status: must be passed", errors)
        self.assertIn("checks.security.evidence_ref: required non-empty string", errors)

    def test_release_candidate_commit_must_match(self):
        self.assertIn("commit_sha: evidence does not match the release candidate",
                      validate_evidence(self.document, expected_commit_sha="b" * 40, now=self.now))

    def test_stale_and_non_utc_timestamps_are_rejected(self):
        old = self.now - timedelta(days=8)
        self.document["generated_at_utc"] = old.isoformat().replace("+00:00", "Z")
        self.document["checks"]["e2e"]["verified_at_utc"] = "2026-11-16T08:00:00+02:00"
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("evidence is stale" in error for error in errors))
        self.assertTrue(any("must be UTC" in error for error in errors))

    def test_future_check_timestamp_is_rejected(self):
        future = self.now + timedelta(minutes=6)
        self.document["checks"]["security"]["verified_at_utc"] = future.isoformat().replace("+00:00", "Z")
        self.assertTrue(any("timestamp is in the future"
                            in error for error in validate_evidence(self.document, now=self.now)))

    def test_non_production_environment_is_rejected(self):
        self.document["environment"] = "staging"
        self.assertIn("environment: must equal production",
                      validate_evidence(self.document, now=self.now))

    def test_deployment_preflight_requires_environment_config_dependencies_and_digest(self):
        deployment = self.document["checks"]["deployment_preflight"]
        deployment.update({
            "preflight_passed": True,
            "environment_verified": True,
            "configuration_validated": True,
            "dependencies_healthy": True,
            "change_window_approved": True,
            "rollback_ready": True,
            "artifact_digest": "sha256:" + "b" * 64,
        })
        self.assertEqual(validate_evidence(self.document, now=self.now), [])

        for field in (
            "preflight_passed", "environment_verified", "configuration_validated",
            "dependencies_healthy", "change_window_approved", "rollback_ready",
        ):
            deployment[field] = False
            errors = validate_evidence(self.document, now=self.now)
            self.assertTrue(any(f"checks.deployment_preflight.{field}: must be true" == e for e in errors))
            deployment[field] = True

        deployment["artifact_digest"] = "sha256:invalid"
        self.assertTrue(any("checks.deployment_preflight.artifact_digest" in e
                            for e in validate_evidence(self.document, now=self.now)))

    def test_deployment_preflight_missing_check_blocks_release(self):
        del self.document["checks"]["deployment_preflight"]
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.deployment_preflight: missing required check" == e for e in errors))

    def test_migration_preflight_and_compatibility_are_mandatory(self):
        self.document["checks"]["migration"]["preflight_passed"] = False
        self.document["checks"]["migration"]["compatibility_window_confirmed"] = False
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("checks.migration.preflight_passed: must be true", errors)
        self.assertIn("checks.migration.compatibility_window_confirmed: must be true", errors)

    def test_rollback_drill_and_strategy_are_mandatory(self):
        self.document["checks"]["rollback"]["drill_passed"] = False
        self.document["checks"]["rollback"]["strategy"] = "unsafe_down_migration"
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("checks.rollback.drill_passed: must be true", errors)
        self.assertIn("checks.rollback.strategy: unsupported rollback strategy", errors)

    def test_blockers_and_no_go_decision_prevent_release(self):
        self.document["blockers"] = ["production smoke not run"]
        self.document["release_decision"] = "no_go"
        errors = validate_evidence(self.document, now=self.now)
        self.assertIn("blockers: must be empty before release", errors)
        self.assertIn("release_decision: must be go; otherwise release is blocked", errors)

    def test_release_owner_and_approver_must_be_independent(self):
        self.document["release_approver"] = self.document["release_owner"]
        self.assertIn("release_approver: must be independent from release_owner",
                      validate_evidence(self.document, now=self.now))

    def test_invalid_document_shape_is_rejected(self):
        self.assertEqual(validate_evidence([], now=self.now),
                         ["evidence document must be a JSON object"])


if __name__ == "__main__":
    unittest.main()

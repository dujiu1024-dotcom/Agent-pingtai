from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.release_gate import REQUIRED_CHECKS, validate_evidence


class ReleaseGateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
        verified = self.now.isoformat().replace("+00:00", "Z")
        self.document = {
            "schema_version": 1,
            "release_id": "release-2026-11-16.1",
            "environment": "production",
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

    def test_missing_check_fails_closed(self):
        del self.document["checks"]["fault_injection"]
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.fault_injection" in error for error in errors))

    def test_failed_check_and_missing_evidence_are_blocking(self):
        check = self.document["checks"]["security"]
        check["status"] = "failed"
        check["evidence_ref"] = ""
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("checks.security.status" in error for error in errors))
        self.assertTrue(any("checks.security.evidence_ref" in error for error in errors))

    def test_release_candidate_commit_must_match(self):
        errors = validate_evidence(
            self.document, expected_commit_sha="b" * 40, now=self.now
        )
        self.assertIn(
            "commit_sha: evidence does not match the release candidate", errors
        )

    def test_stale_and_non_utc_timestamps_are_rejected(self):
        old = self.now - timedelta(days=8)
        self.document["generated_at_utc"] = old.isoformat().replace("+00:00", "Z")
        self.document["checks"]["e2e"]["verified_at_utc"] = "2026-11-16T08:00:00+02:00"
        errors = validate_evidence(self.document, now=self.now)
        self.assertTrue(any("evidence document is stale" in error for error in errors))
        self.assertTrue(any("must be UTC" in error for error in errors))

    def test_invalid_document_shape_is_rejected(self):
        self.assertEqual(
            validate_evidence([], now=self.now),
            ["evidence document must be a JSON object"],
        )


if __name__ == "__main__":
    unittest.main()

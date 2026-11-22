"""Tests for bounded offline authorization and license grace behavior."""
import unittest

from agent_platform.resilience import (
    CachedAuthorization,
    LicenseState,
    OfflineAction,
    OfflineDecision,
    OfflinePolicy,
    OfflinePolicyEvaluator,
)


class OfflinePolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000
        self.events = []
        self.evaluator = OfflinePolicyEvaluator(
            OfflinePolicy(
                authorization_cache_ttl_seconds=600,
                revocation_max_delay_seconds=900,
                license_grace_seconds=3600,
            ),
            clock=lambda: self.now,
            audit_writer=self.events.append,
        )
        self.authorization = CachedAuthorization(
            subject_id="user-1",
            resource_id="agent-1",
            action=OfflineAction.READ,
            granted_at=9_000,
            expires_at=20_000,
            policy_version="policy-v7",
            snapshot_verified=True,
            last_revocation_sync_at=9_900,
        )
        self.license = LicenseState(
            expires_at=20_000,
            license_id="license-1",
            snapshot_verified=True,
        )

    def evaluate(self, action=OfflineAction.READ, **kwargs):
        authorization = kwargs.pop("authorization", self.authorization)
        license_state = kwargs.pop("license_state", self.license)
        return self.evaluator.evaluate(
            authorization, license_state,
            subject_id=kwargs.pop("subject_id", "user-1"),
            resource_id=kwargs.pop("resource_id", "agent-1"),
            action=action,
            local_audit_available=kwargs.pop("local_audit_available", True),
        )

    def test_fresh_cached_read_allowed_and_audited(self):
        result = self.evaluate()
        self.assertEqual(result.decision, OfflineDecision.ALLOW)
        self.assertEqual(self.events[-1]["policy_version"], "policy-v7")
        self.assertTrue(self.events[-1]["offline"])

    def test_cache_ttl_is_measured_from_last_policy_sync(self):
        self.now = 10_501
        result = self.evaluate()
        self.assertEqual(result.decision, OfflineDecision.DENY)
        self.assertEqual(result.reason, "authorization_cache_expired")

    def test_revocation_delay_has_hard_upper_bound(self):
        self.now = 10_201
        result = self.evaluate()
        self.assertEqual(result.decision, OfflineDecision.ALLOW)
        self.now = 10_201
        evaluator = OfflinePolicyEvaluator(
            OfflinePolicy(3600, 900, 3600), clock=lambda: self.now,
            audit_writer=self.events.append,
        )
        self.authorization = CachedAuthorization(
            **{**self.authorization.__dict__, "last_revocation_sync_at": 9_000}
        )
        result = evaluator.evaluate(
            self.authorization, self.license, subject_id="user-1",
            resource_id="agent-1", action=OfflineAction.READ,
            local_audit_available=True,
        )
        self.assertEqual(result.decision, OfflineDecision.DENY)
        self.assertEqual(result.reason, "revocation_sync_stale")

    def test_expired_grant_is_denied_even_when_cache_is_fresh(self):
        auth = CachedAuthorization(
            **{**self.authorization.__dict__, "expires_at": 9_999,
               "last_revocation_sync_at": 9_990}
        )
        result = self.evaluate(authorization=auth)
        self.assertEqual(result.reason, "authorization_expired")

    def test_scope_mismatch_never_reuses_another_subject_grant(self):
        result = self.evaluate(subject_id="other-user")
        self.assertEqual(result.reason, "cached_authorization_scope_mismatch")

    def test_unverified_authorization_or_license_is_denied(self):
        auth = CachedAuthorization(
            **{**self.authorization.__dict__, "snapshot_verified": False}
        )
        self.assertEqual(
            self.evaluate(authorization=auth).reason,
            "authorization_snapshot_unverified",
        )
        license_state = LicenseState(20_000, "license-1", False)
        self.assertEqual(
            self.evaluate(license_state=license_state).reason,
            "license_snapshot_missing_or_unverified",
        )

    def test_license_grace_allows_only_read_and_resumable_internal_work(self):
        self.license = LicenseState(9_900, "license-1", True)
        self.now = 10_100
        self.assertEqual(self.evaluate().decision, OfflineDecision.DEGRADED)
        self.authorization = CachedAuthorization(
            **{
                **self.authorization.__dict__,
                "action": OfflineAction.RESUMABLE_INTERNAL,
            }
        )
        self.assertEqual(
            self.evaluate(action=OfflineAction.RESUMABLE_INTERNAL).decision,
            OfflineDecision.DEGRADED,
        )
        for action in (
            OfflineAction.EXTERNAL_SIDE_EFFECT,
            OfflineAction.ADMINISTRATION,
            OfflineAction.SECURITY_SENSITIVE,
        ):
            self.authorization = CachedAuthorization(
                **{**self.authorization.__dict__, "action": action}
            )
            self.assertEqual(
                self.evaluate(action=action).reason,
                "license_grace_restricts_action",
            )

    def test_license_grace_expiry_is_a_hard_deny(self):
        self.license = LicenseState(9_000, "license-1", True)
        self.now = 13_001
        self.authorization = CachedAuthorization(
            **{**self.authorization.__dict__, "last_revocation_sync_at": 12_900}
        )
        # Revocation freshness expires first under this policy; use a wider bound.
        evaluator = OfflinePolicyEvaluator(
            OfflinePolicy(600, 600, 3600), clock=lambda: self.now,
            audit_writer=self.events.append,
        )
        result = evaluator.evaluate(
            self.authorization, self.license, subject_id="user-1",
            resource_id="agent-1", action=OfflineAction.READ,
            local_audit_available=True,
        )
        self.assertEqual(result.reason, "license_grace_expired")

    def test_sensitive_actions_pause_even_with_valid_license(self):
        for action in (
            OfflineAction.EXTERNAL_SIDE_EFFECT,
            OfflineAction.ADMINISTRATION,
            OfflineAction.SECURITY_SENSITIVE,
        ):
            auth = CachedAuthorization(
                **{**self.authorization.__dict__, "action": action}
            )
            result = self.evaluator.evaluate(
                auth, self.license, subject_id="user-1", resource_id="agent-1",
                action=action, local_audit_available=True,
            )
            self.assertEqual(result.decision, OfflineDecision.PAUSE)

    def test_local_audit_is_mandatory_and_writer_failure_fails_closed(self):
        result = self.evaluate(local_audit_available=False)
        self.assertEqual(result.reason, "local_audit_unavailable")
        failing = OfflinePolicyEvaluator(
            OfflinePolicy(), clock=lambda: self.now,
            audit_writer=lambda _event: (_ for _ in ()).throw(OSError("disk full")),
        )
        result = failing.evaluate(
            self.authorization, self.license, subject_id="user-1",
            resource_id="agent-1", action=OfflineAction.READ,
            local_audit_available=True,
        )
        self.assertEqual(result.reason, "local_audit_write_failed")

    def test_future_clock_is_denied(self):
        auth = CachedAuthorization(
            **{**self.authorization.__dict__, "last_revocation_sync_at": 10_001}
        )
        self.assertEqual(
            self.evaluate(authorization=auth).reason, "clock_before_policy_sync"
        )


if __name__ == "__main__":
    unittest.main()

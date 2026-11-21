"""Fail-closed offline authorization and license policy for hybrid deployments.

This module evaluates a locally cached, signed policy snapshot. It deliberately
does not fetch remote state: synchronization and signature verification belong
to the host identity/control-plane adapters. Callers must provide a durable
local audit writer and a trusted clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class OfflineAction(str, Enum):
    READ = "read"
    RESUMABLE_INTERNAL = "resumable_internal"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    ADMINISTRATION = "administration"
    SECURITY_SENSITIVE = "security_sensitive"


class OfflineDecision(str, Enum):
    ALLOW = "allow"
    DEGRADED = "degraded"
    PAUSE = "pause"
    DENY = "deny"


@dataclass(frozen=True)
class OfflinePolicy:
    """Hard upper bounds; zero disables the corresponding offline allowance."""

    authorization_cache_ttl_seconds: int = 3600
    revocation_max_delay_seconds: int = 900
    license_grace_seconds: int = 72 * 3600
    require_local_audit: bool = True

    def __post_init__(self) -> None:
        if min(
            self.authorization_cache_ttl_seconds,
            self.revocation_max_delay_seconds,
            self.license_grace_seconds,
        ) < 0:
            raise ValueError("offline policy durations must be non-negative")


@dataclass(frozen=True)
class CachedAuthorization:
    subject_id: str
    resource_id: str
    action: OfflineAction
    granted_at: int
    expires_at: int
    policy_version: str
    snapshot_verified: bool
    last_revocation_sync_at: int

    def __post_init__(self) -> None:
        if not all((self.subject_id, self.resource_id, self.policy_version)):
            raise ValueError("authorization identity and policy version are required")
        if self.expires_at < self.granted_at:
            raise ValueError("authorization expiry precedes grant")


@dataclass(frozen=True)
class LicenseState:
    expires_at: int
    license_id: str
    snapshot_verified: bool


@dataclass(frozen=True)
class OfflineResult:
    decision: OfflineDecision
    reason: str
    revocation_delay_seconds: int
    license_grace_remaining_seconds: int


class OfflinePolicyEvaluator:
    """Evaluates offline requests without turning stale cache into a new grant."""

    def __init__(
        self,
        policy: OfflinePolicy,
        *,
        clock: Callable[[], int],
        audit_writer: Callable[[dict[str, object]], None],
    ) -> None:
        self.policy = policy
        self._clock = clock
        self._audit_writer = audit_writer

    def evaluate(
        self,
        authorization: CachedAuthorization | None,
        license_state: LicenseState | None,
        *,
        subject_id: str,
        resource_id: str,
        action: OfflineAction,
        local_audit_available: bool,
    ) -> OfflineResult:
        now = self._clock()
        delay = 0
        grace_remaining = 0

        def finish(decision: OfflineDecision, reason: str) -> OfflineResult:
            event = {
                "subject_id": subject_id,
                "resource_id": resource_id,
                "action": action.value,
                "decision": decision.value,
                "reason": reason,
                "at": now,
                "policy_version": authorization.policy_version if authorization else None,
                "revocation_delay_seconds": delay,
                "license_grace_remaining_seconds": grace_remaining,
                "offline": True,
            }
            if self.policy.require_local_audit and not local_audit_available:
                return OfflineResult(OfflineDecision.DENY, "local_audit_unavailable", delay, grace_remaining)
            try:
                self._audit_writer(event)
            except Exception:
                return OfflineResult(OfflineDecision.DENY, "local_audit_write_failed", delay, grace_remaining)
            return OfflineResult(decision, reason, delay, grace_remaining)

        if self.policy.require_local_audit and not local_audit_available:
            return OfflineResult(OfflineDecision.DENY, "local_audit_unavailable", 0, 0)
        if authorization is None:
            return finish(OfflineDecision.DENY, "no_cached_authorization")
        if not authorization.snapshot_verified:
            return finish(OfflineDecision.DENY, "authorization_snapshot_unverified")
        if (authorization.subject_id, authorization.resource_id, authorization.action) != (
            subject_id, resource_id, action
        ):
            return finish(OfflineDecision.DENY, "cached_authorization_scope_mismatch")
        if now < authorization.granted_at:
            return finish(OfflineDecision.DENY, "clock_before_authorization_issue")
        cache_age = now - authorization.granted_at
        if cache_age > self.policy.authorization_cache_ttl_seconds:
            return finish(OfflineDecision.DENY, "authorization_cache_expired")
        if now > authorization.expires_at:
            return finish(OfflineDecision.DENY, "authorization_expired")
        delay = max(0, now - authorization.last_revocation_sync_at)
        if delay > self.policy.revocation_max_delay_seconds:
            return finish(OfflineDecision.DENY, "revocation_sync_stale")

        if license_state is None or not license_state.snapshot_verified:
            return finish(OfflineDecision.DENY, "license_snapshot_missing_or_unverified")
        if not license_state.license_id:
            return finish(OfflineDecision.DENY, "license_identity_missing")
        if now > license_state.expires_at:
            grace_remaining = max(0, license_state.expires_at + self.policy.license_grace_seconds - now)
            if grace_remaining == 0:
                return finish(OfflineDecision.DENY, "license_grace_expired")
            if action not in (OfflineAction.READ, OfflineAction.RESUMABLE_INTERNAL):
                return finish(OfflineDecision.DENY, "license_grace_restricts_action")
            return finish(OfflineDecision.DEGRADED, "license_grace_read_or_resume_only")

        if action in (OfflineAction.EXTERNAL_SIDE_EFFECT, OfflineAction.ADMINISTRATION,
                      OfflineAction.SECURITY_SENSITIVE):
            return finish(OfflineDecision.PAUSE, "control_plane_required_for_sensitive_action")
        return finish(OfflineDecision.ALLOW, "cached_grant_within_offline_bounds")

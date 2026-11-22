"""Knowledge asset quality, freshness and inherited-permission governance.

This module stores governance decisions and propagation receipts only. The
host injects durable persistence and its existing cache/index revocation
adapter; no parallel RAG, ACL or graph implementation is introduced.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4


class QualityDimension(str, Enum):
    COMPLETENESS = "completeness"
    ACCURACY = "accuracy"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    SOURCE_TRUST = "source_trust"
    SENSITIVE_DATA = "sensitive_data"


class FreshnessState(str, Enum):
    CURRENT = "current"
    REVIEW_DUE = "review_due"
    EXPIRED = "expired"
    SUSPENDED = "suspended"


class PropagationState(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class QualityAssessment:
    tenant_id: str
    asset_id: str
    assessor_id: str
    scores: Mapping[QualityDimension, float]
    evidence: Mapping[str, str]
    assessment_id: str = field(default_factory=lambda: str(uuid4()))
    assessed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "asset_id", "assessor_id", "assessment_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if set(self.scores) != set(QualityDimension):
            raise ValueError("all six quality dimensions must be scored")
        for dimension, score in self.scores.items():
            if not isinstance(dimension, QualityDimension) or isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
                raise ValueError("quality scores must be numeric values from 0 to 100")
        if not isinstance(self.evidence, Mapping) or any(
            dimension.value not in self.evidence or not str(self.evidence[dimension.value]).strip()
            for dimension in QualityDimension
        ):
            raise ValueError("each quality dimension requires explainable evidence")
        if self.assessed_at.tzinfo is None:
            raise ValueError("assessed_at must be timezone-aware")

    @property
    def score(self) -> float:
        # Equal weights are explicit and stable; callers can explain every component.
        return round(sum(float(self.scores[d]) for d in QualityDimension) / len(QualityDimension), 2)

    @property
    def findings(self) -> tuple[str, ...]:
        return tuple(d.value for d in QualityDimension if float(self.scores[d]) < 70)


@dataclass(frozen=True)
class FreshnessPolicy:
    tenant_id: str
    asset_id: str
    valid_from: datetime
    expires_at: datetime
    review_interval_days: int
    last_reviewed_at: datetime
    policy_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        for name in ("tenant_id", "asset_id", "policy_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        values = (self.valid_from, self.expires_at, self.last_reviewed_at)
        if any(v.tzinfo is None for v in values) or self.expires_at <= self.valid_from:
            raise ValueError("freshness timestamps must be timezone-aware and expiry after valid_from")
        if type(self.review_interval_days) is not int or not 1 <= self.review_interval_days <= 3650:
            raise ValueError("review_interval_days must be between 1 and 3650")

    def state_at(self, now: datetime) -> FreshnessState:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if now < self.valid_from:
            return FreshnessState.SUSPENDED
        if now >= self.expires_at:
            return FreshnessState.EXPIRED
        if now >= self.last_reviewed_at.replace(tzinfo=timezone.utc) if self.last_reviewed_at.tzinfo is None else now >= self.last_reviewed_at:
            due = self.last_reviewed_at.timestamp() + self.review_interval_days * 86400
            if now.timestamp() >= due:
                return FreshnessState.REVIEW_DUE
        return FreshnessState.CURRENT


@dataclass(frozen=True)
class SourcePermission:
    tenant_id: str
    asset_id: str
    source_system: str
    source_grant_id: str
    inherited_principals: frozenset[str]
    active: bool = True
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("tenant_id", "asset_id", "source_system", "source_grant_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.revoked_at is not None and self.revoked_at.tzinfo is None:
            raise ValueError("revoked_at must be timezone-aware")
        if not self.active and self.revoked_at is None:
            raise ValueError("revoked permission requires revoked_at")


@dataclass(frozen=True)
class RevocationReceipt:
    tenant_id: str
    asset_id: str
    target: str
    state: PropagationState
    requested_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    receipt_id: str = field(default_factory=lambda: str(uuid4()))


class KnowledgeGovernanceRepository(Protocol):
    async def save_quality(self, assessment: QualityAssessment) -> None: ...
    async def get_quality(self, tenant_id: str, asset_id: str) -> QualityAssessment | None: ...
    async def save_freshness(self, policy: FreshnessPolicy) -> None: ...
    async def get_freshness(self, tenant_id: str, asset_id: str) -> FreshnessPolicy | None: ...
    async def save_source_permission(self, permission: SourcePermission) -> None: ...
    async def get_source_permission(self, tenant_id: str, asset_id: str) -> SourcePermission | None: ...
    async def revoke_source_permission(self, tenant_id: str, asset_id: str, at: datetime) -> SourcePermission | None: ...
    async def add_receipt(self, receipt: RevocationReceipt) -> None: ...
    async def list_receipts(self, tenant_id: str, asset_id: str) -> tuple[RevocationReceipt, ...]: ...


class InMemoryKnowledgeGovernanceRepository:
    """Deterministic test adapter; production must inject a durable repository."""
    def __init__(self) -> None:
        self._quality: dict[tuple[str, str], QualityAssessment] = {}
        self._freshness: dict[tuple[str, str], FreshnessPolicy] = {}
        self._permissions: dict[tuple[str, str], SourcePermission] = {}
        self._receipts: dict[tuple[str, str], list[RevocationReceipt]] = {}
        self._lock = asyncio.Lock()

    async def save_quality(self, assessment: QualityAssessment) -> None:
        async with self._lock:
            self._quality[(assessment.tenant_id, assessment.asset_id)] = assessment

    async def get_quality(self, tenant_id: str, asset_id: str) -> QualityAssessment | None:
        async with self._lock:
            return self._quality.get((tenant_id, asset_id))

    async def save_freshness(self, policy: FreshnessPolicy) -> None:
        async with self._lock:
            self._freshness[(policy.tenant_id, policy.asset_id)] = policy

    async def get_freshness(self, tenant_id: str, asset_id: str) -> FreshnessPolicy | None:
        async with self._lock:
            return self._freshness.get((tenant_id, asset_id))

    async def save_source_permission(self, permission: SourcePermission) -> None:
        async with self._lock:
            self._permissions[(permission.tenant_id, permission.asset_id)] = permission

    async def get_source_permission(self, tenant_id: str, asset_id: str) -> SourcePermission | None:
        async with self._lock:
            return self._permissions.get((tenant_id, asset_id))

    async def revoke_source_permission(self, tenant_id: str, asset_id: str, at: datetime) -> SourcePermission | None:
        async with self._lock:
            key = (tenant_id, asset_id)
            current = self._permissions.get(key)
            if current is None:
                return None
            revoked = SourcePermission(current.tenant_id, current.asset_id, current.source_system,
                current.source_grant_id, frozenset(), False, at)
            self._permissions[key] = revoked
            return revoked

    async def add_receipt(self, receipt: RevocationReceipt) -> None:
        async with self._lock:
            self._receipts.setdefault((receipt.tenant_id, receipt.asset_id), []).append(receipt)

    async def list_receipts(self, tenant_id: str, asset_id: str) -> tuple[RevocationReceipt, ...]:
        async with self._lock:
            return tuple(self._receipts.get((tenant_id, asset_id), ()))


class PermissionPropagationAdapter(Protocol):
    async def revoke_asset_access(self, *, tenant_id: str, asset_id: str, targets: tuple[str, ...]) -> Mapping[str, bool]: ...


class KnowledgeAssetGovernanceService:
    def __init__(self, *, assets: Any, repository: KnowledgeGovernanceRepository,
                 propagation: PermissionPropagationAdapter, targets: tuple[str, ...] = (
                     "knowledge_acl", "retrieval_cache", "vector_index", "downstream_copies",
                 )) -> None:
        if not targets or len(set(targets)) != len(targets):
            raise ValueError("revocation targets must be non-empty and unique")
        self.assets, self.repository, self.propagation, self.targets = assets, repository, propagation, targets

    async def _asset(self, tenant_id: str, asset_id: str):
        asset = await self.assets.get_asset(tenant_id, asset_id)
        if asset is None:
            raise LookupError("asset not found")
        return asset

    async def assess_quality(self, assessment: QualityAssessment) -> QualityAssessment:
        await self._asset(assessment.tenant_id, assessment.asset_id)
        await self.repository.save_quality(assessment)
        return assessment

    async def set_freshness(self, policy: FreshnessPolicy) -> FreshnessPolicy:
        await self._asset(policy.tenant_id, policy.asset_id)
        await self.repository.save_freshness(policy)
        return policy

    async def freshness_state(self, tenant_id: str, asset_id: str, now: datetime | None = None) -> FreshnessState:
        await self._asset(tenant_id, asset_id)
        policy = await self.repository.get_freshness(tenant_id, asset_id)
        if policy is None:
            raise LookupError("freshness policy not found")
        state = policy.state_at(now or datetime.now(timezone.utc))
        if state is FreshnessState.EXPIRED:
            # Expiry is fail-closed: callers must suppress retrieval until reviewed.
            return FreshnessState.EXPIRED
        return state

    async def review(self, tenant_id: str, asset_id: str, reviewed_at: datetime) -> FreshnessPolicy:
        policy = await self.repository.get_freshness(tenant_id, asset_id)
        if policy is None or policy.tenant_id != tenant_id:
            raise LookupError("freshness policy not found")
        if reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        updated = FreshnessPolicy(policy.tenant_id, policy.asset_id, policy.valid_from,
            policy.expires_at, policy.review_interval_days, reviewed_at, policy.policy_id)
        await self.repository.save_freshness(updated)
        return updated

    async def set_source_permission(self, permission: SourcePermission) -> None:
        await self._asset(permission.tenant_id, permission.asset_id)
        await self.repository.save_source_permission(permission)

    async def revoke_source_permission(self, tenant_id: str, asset_id: str,
                                       at: datetime | None = None) -> tuple[RevocationReceipt, ...]:
        await self._asset(tenant_id, asset_id)
        requested_at = at or datetime.now(timezone.utc)
        if requested_at.tzinfo is None:
            raise ValueError("revocation time must be timezone-aware")
        permission = await self.repository.revoke_source_permission(tenant_id, asset_id, requested_at)
        if permission is None:
            raise LookupError("source permission not found")
        receipts = []
        try:
            results = await self.propagation.revoke_asset_access(
                tenant_id=tenant_id, asset_id=asset_id, targets=self.targets,
            )
        except Exception as exc:
            results = {target: False for target in self.targets}
            error = str(exc)[:500]
        else:
            error = None
        for target in self.targets:
            success = results.get(target) is True
            receipt = RevocationReceipt(
                tenant_id, asset_id, target,
                PropagationState.SUCCEEDED if success else PropagationState.FAILED,
                requested_at, datetime.now(timezone.utc) if success else None,
                None if success else (error or "target did not confirm revocation"),
            )
            await self.repository.add_receipt(receipt)
            receipts.append(receipt)
        return tuple(receipts)
